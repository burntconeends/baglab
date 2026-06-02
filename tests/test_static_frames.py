import numpy as np

from baglab.bronze import check_static_frames
from baglab.config import CheckConfig
from baglab.constants import N_BODY_JOINTS
from baglab.reader import LowstateData
from tests.conftest import make_bag, make_lowstate


def test_static_tail_detected(clean_bag):
    # clean_bag has a 1.5s frozen tail appended.
    result = check_static_frames(clean_bag, CheckConfig(min_static_duration=1.0))
    assert len(result.windows) >= 1
    longest = max(result.windows, key=lambda w: w.duration_s)
    assert longest.duration_s >= 1.0
    assert result.total_static_s > 0


def test_constant_motion_has_no_static():
    # Steady ramp on an arm joint -> constant nonzero velocity everywhere.
    rate, dur = 500.0, 3.0
    n = int(rate * dur)
    t_ns = (np.arange(n) / rate * 1e9).astype(np.int64)
    q = np.zeros((n, N_BODY_JOINTS))
    q[:, 18] = np.arange(n) * (1.0 / rate)  # 1 rad/s ramp
    ls = LowstateData(timestamps_ns=t_ns, q=q, dq=np.gradient(q, 1 / rate, axis=0))
    bag = make_bag({}, lowstate=ls)
    result = check_static_frames(bag, CheckConfig(static_vel_threshold=0.02))
    assert result.windows == []


def test_min_duration_filters_short_windows():
    # 0.5s static gap between two motion phases — below the 1.0s default.
    rate = 500.0
    seg = int(rate * 0.5)
    q = np.zeros((seg * 3, N_BODY_JOINTS))
    q[:seg, 18] = np.linspace(0, 0.5, seg)           # move
    q[seg:2 * seg, 18] = 0.5                          # hold 0.5s (static)
    q[2 * seg:, 18] = np.linspace(0.5, 1.0, seg)      # move
    t_ns = (np.arange(seg * 3) / rate * 1e9).astype(np.int64)
    ls = LowstateData(timestamps_ns=t_ns, q=q, dq=np.gradient(q, 1 / rate, axis=0))
    bag = make_bag({}, lowstate=ls)
    assert check_static_frames(bag, CheckConfig(min_static_duration=1.0)).windows == []
    # Lowering the duration floor surfaces the 0.5s window.
    assert check_static_frames(bag, CheckConfig(min_static_duration=0.3)).windows
