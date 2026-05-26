"""Tests for simple orthogonal corridor map generation."""

from __future__ import annotations

import json

import pytest

from geometry import point_in_track
from map_config import (
    compile_map_to_track_region,
    map_config_to_dict,
    validate_map_config,
)
from map_generator import generate_orthogonal_corridor_map


def test_same_seed_generates_same_map() -> None:
    first = _generate(seed=123)
    second = _generate(seed=123)

    assert map_config_to_dict(first) == map_config_to_dict(second)


def test_different_seed_can_generate_different_map() -> None:
    first = _generate(seed=123)
    second = _generate(seed=456)

    assert map_config_to_dict(first) != map_config_to_dict(second)


def test_generated_map_validates_and_contains_start() -> None:
    config = _generate(seed=99)

    result = validate_map_config(config)
    track_region, block_center, _ = compile_map_to_track_region(config)

    assert result.is_valid
    assert result.errors == []
    assert point_in_track(block_center, track_region)
    assert config.block_initial_center_task == [0.0, 0.0, 0.0]
    assert config.track_boxes[0].min[0] <= 0.0 <= config.track_boxes[0].max[0]
    assert config.track_boxes[0].min[1] <= 0.0 <= config.track_boxes[0].max[1]
    assert config.track_boxes[0].min[2] <= 0.0 <= config.track_boxes[0].max[2]


def test_generated_segments_are_ordered_and_continuous() -> None:
    config = _generate(seed=42, num_segments=6)

    assert validate_map_config(config).is_valid
    assert [box.order for box in config.track_boxes] == list(range(6))
    for previous, current in zip(config.track_boxes, config.track_boxes[1:]):
        assert _has_volume_overlap_or_face_contact(previous, current)


def test_generated_boxes_include_required_metadata() -> None:
    config = _generate(seed=7, num_segments=3)

    for index, box in enumerate(config.track_boxes):
        assert box.id == f"segment_{index:02d}"
        assert box.order == index
        assert box.label
        assert box.metadata["segment_direction"] in {"x+", "x-", "y+", "y-"}
        assert box.metadata["direction"] == box.metadata["segment_direction"]
        assert box.metadata["segment_length"] > 0
        assert box.metadata["turn_from_previous"] in {"left", "right", "straight"}


def test_generated_map_metadata_and_json_output() -> None:
    config = _generate(seed=8)
    payload = map_config_to_dict(config)
    encoded = json.dumps(payload)

    assert "generate_orthogonal_corridor_map" in encoded
    assert payload["metadata"]["generated"] is True
    assert payload["metadata"]["generator_seed"] == 8
    assert payload["metadata"]["generator_params"]["plane"] == "xoy"


def test_generated_target_region_is_independent_and_overlaps_last_segment() -> None:
    config = _generate(seed=8, num_segments=3)
    target = config.target_region
    last_segment = config.track_boxes[-1]

    assert target is not None
    assert target.id == "target"
    assert target.label == "Target region"
    assert target.min != last_segment.min or target.max != last_segment.max
    assert target.metadata["type"] == "target_region"
    assert target.metadata["based_on_segment_id"] == last_segment.id
    assert validate_map_config(config).is_valid
    assert _has_positive_volume_overlap(target, last_segment)


def test_generated_target_region_is_at_last_segment_end() -> None:
    config = generate_orthogonal_corridor_map(
        map_id="target_at_end",
        seed=1,
        num_segments=1,
        start=[0.0, 0.0, 0.0],
        initial_direction="x+",
        segment_length_range=(0.4, 0.4),
        track_width=0.2,
        z_tolerance=0.1,
        allowed_turns=["straight"],
        target_length=0.1,
    )
    target = config.target_region
    last_segment = config.track_boxes[-1]

    assert target is not None
    assert target.max[0] == pytest.approx(last_segment.max[0])
    assert target.min[0] == pytest.approx(last_segment.max[0] - 0.1)
    assert target.min[1:] == pytest.approx(last_segment.min[1:])
    assert target.max[1:] == pytest.approx(last_segment.max[1:])


