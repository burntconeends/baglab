"""Convert a ROS 2 Foxy sqlite3 (.db3) bag to a single self-contained .mcap file.

Schemas for Unitree custom types (unitree_hg, unitree_go, unitree_api) are
loaded from the local clone of unitree_ros2 and embedded into the resulting
mcap, so downstream consumers don't need any ROS install.

rosbags writes mcap inside a rosbag2-style directory wrapper. This script
extracts the inner .mcap file and discards the wrapper, producing a single
standalone .mcap file at the output path.

Usage:
    python3 convert_db3_to_mcap.py <input_bag_dir> <output_mcap_path>

Example:
    python3 convert_db3_to_mcap.py \\
        ~/Downloads/2026-05-15-11-05-16-g1-real \\
        ~/Downloads/2026-05-15-11-05-16-g1-real.mcap
"""
import shutil
import sys
import tempfile
from pathlib import Path

from rosbags.highlevel import AnyReader
from rosbags.rosbag2 import Writer, StoragePlugin

from baglab.constants import DEFAULT_EXPECTED_TOPICS
from baglab.typestore import build_typestore


def convert(bag_path: Path, mcap_path: Path) -> None:
    typestore = build_typestore()

    if mcap_path.exists():
        print(f"ERROR: {mcap_path} already exists, refusing to overwrite", file=sys.stderr)
        sys.exit(1)

    print(f"Reading:  {bag_path}")
    print(f"Writing:  {mcap_path}")
    print()

    # rosbags Writer creates a rosbag2-style directory wrapper around the .mcap.
    # We write into a temp directory, then pull the inner file out as our final output.
    tmp_dir = Path(tempfile.mkdtemp(prefix="mcap_convert_", dir=mcap_path.parent))
    bag_wrapper = tmp_dir / "bag"  # rosbags creates this directory

    counts: dict[str, int] = {}
    try:
        with AnyReader([bag_path], default_typestore=typestore) as reader, \
             Writer(bag_wrapper, version=9, storage_plugin=StoragePlugin.MCAP) as writer:

            # Surface missing expected topics upfront so a partial bag is obvious.
            present = {c.topic for c in reader.connections}
            missing = sorted(set(DEFAULT_EXPECTED_TOPICS) - present)
            if missing:
                print(f"WARNING: bag is missing {len(missing)} expected topics: "
                      f"{', '.join(missing)}", file=sys.stderr)

            conn_map = {}
            for conn in reader.connections:
                out_conn = writer.add_connection(
                    topic=conn.topic,
                    msgtype=conn.msgtype,
                    typestore=typestore,
                )
                conn_map[conn.id] = out_conn

            for conn, timestamp, rawdata in reader.messages():
                writer.write(conn_map[conn.id], timestamp, rawdata)
                counts[conn.topic] = counts.get(conn.topic, 0) + 1

        # Extract the inner .mcap from the rosbag2 directory wrapper
        inner_mcaps = list(bag_wrapper.glob("*.mcap"))
        if len(inner_mcaps) != 1:
            raise RuntimeError(
                f"Expected exactly one .mcap inside {bag_wrapper}, found {len(inner_mcaps)}: "
                f"{[p.name for p in inner_mcaps]}"
            )
        inner_mcap = inner_mcaps[0]
        shutil.move(str(inner_mcap), str(mcap_path))

    finally:
        # Always clean up the temp directory (including metadata.yaml from rosbags)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Report results
    print("Conversion complete. Topic counts:")
    for topic in sorted(counts):
        print(f"  {topic}: {counts[topic]}")

    size_gb = mcap_path.stat().st_size / 1e9
    print(f"\nOutput: {mcap_path}")
    print(f"Size:   {size_gb:.2f} GB")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    bag = Path(sys.argv[1]).expanduser().resolve()
    out = Path(sys.argv[2]).expanduser().resolve()
    if not bag.is_dir():
        print(f"ERROR: {bag} is not a directory", file=sys.stderr)
        sys.exit(1)
    convert(bag, out)
