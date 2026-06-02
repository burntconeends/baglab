"""Read a G1 rosbag into in-memory timeseries (BagData).

A single streaming pass over the bag collects, per topic, the record
timestamps (cheap — no deserialization needed) plus the /lowstate joint
positions/velocities (deserialized, since smoothness and static-frame checks
need them). Image/other payloads are never decoded.

The checks in baglab.bronze operate on BagData rather than the reader, so they
can be unit-tested against synthetic data with no bag I/O.

Uses rosbags.highlevel.AnyReader with the Unitree typestore from
baglab.typestore.build_typestore() so raw .db3 bags (no embedded schemas)
deserialize; converted .mcap files carry their own schemas and work too.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rosbags.highlevel import AnyReader

from baglab.constants import N_BODY_JOINTS
from baglab.typestore import build_typestore

LOWSTATE_TOPIC = "/lowstate"


@dataclass
class TopicTimeline:
    topic: str
    msgtype: str
    timestamps_ns: np.ndarray  # int64, sorted ascending

    @property
    def count(self) -> int:
        return int(self.timestamps_ns.size)


@dataclass
class LowstateData:
    """High-rate body proprioception sampled from /lowstate."""
    timestamps_ns: np.ndarray  # (N,) int64, sorted ascending
    q: np.ndarray              # (N, 29) float64 joint positions [rad]
    dq: np.ndarray             # (N, 29) float64 joint velocities [rad/s] (as reported)
    decimated: bool = False    # True if read_bag stride-decimated to fit the cap
    raw_count: int = 0         # original sample count before decimation


@dataclass
class BagData:
    path: str
    name: str
    start_ns: int
    end_ns: int
    message_count: int
    topics: dict[str, TopicTimeline]
    lowstate: LowstateData | None

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_ns - self.start_ns) / 1e9)

    @property
    def present_topics(self) -> set[str]:
        return {t for t, tl in self.topics.items() if tl.count > 0}


def read_bag(
    bag_path: str | Path,
    *,
    want_lowstate: bool = True,
    max_lowstate_samples: int | None = None,
) -> BagData:
    """Stream a bag once and return BagData.

    bag_path may be a standalone .mcap file or a rosbag2 directory (.db3).
    If `max_lowstate_samples` is set and the bag exceeds it, the /lowstate
    timeseries is uniformly stride-decimated to fit (see LowstateData.decimated).
    """
    bag_path = Path(bag_path)
    typestore = build_typestore()

    ts_lists: dict[str, list[int]] = {}
    msgtypes: dict[str, str] = {}
    ls_ts: list[int] = []
    ls_q: list[list[float]] = []
    ls_dq: list[list[float]] = []

    with AnyReader([bag_path], default_typestore=typestore) as reader:
        # Register every connection so even zero-message topics appear.
        for conn in reader.connections:
            msgtypes.setdefault(conn.topic, conn.msgtype)
            ts_lists.setdefault(conn.topic, [])

        for conn, timestamp, rawdata in reader.messages():
            ts_lists[conn.topic].append(int(timestamp))
            if want_lowstate and conn.topic == LOWSTATE_TOPIC:
                try:
                    msg = reader.deserialize(rawdata, conn.msgtype)
                    motors = msg.motor_state
                    n = min(N_BODY_JOINTS, len(motors))
                    q = [float(motors[i].q) for i in range(n)]
                    dq = [float(motors[i].dq) for i in range(n)]
                    # Pad if a bag somehow reports fewer than 29 motors.
                    if n < N_BODY_JOINTS:
                        q += [0.0] * (N_BODY_JOINTS - n)
                        dq += [0.0] * (N_BODY_JOINTS - n)
                    ls_ts.append(int(timestamp))
                    ls_q.append(q)
                    ls_dq.append(dq)
                except Exception:
                    # Skip a malformed/undecodable /lowstate frame rather than abort.
                    pass

    topics: dict[str, TopicTimeline] = {}
    all_min, all_max, message_count = None, None, 0
    for topic, ts in ts_lists.items():
        arr = np.asarray(sorted(ts), dtype=np.int64)
        topics[topic] = TopicTimeline(
            topic=topic, msgtype=msgtypes.get(topic, "unknown"), timestamps_ns=arr
        )
        message_count += int(arr.size)
        if arr.size:
            lo, hi = int(arr[0]), int(arr[-1])
            all_min = lo if all_min is None else min(all_min, lo)
            all_max = hi if all_max is None else max(all_max, hi)

    start_ns = all_min if all_min is not None else 0
    end_ns = all_max if all_max is not None else 0

    lowstate = None
    if ls_ts:
        order = np.argsort(np.asarray(ls_ts, dtype=np.int64))
        ts_arr = np.asarray(ls_ts, dtype=np.int64)[order]
        q_arr = np.asarray(ls_q, dtype=np.float64)[order]
        dq_arr = np.asarray(ls_dq, dtype=np.float64)[order]
        raw_count = int(ts_arr.size)
        decimated = False
        if max_lowstate_samples is not None and raw_count > max_lowstate_samples:
            stride = int(np.ceil(raw_count / max_lowstate_samples))
            ts_arr = ts_arr[::stride]
            q_arr = q_arr[::stride]
            dq_arr = dq_arr[::stride]
            decimated = True
        lowstate = LowstateData(
            timestamps_ns=ts_arr, q=q_arr, dq=dq_arr,
            decimated=decimated, raw_count=raw_count,
        )

    return BagData(
        path=str(bag_path),
        name=bag_path.name,
        start_ns=start_ns,
        end_ns=end_ns,
        message_count=message_count,
        topics=topics,
        lowstate=lowstate,
    )
