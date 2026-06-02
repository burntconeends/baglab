import numpy as np

from baglab.bronze import check_smoothness
from baglab.config import CheckConfig
from baglab.constants import N_BODY_JOINTS
from baglab.reader import LowstateData
from tests.conftest import make_bag, make_lowstate


def test_smooth_motion_no_violations(clean_bag):
    result = check_smoothness(clean_bag, CheckConfig())
    assert result.passed
    assert result.n_violations_total == 0


def test_velocity_spike_detected():
    ls, _ = make_lowstate(rate_hz=500.0, duration_s=2.0, moving_joints=())
    # Inject a single-sample position jump on the right_elbow (index 25):
    # a 2 rad step at 500 Hz -> ~1000 rad/s, far above the 6 rad/s threshold.
    mid = ls.q.shape[0] // 2
    ls.q[mid, 25] += 2.0
    bag = make_bag({}, lowstate=ls)
    result = check_smoothness(bag, CheckConfig())
    assert not result.passed
    elbow = next(j for j in result.per_joint if j.joint_index == 25)
    assert elbow.n_vel_violations >= 1
    assert any(v.joint_index == 25 and v.kind == "velocity"
               for v in result.violations)


def test_thresholds_are_configurable():
    ls, _ = make_lowstate(rate_hz=500.0, duration_s=2.0, moving_joints=())
    mid = ls.q.shape[0] // 2
    ls.q[mid, 25] += 0.05  # ~25 rad/s spike
    bag = make_bag({}, lowstate=ls)
    # Default 6 rad/s flags it; a very high threshold should not.
    assert not check_smoothness(bag, CheckConfig(vel_threshold=6.0)).passed
    assert check_smoothness(
        bag, CheckConfig(vel_threshold=1e6, accel_threshold=1e12)
    ).passed


def test_insufficient_samples():
    ls = LowstateData(
        timestamps_ns=np.array([0, 1], dtype=np.int64),
        q=np.zeros((2, N_BODY_JOINTS)), dq=np.zeros((2, N_BODY_JOINTS)),
    )
    bag = make_bag({}, lowstate=ls)
    result = check_smoothness(bag, CheckConfig())
    assert result.passed
    assert "insufficient" in result.note
