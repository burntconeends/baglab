"""Priority 1 — data integrity.

Verifies expected topics are present, then for each present topic computes
message count / rate and detects timestamp jitter and dropped frames: any
inter-message interval exceeding `gap_factor` x the topic's median interval is
reported as a gap (with its location relative to bag start).
"""
from __future__ import annotations

import numpy as np

from baglab.config import CheckConfig
from baglab.reader import BagData
from baglab.report import Gap, IntegrityResult, TopicStat


def _topic_stat(
    topic: str, msgtype: str, ts_ns: np.ndarray, start_ns: int, config: CheckConfig
) -> TopicStat:
    count = int(ts_ns.size)
    if count < 2:
        return TopicStat(
            topic=topic, msgtype=msgtype, count=count, span_s=0.0, rate_hz=0.0,
            median_interval_s=0.0, max_interval_s=0.0, n_gaps=0, gaps=[],
            low_rate=(count < 1 and config.min_rate_hz > 0),
        )

    t = np.sort(ts_ns)
    intervals = np.diff(t) / 1e9  # seconds
    span_s = float((t[-1] - t[0]) / 1e9)
    rate_hz = float((count - 1) / span_s) if span_s > 0 else 0.0
    median_iv = float(np.median(intervals))
    max_iv = float(intervals.max())

    gaps: list[Gap] = []
    if median_iv > 0:
        # Take the stricter of `gap_factor * median` and `gap_floor_s` so
        # bursty record-time jitter (sub-100 ms) doesn't masquerade as a gap.
        threshold = max(config.gap_factor * median_iv, config.gap_floor_s)
        gap_idx = np.nonzero(intervals > threshold)[0]
        for i in gap_idx:
            gaps.append(Gap(
                start_s=float((t[i] - start_ns) / 1e9),
                end_s=float((t[i + 1] - start_ns) / 1e9),
                gap_s=float(intervals[i]),
                factor=float(intervals[i] / median_iv),
            ))

    low_rate = config.min_rate_hz > 0 and rate_hz < config.min_rate_hz
    return TopicStat(
        topic=topic, msgtype=msgtype, count=count, span_s=span_s, rate_hz=rate_hz,
        median_interval_s=median_iv, max_interval_s=max_iv,
        n_gaps=len(gaps), gaps=gaps, low_rate=low_rate,
    )


def check_integrity(bag: BagData, config: CheckConfig) -> IntegrityResult:
    present = bag.present_topics
    expected = set(config.expected_topics)
    missing = sorted(expected - present)
    unexpected = sorted(present - expected)

    stats: list[TopicStat] = []
    for topic in sorted(present):
        tl = bag.topics[topic]
        stats.append(_topic_stat(
            topic, tl.msgtype, tl.timestamps_ns, bag.start_ns, config
        ))

    passed = len(missing) == 0
    return IntegrityResult(
        present_topics=sorted(present),
        missing_topics=missing,
        unexpected_topics=unexpected,
        topic_stats=stats,
        passed=passed,
    )
