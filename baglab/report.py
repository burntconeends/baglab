"""Report data model: structured results + JSON serialization + summary printer.

All check functions return the dataclasses defined here. BagReport bundles them
with bag metadata and the config used. Times are reported in seconds relative to
the bag start (start_time_ns is included so absolute time can be recovered).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from dataclasses import dataclass, field

import numpy as np


# --------------------------------------------------------------------------- #
# Priority 1 — data integrity
# --------------------------------------------------------------------------- #
@dataclass
class Gap:
    start_s: float       # relative to bag start
    end_s: float
    gap_s: float
    factor: float        # gap_s / median interval for the topic


@dataclass
class TopicStat:
    topic: str
    msgtype: str
    count: int
    span_s: float            # last - first message time
    rate_hz: float
    median_interval_s: float
    max_interval_s: float
    n_gaps: int
    gaps: list[Gap] = field(default_factory=list)
    low_rate: bool = False


@dataclass
class IntegrityResult:
    present_topics: list[str]
    missing_topics: list[str]
    unexpected_topics: list[str]
    topic_stats: list[TopicStat]
    passed: bool


# --------------------------------------------------------------------------- #
# Priority 4 — trajectory smoothness
# --------------------------------------------------------------------------- #
@dataclass
class JointSmoothness:
    joint_index: int
    joint_name: str
    max_abs_vel: float
    max_abs_accel: float
    n_vel_violations: int
    n_accel_violations: int


@dataclass
class SmoothnessViolation:
    joint_index: int
    joint_name: str
    time_s: float
    kind: str        # "velocity" | "acceleration"
    value: float
    threshold: float


@dataclass
class SmoothnessResult:
    n_samples: int
    vel_threshold: float
    accel_threshold: float
    per_joint: list[JointSmoothness]
    violations: list[SmoothnessViolation]
    n_violations_total: int
    violations_truncated: bool
    passed: bool
    note: str = ""


# --------------------------------------------------------------------------- #
# Priority 5 — static-frame scrubbing
# --------------------------------------------------------------------------- #
@dataclass
class StaticWindow:
    start_s: float
    end_s: float
    duration_s: float


@dataclass
class StaticResult:
    static_vel_threshold: float
    min_static_duration: float
    static_joints: list[int]
    windows: list[StaticWindow]
    total_static_s: float
    fraction_static: float
    passed: bool
    note: str = ""


# --------------------------------------------------------------------------- #
# Top-level report
# --------------------------------------------------------------------------- #
@dataclass
class BagReport:
    bag_path: str
    bag_name: str
    duration_s: float
    message_count: int
    start_time_ns: int
    end_time_ns: int
    generated_at: str
    config: dict
    integrity: IntegrityResult
    smoothness: SmoothnessResult
    static_frames: StaticResult
    lowstate_decimated: bool = False
    lowstate_raw_count: int = 0
    # Deferred vision checks (priorities 2 & 3) — see baglab.bronze.vision.
    vision: dict = field(default_factory=lambda: {
        "status": "not_implemented",
        "deferred": {
            "gripper_hand_tracking": "priority 2 — needs HaMeR (see bronze/vision.py)",
            "object_tracking": "priority 3 — needs Grounded-SAM (see bronze/vision.py)",
        },
    })

    @property
    def passed(self) -> bool:
        return (
            self.integrity.passed
            and self.smoothness.passed
            and self.static_frames.passed
        )


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def _to_jsonable(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = {f.name: _to_jsonable(getattr(obj, f.name))
             for f in dataclasses.fields(obj)}
        # Surface computed `passed` on dataclasses that expose it as a property.
        if isinstance(obj, BagReport):
            d["passed"] = obj.passed
        return d
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def report_to_dict(report: BagReport) -> dict:
    return _to_jsonable(report)


def report_to_json(report: BagReport, indent: int = 2) -> str:
    return json.dumps(report_to_dict(report), indent=indent)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Human-readable summary
# --------------------------------------------------------------------------- #
def _status(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def format_summary(report: BagReport) -> str:
    lines: list[str] = []
    w = 78
    lines.append("=" * w)
    lines.append(f"baglab bronze report — {report.bag_name}")
    lines.append("=" * w)
    lines.append(f"path:      {report.bag_path}")
    lines.append(f"duration:  {report.duration_s:.2f}s   messages: {report.message_count}")
    lines.append(f"overall:   {_status(report.passed)}")
    sm = report.smoothness
    if getattr(report, "lowstate_decimated", False):
        lines.append(f"note:      /lowstate decimated from "
                     f"{report.lowstate_raw_count} -> {sm.n_samples} samples "
                     f"(--max-lowstate-samples)")
    lines.append("")

    # --- Integrity ---
    integ = report.integrity
    lines.append(f"[1] Data integrity — {_status(integ.passed)}")
    if integ.missing_topics:
        lines.append(f"    MISSING topics: {', '.join(integ.missing_topics)}")
    if integ.unexpected_topics:
        lines.append(f"    extra topics:   {', '.join(integ.unexpected_topics)}")
    lines.append(
        f"    {'topic':<34}{'count':>8}{'rate Hz':>10}{'gaps':>6}{'maxgap s':>10}"
    )
    for st in sorted(integ.topic_stats, key=lambda s: s.topic):
        flag = " !" if (st.n_gaps or st.low_rate) else ""
        lines.append(
            f"    {st.topic:<34}{st.count:>8}{st.rate_hz:>10.2f}"
            f"{st.n_gaps:>6}{st.max_interval_s:>10.3f}{flag}"
        )
    lines.append("")

    # --- Smoothness ---
    sm = report.smoothness
    lines.append(f"[4] Trajectory smoothness — {_status(sm.passed)}")
    if sm.note:
        lines.append(f"    {sm.note}")
    else:
        lines.append(
            f"    samples: {sm.n_samples}   vel>{sm.vel_threshold} rad/s, "
            f"accel>{sm.accel_threshold} rad/s^2"
        )
        lines.append(
            f"    total violations: {sm.n_violations_total}"
            + ("  (event list truncated)" if sm.violations_truncated else "")
        )
        flagged = [j for j in sm.per_joint
                   if j.n_vel_violations or j.n_accel_violations]
        if flagged:
            lines.append(
                f"    {'joint':<22}{'max|v|':>9}{'max|a|':>10}{'vViol':>7}{'aViol':>7}"
            )
            for j in flagged:
                lines.append(
                    f"    {j.joint_name:<22}{j.max_abs_vel:>9.2f}"
                    f"{j.max_abs_accel:>10.2f}{j.n_vel_violations:>7}"
                    f"{j.n_accel_violations:>7}"
                )
        else:
            lines.append("    no joints exceeded thresholds")
    lines.append("")

    # --- Static frames ---
    sf = report.static_frames
    lines.append(f"[5] Static-frame scrubbing — {_status(sf.passed)}")
    if sf.note:
        lines.append(f"    {sf.note}")
    lines.append(
        f"    static windows: {len(sf.windows)}   total: {sf.total_static_s:.2f}s "
        f"({sf.fraction_static * 100:.1f}% of bag)"
    )
    for win in sf.windows[:20]:
        lines.append(
            f"      drop [{win.start_s:8.2f} .. {win.end_s:8.2f}]  "
            f"({win.duration_s:.2f}s)"
        )
    if len(sf.windows) > 20:
        lines.append(f"      ... and {len(sf.windows) - 20} more")
    lines.append("")

    lines.append(f"[2] gripper/hand tracking — DEFERRED (needs HaMeR)")
    lines.append(f"[3] object tracking/size  — DEFERRED (needs Grounded-SAM)")
    lines.append("=" * w)
    return "\n".join(lines)
