"""Serve a bag in the Rerun web viewer.

Usage:
    python3 bag_to_rerun_web.py <bag.mcap> --port 9876
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
BAG_TO_RERUN = SCRIPT_DIR / "bag_to_rerun.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bag", type=Path, help="Path to .mcap file or bag directory")
    parser.add_argument("--subsample", type=int, default=20)
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--port", type=int, default=9876)
    args = parser.parse_args()

    cmd = [
        sys.executable,
        "-u",  # unbuffered so the viewer URL prints immediately
        str(BAG_TO_RERUN),
        str(args.bag),
        "--subsample",
        str(args.subsample),
        "--serve-web",
        "--web-port",
        str(args.port),
    ]
    if args.no_images:
        cmd.append("--no-images")

    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
