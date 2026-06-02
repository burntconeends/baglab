"""Test fixtures: build synthetic BagData with no bag I/O.

The bronze checks consume BagData (see baglab.reader), so we can exercise all
their logic against hand-built numpy timeseries. This keeps the unit tests fast
and free of rosbags / Unitree-schema dependencies.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

# Allow `import baglab` when running pytest from the repo root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baglab.constants import N_BODY_JOINTS  # noqa: E402
from baglab.reader import BagData, LowstateData, TopicTimeline  # noqa: E402


def make_timeline(topic, rate_hz, duration_s, start_ns=0, msgtype="x/msg/Y",
                  gaps_at_s=()):
    """Evenly-spaced timestamps at rate_hz, optionally dropping samples to create
    gaps near each time in gaps_at_s."""
    n = int(rate_hz * duration_s)
    dt_ns = int(1e9 / rate_hz)
    ts = start_ns + np.arange(n, dtype=np.int64) * dt_ns
    if gaps_at_s:
        drop = np.zeros(n, dtype=bool)
        for g in gaps_at_s:
            idx = int(g * rate_hz)
            # Drop a run of samples to open a gap.
            drop[idx:idx + int(rate_hz)] = True  # ~1s hole
        ts = ts[~drop]
    return TopicTimeline(topic=topic, msgtype=msgtype, timestamps_ns=ts)


def make_lowstate(rate_hz=500.0, duration_s=4.0, start_ns=0,
                  moving_joints=(18, 25), amplitude=0.3, freq=0.5):
    """Smooth sinusoidal motion on a couple of arm joints, rest held static."""
    n = int(rate_hz * duration_s)
    dt = 1.0 / rate_hz
    t = np.arange(n) * dt
    ts = start_ns + (t * 1e9).astype(np.int64)
    q = np.zeros((n, N_BODY_JOINTS), dtype=np.float64)
    for j in moving_joints:
        q[:, j] = amplitude * np.sin(2 * np.pi * freq * t)
    dq = np.gradient(q, dt, axis=0)
    return LowstateData(timestamps_ns=ts, q=q, dq=dq), t


def make_bag(topics, lowstate=None, start_ns=0, end_ns=None):
    if end_ns is None:
        ends = [tl.timestamps_ns[-1] for tl in topics.values()
                if tl.timestamps_ns.size]
        if lowstate is not None and lowstate.timestamps_ns.size:
            ends.append(int(lowstate.timestamps_ns[-1]))
        end_ns = max(ends) if ends else start_ns
    count = sum(tl.count for tl in topics.values())
    return BagData(
        path="synthetic.mcap", name="synthetic.mcap", start_ns=start_ns,
        end_ns=int(end_ns), message_count=count, topics=topics, lowstate=lowstate,
    )


@pytest.fixture
def clean_bag():
    """A well-formed bag: all expected topics present, smooth motion, one static
    tail window."""
    from baglab.constants import DEFAULT_EXPECTED_TOPICS
    topics = {
        t: make_timeline(t, rate_hz=30.0, duration_s=4.0)
        for t in DEFAULT_EXPECTED_TOPICS
    }
    # duration 3.5s ends the 0.5 Hz sinusoid at a velocity node (peak), so
    # freezing into the static tail introduces no velocity discontinuity.
    ls, _ = make_lowstate(duration_s=3.5)
    # Append a 1.5s static tail (all joints frozen) so static detection has a hit.
    n_static = int(500.0 * 1.5)
    last_ts = ls.timestamps_ns[-1]
    static_ts = last_ts + (np.arange(1, n_static + 1) * int(1e9 / 500.0))
    static_q = np.tile(ls.q[-1], (n_static, 1))
    static_dq = np.zeros((n_static, ls.dq.shape[1]))
    ls = LowstateData(
        timestamps_ns=np.concatenate([ls.timestamps_ns, static_ts]),
        q=np.concatenate([ls.q, static_q]),
        dq=np.concatenate([ls.dq, static_dq]),
    )
    return make_bag(topics, lowstate=ls)
