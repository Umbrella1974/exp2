"""Tests for task-space map configuration."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from geometry import point_in_track
from map_config import (
    MapBoxSpec,
    compile_map_to_track_region,
    load_map_config,
    map_config_to_trial_config,
    validate_map_config,
)


def test_load_example_map_and_validate() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")

    result = validate_map_config(config)

    assert result.is_valid
    assert result.errors == []
    assert config.map_id == "xoy_turn"
    assert len(config.track_boxes) == 2


def test_compile_map_to_track_region_and_initial_point_inside() -> None:
    config = load_map_config("maps/examples/xoy_straight.json")

    track_region, block_center, block_size = compile_map_to_track_region(config)

    assert point_in_track(block_center, track_region)
    assert block_size.x == 0.2
    assert block_size.y == 0.2
    assert block_size.z == 0.2
    assert len(track_region.boxes) == 1


def test_invalid_box_min_max_reports_error() -> None:
    config = load_map_config("maps/examples/xoy_straight.json")
    bad_box = replace(config.track_boxes[0], min=[1.0, 0.0, 0.0], max=[0.0, 1.0, 1.0])
    bad_config = replace(config, track_boxes=[bad_box])

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("min must be < max" in error for error in result.errors)


def test_block_initial_center_outside_track_reports_error() -> None:
    config = load_map_config("maps/examples/xoy_straight.json")
    bad_config = replace(config, block_initial_center_task=[9.0, 9.0, 0.0])

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("block_initial_center_task" in error for error in result.errors)


def test_target_region_without_intersection_reports_error() -> None:
    config = load_map_config("maps/examples/xoy_straight.json")
    target = MapBoxSpec(
        id="far_target",
        min=[5.0, 5.0, -0.1],
        max=[5.2, 5.2, 0.1],
        label="Far target",
        metadata={},
    )
    bad_config = replace(config, target_region=target)

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("target_region" in error for error in result.errors)


def test_target_region_face_touch_reports_warning() -> None:
    config = load_map_config("maps/examples/xoy_straight.json")
    target = MapBoxSpec(
        id="touch_target",
        min=[1.2, -0.15, -0.1],
        max=[1.4, 0.15, 0.1],
        label="Touch target",
        metadata={},
    )
    warning_config = replace(config, target_region=target)

    result = validate_map_config(warning_config)

    assert result.is_valid
    assert any("only touches" in warning for warning in result.warnings)


def test_ordered_track_boxes_with_gap_report_error() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    moved_second = replace(config.track_boxes[1], min=[2.0, 0.0, -0.1], max=[2.3, 0.9, 0.1])
    bad_config = replace(config, track_boxes=[config.track_boxes[0], moved_second])

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("gap" in error for error in result.errors)


def test_duplicate_order_reports_error() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    duplicate = replace(config.track_boxes[1], order=0)
    bad_config = replace(config, track_boxes=[config.track_boxes[0], duplicate])

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("duplicated" in error for error in result.errors)


def test_missing_order_when_some_boxes_ordered_reports_error() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    unordered = replace(config.track_boxes[1], order=None)
    bad_config = replace(config, track_boxes=[config.track_boxes[0], unordered])

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("all track_boxes must define order" in error for error in result.errors)


def test_non_contiguous_order_reports_error() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    non_contiguous = replace(config.track_boxes[1], order=2)
    bad_config = replace(config, track_boxes=[config.track_boxes[0], non_contiguous])

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("contiguous" in error for error in result.errors)


def test_edge_only_adjacent_boxes_report_error() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    first = replace(config.track_boxes[0], min=[0.0, 0.0, 0.0], max=[1.0, 1.0, 1.0])
    second = replace(config.track_boxes[1], min=[1.0, 1.0, 0.0], max=[2.0, 2.0, 1.0])
    bad_config = replace(
        config,
        block_initial_center_task=[0.5, 0.5, 0.5],
        track_boxes=[first, second],
        target_region=None,
    )

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("edge/point" in error for error in result.errors)


def test_point_only_adjacent_boxes_report_error() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    first = replace(config.track_boxes[0], min=[0.0, 0.0, 0.0], max=[1.0, 1.0, 1.0])
    second = replace(config.track_boxes[1], min=[1.0, 1.0, 1.0], max=[2.0, 2.0, 2.0])
    bad_config = replace(
        config,
        block_initial_center_task=[0.5, 0.5, 0.5],
        track_boxes=[first, second],
        target_region=None,
    )

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("edge/point" in error for error in result.errors)


def test_ordered_face_contact_with_area_passes() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    first = replace(config.track_boxes[0], min=[0.0, 0.0, 0.0], max=[1.0, 1.0, 1.0])
    second = replace(config.track_boxes[1], min=[1.0, 0.0, 0.0], max=[2.0, 1.0, 1.0])
    good_config = replace(
        config,
        block_initial_center_task=[0.5, 0.5, 0.5],
        track_boxes=[first, second],
        target_region=None,
    )

    result = validate_map_config(good_config)

    assert result.is_valid


def test_ordered_volume_overlap_passes() -> None:
    config = load_map_config("maps/examples/xoy_turn.json")
    first = replace(config.track_boxes[0], min=[0.0, 0.0, 0.0], max=[1.1, 1.0, 1.0])
    second = replace(config.track_boxes[1], min=[1.0, 0.0, 0.0], max=[2.0, 1.0, 1.0])
    good_config = replace(
        config,
        block_initial_center_task=[0.5, 0.5, 0.5],
        track_boxes=[first, second],
        target_region=None,
    )

    result = validate_map_config(good_config)

    assert result.is_valid


def test_target_region_edge_only_contact_reports_error() -> None:
    config = load_map_config("maps/examples/xoy_straight.json")
    target = MapBoxSpec(
        id="edge_target",
        min=[1.2, 0.15, -0.1],
        max=[1.4, 0.35, 0.1],
        label="Edge target",
        metadata={},
    )
    bad_config = replace(config, target_region=target)

    result = validate_map_config(bad_config)

    assert not result.is_valid
    assert any("edge/point" in error for error in result.errors)


def test_map_config_to_trial_config_is_json_serializable_and_complete() -> None:
    config = load_map_config("maps/examples/xoy_two_turns.json")

    trial_config = map_config_to_trial_config(config)
    encoded = json.dumps(trial_config)

    assert "xoy_two_turns" in encoded
    assert trial_config["map_config_version"] == 1
    assert trial_config["map_source_type"] == "manual"
    assert trial_config["description"] == config.description
    assert trial_config["is_generated"] is False
    assert len(trial_config["track_boxes"]) == 3
    assert trial_config["target_region"]["id"] == "target"


def test_load_map_config_from_temp_json(tmp_path: Path) -> None:
    source = Path("maps/examples/xoy_straight.json")
    target = tmp_path / "map.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    config = load_map_config(target)

    assert validate_map_config(config).is_valid
