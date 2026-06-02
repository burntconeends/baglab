"""Shared constants for the G1 embodiment used across bronze checks."""

# G1 body joint layout (29 joints, /lowstate motor_state order).
# Matches G1_JOINT_NAMES in scripts/bag_to_rerun.py.
G1_JOINT_NAMES = [
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
N_BODY_JOINTS = len(G1_JOINT_NAMES)  # 29

# Joint-group index ranges into the 29-vector.
LEG_JOINTS = list(range(0, 12))        # both legs (hip/knee/ankle)
WAIST_JOINTS = list(range(12, 15))     # waist yaw/roll/pitch
LEFT_ARM_JOINTS = list(range(15, 22))  # left shoulder/elbow/wrist
RIGHT_ARM_JOINTS = list(range(22, 29))  # right shoulder/elbow/wrist
ARM_JOINTS = LEFT_ARM_JOINTS + RIGHT_ARM_JOINTS  # 14 upper-body joints

# Topics validation expects in a complete G1 real-robot teleop bag.
# Source: GR00T CLAUDE.md "Currently recorded rosbag topics".
DEFAULT_EXPECTED_TOPICS = [
    "/lowstate",
    "/lowcmd",
    "/dex3/left/state",
    "/dex3/right/state",
    "/dex3/left/cmd",
    "/dex3/right/cmd",
    "/zed/image_raw/compressed",
    "/utlidar/imu_livox_mid360",
    "/G1Env/env_state_act",
    "/ControlPolicy/lower_body_policy_status",
    "/ControlPolicy/joint_safety_status",
]
