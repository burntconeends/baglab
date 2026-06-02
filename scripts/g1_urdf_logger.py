"""Log an animated G1 robot to Rerun using rerun's native URDF support.

rerun >= 0.32 parses the URDF, loads its meshes, and computes joint transforms
natively (rr.urdf), so no external URDF/FK library (yourdfpy/trimesh) is needed.

Flow:
  1. UrdfTree.from_file_path(urdf) parses the model + builds joint handles.
  2. log_model() logs geometry + static transforms once (tree.log_urdf_to_recording).
  3. update(...) logs each driven joint's Transform3D from its measured angle;
     rerun places the geometry via the transform's parent/child frame names.

Joint mapping: /lowstate gives 29 body joints in G1 order; the URDF names them
with a "_joint" suffix. /dex3/{left,right}/state give 7 hand joints each in
Unitree Dex3-1 SDK order (thumb 0/1/2, middle 0/1, index 0/1), which matches
the URDF declaration order.

Usage:
    from g1_urdf_logger import G1UrdfLogger, find_g1_urdf
    logger = G1UrdfLogger(find_g1_urdf())
    logger.log_model()
    logger.update(body_q=q29, left_hand_q=lh7, right_hand_q=rh7)
"""
from __future__ import annotations

import os
from pathlib import Path

import rerun as rr
import rerun.blueprint as rrb

# 29 body joints in /lowstate order; URDF joint = name + "_joint".
G1_BODY_JOINT_NAMES = [
    "left_hip_pitch", "left_hip_roll", "left_hip_yaw",
    "left_knee", "left_ankle_pitch", "left_ankle_roll",
    "right_hip_pitch", "right_hip_roll", "right_hip_yaw",
    "right_knee", "right_ankle_pitch", "right_ankle_roll",
    "waist_yaw", "waist_roll", "waist_pitch",
    "left_shoulder_pitch", "left_shoulder_roll", "left_shoulder_yaw",
    "left_elbow", "left_wrist_roll", "left_wrist_pitch", "left_wrist_yaw",
    "right_shoulder_pitch", "right_shoulder_roll", "right_shoulder_yaw",
    "right_elbow", "right_wrist_roll", "right_wrist_pitch", "right_wrist_yaw",
]

# Dex3-1 hand: 7 joints per side, SDK motor order = URDF declaration order
# (thumb 0/1/2, middle 0/1, index 0/1).
_HAND_SUFFIXES = [
    "thumb_0", "thumb_1", "thumb_2",
    "middle_0", "middle_1",
    "index_0", "index_1",
]
LEFT_HAND_JOINT_NAMES = [f"left_hand_{s}_joint" for s in _HAND_SUFFIXES]
RIGHT_HAND_JOINT_NAMES = [f"right_hand_{s}_joint" for s in _HAND_SUFFIXES]

_DRIVABLE_TYPES = ("revolute", "continuous", "prismatic")

_ASSET_URDF = Path(__file__).resolve().parents[1] / "assets" / "g1" / "g1_29dof_with_hand.urdf"
# Fallback to the source checkout in the GR00T repo (useful when run on the host).
_GR00T_URDF = (
    Path.home()
    / "GR00T/GR00T-WholeBodyControl/decoupled_wbc/control/robot_model"
    / "model_data/g1/g1_29dof_with_hand.urdf"
)


def find_g1_urdf(explicit: str | os.PathLike | None = None) -> Path | None:
    """Locate the G1 URDF. Priority: explicit arg, $G1_URDF, vendored asset,
    GR00T source checkout. Returns None if none exist."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("G1_URDF"):
        candidates.append(Path(os.environ["G1_URDF"]))
    candidates += [_ASSET_URDF, _GR00T_URDF]
    for c in candidates:
        if c and c.expanduser().exists():
            return c.expanduser()
    return None


class G1UrdfLogger:
    def __init__(self, urdf_path: str | os.PathLike,
                 joint_entity_path: str = "robot_joints", clamp: bool = False):
        self.urdf_path = Path(urdf_path)
        self.joint_entity_path = joint_entity_path
        self.clamp = clamp
        self.tree = rr.urdf.UrdfTree.from_file_path(str(self.urdf_path))
        self._body = [self.tree.get_joint_by_name(f"{n}_joint")
                      for n in G1_BODY_JOINT_NAMES]
        self._left = [self.tree.get_joint_by_name(n) for n in LEFT_HAND_JOINT_NAMES]
        self._right = [self.tree.get_joint_by_name(n) for n in RIGHT_HAND_JOINT_NAMES]

    def log_model(self) -> None:
        """Log the full model (geometry + static transforms) once."""
        self.tree.log_urdf_to_recording()

    def hide_collision(self) -> None:
        """Send a blueprint that hides the URDF's collision geometries.

        The URDF importer logs both visual and collision meshes; collision
        meshes clutter the view. The override path is `<robot_name>/collision_geometries`.
        """
        path = f"{self.tree.name}/collision_geometries"
        blueprint = rrb.Blueprint(
            rrb.Spatial3DView(
                name="3D view",
                overrides={path: rrb.EntityBehavior(visible=False)},
            )
        )
        rr.send_blueprint(blueprint)

    def update(self, body_q=None, left_hand_q=None, right_hand_q=None) -> None:
        self._log_joints(self._body, body_q)
        self._log_joints(self._left, left_hand_q)
        self._log_joints(self._right, right_hand_q)

    def _log_joints(self, joints, values) -> None:
        if values is None:
            return
        for joint, value in zip(joints, values):
            if joint is None or joint.joint_type not in _DRIVABLE_TYPES:
                continue
            rr.log(self.joint_entity_path,
                   joint.compute_transform(float(value), clamp=self.clamp))
