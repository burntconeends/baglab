import numpy as np

from baglab.bronze import check_integrity
from baglab.config import CheckConfig
from baglab.constants import DEFAULT_EXPECTED_TOPICS
from baglab.reader import TopicTimeline
from tests.conftest import make_bag, make_timeline


def test_all_topics_present_passes(clean_bag):
    result = check_integrity(clean_bag, CheckConfig())
    assert result.passed
    assert result.missing_topics == []
    assert set(result.present_topics) >= set(DEFAULT_EXPECTED_TOPICS)


def test_missing_topic_fails():
    topics = {
        t: make_timeline(t, rate_hz=30.0, duration_s=2.0)
        for t in DEFAULT_EXPECTED_TOPICS
        if t != "/lowstate"
    }
    bag = make_bag(topics)
    result = check_integrity(bag, CheckConfig())
    assert not result.passed
    assert "/lowstate" in result.missing_topics


def test_gap_detection():
    # 30 Hz for 6s with a ~1s hole at t=3s -> one gap well above 5x median.
    tl = make_timeline("/zed/image_raw/compressed", rate_hz=30.0,
                        duration_s=6.0, gaps_at_s=(3.0,))
    bag = make_bag({"/zed/image_raw/compressed": tl})
    cfg = CheckConfig(expected_topics=["/zed/image_raw/compressed"], gap_factor=5.0)
    result = check_integrity(bag, cfg)
    stat = next(s for s in result.topic_stats
                if s.topic == "/zed/image_raw/compressed")
    assert stat.n_gaps >= 1
    assert stat.gaps[0].factor > 5.0
    assert stat.gaps[0].gap_s > 0.5


def test_rate_computation():
    tl = make_timeline("/lowstate", rate_hz=500.0, duration_s=2.0)
    bag = make_bag({"/lowstate": tl})
    result = check_integrity(bag, CheckConfig(expected_topics=["/lowstate"]))
    stat = result.topic_stats[0]
    assert abs(stat.rate_hz - 500.0) < 5.0


def test_gap_floor_suppresses_sub_floor_jitter():
    # A short jitter "gap" well below the absolute floor must not be flagged,
    # even though it exceeds gap_factor x median.
    # 30 Hz baseline (median 33 ms), one 90 ms hole (~2.7x median, 0.09s).
    import numpy as np
    n = 60  # 2s at 30Hz
    ts = (np.arange(n) * int(1e9 / 30.0)).astype(np.int64)
    # remove a couple of samples around index 30 to open a ~90ms hole
    keep = np.ones(n, dtype=bool)
    keep[30:32] = False
    ts = ts[keep]
    from baglab.reader import TopicTimeline
    tl = TopicTimeline(topic="/zed/image_raw/compressed",
                       msgtype="sensor_msgs/CompressedImage",
                       timestamps_ns=ts)
    bag = make_bag({"/zed/image_raw/compressed": tl})
    cfg = CheckConfig(expected_topics=["/zed/image_raw/compressed"],
                      gap_factor=2.0, gap_floor_s=0.1)
    result = check_integrity(bag, cfg)
    stat = result.topic_stats[0]
    assert stat.n_gaps == 0, f"sub-floor jitter should not flag; got {stat.gaps}"


def test_unexpected_topic_reported():
    tl = make_timeline("/surprise", rate_hz=10.0, duration_s=1.0)
    bag = make_bag({"/surprise": tl})
    result = check_integrity(bag, CheckConfig(expected_topics=["/lowstate"]))
    assert "/surprise" in result.unexpected_topics
    assert "/lowstate" in result.missing_topics
