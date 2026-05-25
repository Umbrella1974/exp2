"""Tests for raw MANUS/Vive parser."""

from __future__ import annotations

from device_frame_models import DeviceAdapterConfig
from raw_manus_vive_parser import parse_raw_manus_vive_frame


def valid_raw() -> dict:
    return {
        "timestamp": 12.5,
        "frame": 7,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "side": "left",
                "nodes": [
                    {"id": 4, "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]},
                    {"id": 9, "position": [0.02, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]},
                ],
            }
        ],
        "trackers": [
            {
                "id": "tracker-a",
                "trackerId": "vive-1",
                "position": [1.0, 2.0, 3.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "quality": 3,
                "valid": True,
            }
        ],
        "extra": {"ignored": True},
    }


def test_valid_skeleton_and_tracker_parse_device_frame() -> None:
    frame = parse_raw_manus_vive_frame(valid_raw())
    assert frame.time == 12.5
    assert frame.source_frame_id == 7
    assert frame.hand is not None and frame.hand.valid
    assert frame.hand.glove_id == "glove-a"
    assert frame.hand.side == "left"
    assert frame.tracker is not None and frame.tracker.valid
    assert frame.tracker.tracker_id == "vive-1"


def test_missing_skeleton_returns_no_hand() -> None:
    raw = valid_raw()
    raw.pop("skeletons")
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.hand is None


def test_missing_tracker_returns_no_tracker() -> None:
    raw = valid_raw()
    raw.pop("trackers")
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.tracker is None


def test_tracker_valid_false_is_preserved() -> None:
    raw = valid_raw()
    raw["trackers"][0]["valid"] = False
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.tracker is not None
    assert frame.tracker.valid is False


def test_empty_nodes_make_hand_invalid() -> None:
    raw = valid_raw()
    raw["skeletons"][0]["nodes"] = []
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.hand is not None
    assert frame.hand.valid is False


def test_node_count_can_be_low_without_parser_failure() -> None:
    raw = valid_raw()
    raw["skeletons"][0]["nodes"] = [{"id": 4, "position": [0.0, 0.0, 0.0]}]
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.hand is not None
    assert frame.hand.valid is True
    assert list(frame.hand.nodes) == [4]


def test_timestamp_scale_is_applied() -> None:
    frame = parse_raw_manus_vive_frame(valid_raw(), DeviceAdapterConfig(timestamp_scale=0.001))
    assert frame.time == 0.0125


def test_node_with_invalid_position_is_skipped_without_crashing() -> None:
    raw = valid_raw()
    raw["skeletons"][0]["nodes"] = [
        {"id": 4, "position": [0.0, 0.0]},
        {"id": 9, "position": [0.02, 0.0, 0.0]},
    ]
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.hand is not None
    assert 4 not in frame.hand.nodes
    assert 9 in frame.hand.nodes


def test_node_with_missing_position_is_skipped_without_crashing() -> None:
    raw = valid_raw()
    raw["skeletons"][0]["nodes"] = [
        {"id": 4},
        {"id": 9, "position": [0.02, 0.0, 0.0]},
    ]
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.hand is not None
    assert 4 not in frame.hand.nodes
    assert 9 in frame.hand.nodes


def test_tracker_with_invalid_position_returns_no_tracker() -> None:
    raw = valid_raw()
    raw["trackers"][0]["position"] = [1.0, 2.0]
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.tracker is None


def test_extra_fields_do_not_break_parser() -> None:
    raw = valid_raw()
    raw["unexpected"] = {"deep": ["value"]}
    raw["skeletons"][0]["unexpected_node_schema_version"] = 123
    raw["trackers"][0]["debug"] = True
    frame = parse_raw_manus_vive_frame(raw)
    assert frame.hand is not None and frame.hand.valid
    assert frame.tracker is not None and frame.tracker.valid


def test_timing_fields_are_preserved_and_sync_delta_is_computed() -> None:
    raw = valid_raw()
    raw.update(
        {
            "combined_monotonic_ms": 1234.5,
            "skeleton_publish_time": 100,
            "tracker_publish_time": 101.5,
            "skeleton_receive_monotonic_ms": 2000.0,
            "tracker_receive_monotonic_ms": 2012.25,
            "skeleton_callback_index": 11,
            "tracker_callback_index": 12,
            "skeleton_frame": 21,
            "tracker_frame": 22,
        }
    )
    raw["trackers"][0]["last_update_time"] = 98.75

    frame = parse_raw_manus_vive_frame(raw)

    assert frame.combined_monotonic_ms == 1234.5
    assert frame.skeleton_publish_time == 100
    assert frame.tracker_publish_time == 101.5
    assert frame.skeleton_receive_monotonic_ms == 2000.0
    assert frame.tracker_receive_monotonic_ms == 2012.25
    assert frame.sync_delta_ms == 12.25
    assert frame.skeleton_callback_index == 11
    assert frame.tracker_callback_index == 12
    assert frame.skeleton_frame_id == 21
    assert frame.tracker_frame_id == 22
    assert frame.tracker is not None
    assert frame.tracker.last_update_time == 98.75


def test_missing_timing_fields_default_to_none() -> None:
    frame = parse_raw_manus_vive_frame(valid_raw())

    assert frame.combined_monotonic_ms is None
    assert frame.skeleton_receive_monotonic_ms is None
    assert frame.tracker_receive_monotonic_ms is None
    assert frame.sync_delta_ms is None
    assert frame.skeleton_callback_index is None
    assert frame.tracker_callback_index is None
    assert frame.skeleton_frame_id is None
    assert frame.tracker_frame_id is None
    assert frame.tracker is not None
    assert frame.tracker.last_update_time is None