def test_target_length_default_uses_min_of_20cm_or_quarter_segment() -> None:
    config = generate_orthogonal_corridor_map(
        map_id="default_target",
        seed=1,
        num_segments=1,
        start=[0.0, 0.0, 0.0],
        initial_direction="x+",
        segment_length_range=(0.4, 0.4),
        track_width=0.2,
        z_tolerance=0.1,
        allowed_turns=["straight"],
    )

    assert config.target_region is not None
    assert config.target_region.metadata["target_length"] == pytest.approx(0.1)


def test_target_length_is_clamped_when_longer_than_last_segment() -> None:
    config = generate_orthogonal_corridor_map(
        map_id="clamped_target",
        seed=1,
        num_segments=1,
        start=[0.0, 0.0, 0.0],
        initial_direction="x+",
        segment_length_range=(0.4, 0.4),
        track_width=0.2,
        z_tolerance=0.1,
        allowed_turns=["straight"],
        target_length=2.0,
    )

    assert config.target_region is not None
    assert config.target_region.metadata["target_length"] == pytest.approx(0.4)
    assert config.target_region.metadata["warnings"]
    assert config.target_region.min != config.track_boxes[-1].min
    assert config.target_region.max == config.track_boxes[-1].max


def test_left_and_right_are_90_degree_relative_turns() -> None:
    left_map = generate_orthogonal_corridor_map(
        map_id="left_turn",
        seed=1,
        num_segments=2,
        start=[0.0, 0.0, 0.0],
        initial_direction="x+",
        segment_length_range=(0.4, 0.4),
        track_width=0.2,
        z_tolerance=0.1,
        allowed_turns=["left"],
    )
    right_map = generate_orthogonal_corridor_map(
        map_id="right_turn",
        seed=1,
        num_segments=2,
        start=[0.0, 0.0, 0.0],
        initial_direction="x+",
        segment_length_range=(0.4, 0.4),
        track_width=0.2,
        z_tolerance=0.1,
        allowed_turns=["right"],
    )

    assert [box.metadata["segment_direction"] for box in left_map.track_boxes] == ["x+", "y+"]
    assert [box.metadata["segment_direction"] for box in right_map.track_boxes] == ["x+", "y-"]


def test_generator_rejects_unsupported_plane_and_turn() -> None:
    with pytest.raises(NotImplementedError):
        _generate(seed=1, plane="xoz")
    with pytest.raises(NotImplementedError):
        _generate(seed=1, plane="yoz")
    with pytest.raises(ValueError, match="unsupported turns"):
        _generate(seed=1, allowed_turns=["straight", "back"])


def test_generator_rejects_invalid_initial_direction() -> None:
    with pytest.raises(ValueError, match="initial_direction"):
        generate_orthogonal_corridor_map(
            map_id="bad_direction",
            seed=1,
            num_segments=1,
            start=[0.0, 0.0, 0.0],
            initial_direction="z+",
            segment_length_range=(0.4, 0.8),
            track_width=0.2,
            z_tolerance=0.1,
            allowed_turns=["straight"],
        )


def _generate(
    *,
    seed: int,
    num_segments: int = 4,
    plane: str = "xoy",
    allowed_turns: list[str] | None = None,
):
    return generate_orthogonal_corridor_map(
        map_id=f"generated_{seed}",
        seed=seed,
        num_segments=num_segments,
        start=[0.0, 0.0, 0.0],
        initial_direction="x+",
        segment_length_range=(0.4, 0.8),
        track_width=0.2,
        z_tolerance=0.1,
        allowed_turns=allowed_turns or ["left", "right", "straight"],
        plane=plane,
        junction_overlap=0.03,
    )


def _has_volume_overlap_or_face_contact(previous, current) -> bool:
    overlaps = [
        min(previous.max[index], current.max[index])
        - max(previous.min[index], current.min[index])
        for index in range(3)
    ]
    positive_axes = sum(value > 1e-9 for value in overlaps)
    touching_axes = sum(abs(value) <= 1e-9 for value in overlaps)
    return positive_axes == 3 or (positive_axes == 2 and touching_axes == 1)


def _has_positive_volume_overlap(first, second) -> bool:
    overlaps = [
        min(first.max[index], second.max[index])
        - max(first.min[index], second.min[index])
        for index in range(3)
    ]
    return all(value > 1e-9 for value in overlaps)
