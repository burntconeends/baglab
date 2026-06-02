"""Build a rosbags typestore augmented with Unitree custom messages.

Searches a list of standard locations for Unitree .msg schema files and
registers every found type. In the baglab Docker image, the canonical root is
`/opt/unitree_ros2/...` via the `UNITREE_MSG_ROOTS` env var.

Search order (first match wins per type):
  1. Colon-separated paths in `$UNITREE_MSG_ROOTS` (highest priority).
  2. ~/g1_bag_tools/unitree_ros2/cyclonedds_ws/src/unitree   (legacy default)
  3. ~/unitree_ros2_ws/src
  4. ~/unitree_ros2/cyclonedds_ws/src/unitree
  5. ~/unitree_ros2
  6. /opt/ros/humble/share, /opt/ros/foxy/share

Usage:
    from baglab.typestore import build_typestore
    typestore = build_typestore()
"""
from __future__ import annotations

import os
from pathlib import Path

from rosbags.typesys import Stores, get_types_from_msg, get_typestore


def _default_search_roots() -> list[Path]:
    home = Path.home()
    env_roots = [
        Path(p) for p in (os.environ.get("UNITREE_MSG_ROOTS") or "").split(":")
        if p
    ]
    return env_roots + [
        # Legacy default — kept first among non-env roots for backward compat.
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
    """Return a typestore with Unitree types registered on top of the base distro.

    If `schemas_root` is given, it's used as the sole search location. Otherwise
    the default multi-root search above runs.
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
        print("No Unitree types found. Set UNITREE_MSG_ROOTS to point at a")
        print("dir containing <pkg>/msg/*.msg, e.g.:")
        print("  UNITREE_MSG_ROOTS=/path/to/unitree_ros2/src "
              "python -m baglab.typestore")
