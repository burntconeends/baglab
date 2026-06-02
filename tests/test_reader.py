"""Exercise read_bag's aggregation logic via a mocked AnyReader (no bag I/O)."""
import numpy as np

import baglab.reader as reader_mod
from baglab.reader import read_bag


class _Motor:
    def __init__(self, q, dq):
        self.q = q
        self.dq = dq


class _LowstateMsg:
    def __init__(self, qs, dqs):
        self.motor_state = [_Motor(q, dq) for q, dq in zip(qs, dqs)]


class _Conn:
    def __init__(self, topic, msgtype, cid):
        self.topic = topic
        self.msgtype = msgtype
        self.id = cid


class _FakeReader:
    def __init__(self, connections, messages):
        self.connections = connections
        self._messages = messages
        ts = [t for _, t, _ in messages]
        self.start_time = min(ts) if ts else 0
        self.end_time = max(ts) if ts else 0
        self.message_count = len(messages)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def messages(self):
        return iter(self._messages)

    def deserialize(self, rawdata, msgtype):
        return rawdata  # rawdata already carries the decoded message in the fake


def _install_fake(monkeypatch, connections, messages):
    monkeypatch.setattr(reader_mod, "build_typestore", lambda: None)
    fake = _FakeReader(connections, messages)
    monkeypatch.setattr(reader_mod, "AnyReader",
                        lambda paths, default_typestore=None: fake)


def test_topic_timelines_and_counts(monkeypatch):
    conns = [_Conn("/lowcmd", "unitree_hg/msg/LowCmd", 1),
             _Conn("/zed/image_raw/compressed", "sensor_msgs/msg/CompressedImage", 2)]
    # Deliberately out-of-order timestamps to confirm per-topic sorting.
    messages = [
        (conns[0], 300, b""), (conns[1], 100, b""),
        (conns[0], 100, b""), (conns[1], 200, b""),
        (conns[0], 200, b""),
    ]
    _install_fake(monkeypatch, conns, messages)

    bag = read_bag("dummy")
    assert bag.topics["/lowcmd"].count == 3
    assert list(bag.topics["/lowcmd"].timestamps_ns) == [100, 200, 300]
    assert bag.topics["/zed/image_raw/compressed"].count == 2
    assert bag.message_count == 5
    assert bag.start_ns == 100 and bag.end_ns == 300
    assert bag.lowstate is None
    assert bag.present_topics == {"/lowcmd", "/zed/image_raw/compressed"}


def test_lowstate_extraction(monkeypatch):
    conn = _Conn("/lowstate", "unitree_hg/msg/LowState", 1)
    # 35 motors reported; reader keeps the first 29.
    msg_a = _LowstateMsg([0.1 * i for i in range(35)], [0.01 * i for i in range(35)])
    msg_b = _LowstateMsg([0.2 * i for i in range(35)], [0.02 * i for i in range(35)])
    messages = [(conn, 2_000_000, msg_b), (conn, 1_000_000, msg_a)]  # out of order
    _install_fake(monkeypatch, [conn], messages)

    bag = read_bag("dummy")
    ls = bag.lowstate
    assert ls is not None
    assert ls.q.shape == (2, 29)
    # Sorted by timestamp -> msg_a (t=1e6) first.
    assert list(ls.timestamps_ns) == [1_000_000, 2_000_000]
    np.testing.assert_allclose(ls.q[0], [0.1 * i for i in range(29)])
    np.testing.assert_allclose(ls.dq[1], [0.02 * i for i in range(29)])


def test_lowstate_decimation_above_cap(monkeypatch):
    conn = _Conn("/lowstate", "unitree_hg/msg/LowState", 1)
    # 100 motors per msg (we keep 29); 50 frames; cap to 10 -> stride 5 -> 10 kept.
    n_frames = 50
    msgs = []
    for i in range(n_frames):
        qs = [float(i) + 0.001 * j for j in range(29)]
        dqs = [0.0] * 29
        msgs.append((conn, i * 1_000_000, _LowstateMsg(qs, dqs)))
    _install_fake(monkeypatch, [conn], msgs)
    bag = read_bag("dummy", max_lowstate_samples=10)
    ls = bag.lowstate
    assert ls is not None
    assert ls.decimated is True
    assert ls.raw_count == n_frames
    assert ls.timestamps_ns.size == 10  # stride = ceil(50/10) = 5 -> 10 kept


def test_zero_message_topic_present_but_empty(monkeypatch):
    conns = [_Conn("/lowstate", "unitree_hg/msg/LowState", 1)]
    _install_fake(monkeypatch, conns, [])  # connection exists, no messages
    bag = read_bag("dummy")
    assert "/lowstate" in bag.topics
    assert bag.topics["/lowstate"].count == 0
    assert bag.present_topics == set()
