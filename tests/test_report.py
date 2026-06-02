"""Confirm the full report serializes to JSON-native types (no numpy leakage)."""
import json

from baglab.bronze import check_integrity, check_smoothness, check_static_frames
from baglab.config import CheckConfig
from baglab.report import BagReport, now_iso, report_to_dict, report_to_json


def _build_report(bag, config):
    return BagReport(
        bag_path=bag.path, bag_name=bag.name, duration_s=bag.duration_s,
        message_count=bag.message_count, start_time_ns=bag.start_ns,
        end_time_ns=bag.end_ns, generated_at=now_iso(), config=config.to_dict(),
        integrity=check_integrity(bag, config),
        smoothness=check_smoothness(bag, config),
        static_frames=check_static_frames(bag, config),
    )


def _assert_jsonable(obj):
    """Recursively assert only JSON-native types are present."""
    if isinstance(obj, dict):
        for v in obj.values():
            _assert_jsonable(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_jsonable(v)
    else:
        assert isinstance(obj, (str, int, float, bool, type(None))), type(obj)


def test_report_round_trips_to_json(clean_bag):
    report = _build_report(clean_bag, CheckConfig())
    d = report_to_dict(report)
    _assert_jsonable(d)
    # Top-level keys the consumer relies on.
    for key in ("bag_name", "duration_s", "passed", "integrity",
                "smoothness", "static_frames", "vision", "config"):
        assert key in d
    # Re-parse to prove it is valid JSON.
    parsed = json.loads(report_to_json(report))
    assert parsed["passed"] is True
    assert parsed["vision"]["status"] == "not_implemented"
    assert "gripper_hand_tracking" in parsed["vision"]["deferred"]
