"""Helper to register Unitree custom message types with rosbags typestore.

Searches a list of standard locations for Unitree .msg schema files and
registers every found type with a rosbags typestore.

Search order (first match wins per type):
  1. Colon-separated paths in $UNITREE_MSG_ROOTS (highest priority; useful
     for unusual layouts or pinning a specific clone).
  2. <repo>/external_dependencies/unitree_ros2/cyclonedds_ws/src/unitree
  3. <repo>/external_dependencies/unitree_ros2
  4. ~/g1_bag_tools/unitree_ros2/cyclonedds_ws/src/unitree   (legacy default)
  5. ~/unitree_ros2_ws/src
  6. ~/unitree_ros2/cyclonedds_ws/src/unitree
  7. ~/unitree_ros2
  8. /opt/ros/humble/share, /opt/ros/foxy/share  (installed package shares)

The search inspects every <root>/.../<pkg>/msg/*.msg path where <pkg>
starts with "unitree_", so both source-tree and install-tree layouts work.

Usage:
    from unitree_types import build_typestore
    typestore = build_typestore()
"""
import os
from pathlib import Path

from rosbags.typesys import Stores, get_types_from_msg, get_typestore


def _default_search_roots() -> list[Path]:
    home = Path.home()
    repo_root = Path(__file__).resolve().parents[3]
    env_roots = [
        Path(p) for p in (os.environ.get("UNITREE_MSG_ROOTS") or "").split(":")
        if p
    ]
    return env_roots + [
        # Repo-local checkout, useful when the Docker home is not persistent.
        repo_root / "external_dependencies/unitree_ros2/cyclonedds_ws/src/unitree",
        repo_root / "external_dependencies/unitree_ros2",
        # Legacy default -- kept first among non-env roots for backward compat.
        home / "g1_bag_tools/unitree_ros2/cyclonedds_ws/src/unitree",
        # Typical user workspace layouts.
        home / "unitree_ros2_ws/src",
        home / "unitree_ros2/cyclonedds_ws/src/unitree",
        home / "unitree_ros2",
        # Installed package shares.
        Path("/opt/ros/humble/share"),
        Path("/opt/ros/foxy/share"),
    ]


def _find_unitree_msgs(roots):
    """Yield (msgtype, msg_def_text) for every unitree_* .msg under any root.

    Looks for files matching `<root>/.../<pkg>/msg/<Name>.msg` where <pkg>
    starts with "unitree_".  Dedupes across roots so an earlier root wins.
    """
    seen: set[str] = set()
    for root in roots:
        if not root or not root.exists():
            continue
        for msg_path in root.rglob("*.msg"):
            # Expect <pkg>/msg/<Name>.msg
            if msg_path.parent.name != "msg":
                continue
            pkg = msg_path.parent.parent.name
            if not pkg.startswith("unitree_"):
                continue
            msgtype = f"{pkg}/msg/{msg_path.stem}"
            if msgtype in seen:
                continue
            seen.add(msgtype)
            yield msgtype, msg_path.read_text(encoding="utf-8")


def build_typestore(
    schemas_root: Path | None = None,
    store: Stores = Stores.ROS2_FOXY,
):
    """Return a typestore with Unitree types registered on top of the base
    distro types.

    If `schemas_root` is given, it's used as the sole search location
    (back-compat with the previous single-path signature).  Otherwise the
    default multi-root search above runs.
    """
    typestore = get_typestore(store)
    if schemas_root is not None:
        roots = [Path(schemas_root)]
    else:
        roots = _default_search_roots()
    add_types = {}
    for msgtype, msg_def in _find_unitree_msgs(roots):
        add_types.update(get_types_from_msg(msg_def, msgtype))
    typestore.register(add_types)
    return typestore


if __name__ == "__main__":
    ts = build_typestore()
    unitree = sorted(t for t in ts.types if t.startswith("unitree_"))
    print(f"Loaded {len(unitree)} Unitree types")
    for t in unitree:
        print(f"  {t}")
    if not unitree:
        print()
        print("No Unitree types found.  Set UNITREE_MSG_ROOTS to point at a")
        print("dir containing <pkg>/msg/*.msg, e.g.:")
        print("  UNITREE_MSG_ROOTS=/path/to/unitree_ros2/src python3 unitree_types.py")
