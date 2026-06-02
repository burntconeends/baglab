"""baglab command-line interface.

    baglab check <bag> [options]      run bronze-tier quality checks

`<bag>` is a standalone .mcap file or a rosbag2 directory (.db3). Emits a
machine-readable JSON report (--out) and/or a printed summary table.

Exit code is 0 if all checks pass, 1 otherwise (useful in CI / batch screening).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from baglab import __version__
from baglab.config import CheckConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="baglab",
        description="Data review pipeline for G1 robot rosbags (bronze tier).",
    )
    parser.add_argument("--version", action="version", version=f"baglab {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser(
        "check", help="run bronze-tier quality checks on a bag",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    chk.add_argument("bag", type=Path, help="path to .mcap file or rosbag2 (.db3) directory")
    chk.add_argument("--config", type=Path, default=None,
                     help="JSON config file with threshold overrides")
    chk.add_argument("--out", type=Path, default=None,
                     help="write JSON report to this path")
    chk.add_argument("--quiet", action="store_true",
                     help="suppress the printed summary table")
    chk.add_argument("--json", action="store_true",
                     help="print the JSON report to stdout (instead of the table)")

    # Threshold overrides (None => use config/default).
    g1 = chk.add_argument_group("data integrity (priority 1)")
    g1.add_argument("--expected-topic", action="append", dest="expected_topics",
                    metavar="TOPIC",
                    help="expected topic (repeatable; replaces the default list)")
    g1.add_argument("--gap-factor", type=float, default=None,
                    help="flag gaps exceeding N x median interval (default 5.0)")
    g1.add_argument("--gap-floor-s", type=float, default=None,
                    help="absolute gap floor in seconds (default 0.1)")
    g1.add_argument("--min-rate-hz", type=float, default=None,
                    help="flag topics slower than this rate (default 0 = off)")

    g4 = chk.add_argument_group("trajectory smoothness (priority 4)")
    g4.add_argument("--vel-threshold", type=float, default=None,
                    help="velocity spike threshold rad/s (default 6.0)")
    g4.add_argument("--accel-threshold", type=float, default=None,
                    help="acceleration spike threshold rad/s^2 (default 60.0)")
    g4.add_argument("--deriv-window-s", type=float, default=None,
                    help="finite-difference window in seconds (default 0.02)")
    g4.add_argument("--spike-mad-factor", type=float, default=None,
                    help="robust MAD spike factor (default 0 = off)")
    g4.add_argument("--max-events", type=int, default=None,
                    help="cap on recorded violation events (default 200)")
    g4.add_argument("--no-smoothness", action="store_true",
                    help="skip the smoothness check")

    g5 = chk.add_argument_group("static-frame scrubbing (priority 5)")
    g5.add_argument("--static-vel-threshold", type=float, default=None,
                    help="inactivity velocity threshold rad/s (default 0.02)")
    g5.add_argument("--min-static-duration", type=float, default=None,
                    help="min inactive window to report, seconds (default 1.0)")
    g5.add_argument("--no-static", action="store_true",
                    help="skip the static-frame check")

    gr = chk.add_argument_group("reader")
    gr.add_argument("--max-lowstate-samples", type=int, default=None,
                    help="cap on /lowstate samples held in RAM (default 600_000); "
                         "exceeding triggers uniform stride decimation")
    return parser


def _resolve_config(args) -> CheckConfig:
    config = CheckConfig.from_file(args.config) if args.config else CheckConfig()
    return config.apply_overrides(
        expected_topics=args.expected_topics,
        gap_factor=args.gap_factor,
        gap_floor_s=args.gap_floor_s,
        min_rate_hz=args.min_rate_hz,
        vel_threshold=args.vel_threshold,
        accel_threshold=args.accel_threshold,
        deriv_window_s=args.deriv_window_s,
        spike_mad_factor=args.spike_mad_factor,
        max_events=args.max_events,
        static_vel_threshold=args.static_vel_threshold,
        min_static_duration=args.min_static_duration,
        max_lowstate_samples=args.max_lowstate_samples,
    )


def _cmd_check(args) -> int:
    # Imports deferred so `baglab --help` works without the heavy deps present.
    from baglab.bronze import check_integrity, check_smoothness, check_static_frames
    from baglab.reader import read_bag
    from baglab.report import (
        BagReport, SmoothnessResult, StaticResult, format_summary,
        now_iso, report_to_json,
    )

    if not args.bag.exists():
        print(f"ERROR: {args.bag} not found", file=sys.stderr)
        return 2

    config = _resolve_config(args)
    bag = read_bag(args.bag,
                   max_lowstate_samples=config.max_lowstate_samples)

    integrity = check_integrity(bag, config)

    if args.no_smoothness:
        smoothness = SmoothnessResult(
            n_samples=0, vel_threshold=config.vel_threshold,
            accel_threshold=config.accel_threshold, per_joint=[], violations=[],
            n_violations_total=0, violations_truncated=False, passed=True,
            note="skipped (--no-smoothness)",
        )
    else:
        smoothness = check_smoothness(bag, config)

    if args.no_static:
        static = StaticResult(
            static_vel_threshold=config.static_vel_threshold,
            min_static_duration=config.min_static_duration,
            static_joints=list(config.static_joints), windows=[],
            total_static_s=0.0, fraction_static=0.0, passed=True,
            note="skipped (--no-static)",
        )
    else:
        static = check_static_frames(bag, config)

    report = BagReport(
        bag_path=bag.path, bag_name=bag.name, duration_s=bag.duration_s,
        message_count=bag.message_count, start_time_ns=bag.start_ns,
        end_time_ns=bag.end_ns, generated_at=now_iso(),
        config=config.to_dict(), integrity=integrity,
        smoothness=smoothness, static_frames=static,
        lowstate_decimated=(bag.lowstate.decimated if bag.lowstate else False),
        lowstate_raw_count=(bag.lowstate.raw_count if bag.lowstate else 0),
    )

    json_text = report_to_json(report)
    if args.out:
        args.out.write_text(json_text, encoding="utf-8")
        if not args.quiet:
            print(f"wrote {args.out}")

    if args.json:
        print(json_text)
    elif not args.quiet:
        print(format_summary(report))

    return 0 if report.passed else 1


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
