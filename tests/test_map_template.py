"""Tests for template-aligned map generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from map_config import validate_map_config
from map_template import (
    estimate_main_direction_from_points,
    load_map_template,
    transform_template_to_map_config,
)


def test_load_map_template(tmp_path: Path) -> None:
    path = _write_template(tmp_path)

    template = load_map_template(path)

    assert template.template_id == "template_l"
    assert template.coordinate_space == "template"
    assert template.anchor_direction == "x+"
    assert len(template.track_boxes) == 2
    assert template.target_region is not None


def test_estimate_main_direction_snaps_to_axis() -> None:
    info = estimate_main_direction_from_points(
        [
            [0.0, 0.0, 0.0],
            [0.1, 0.3, 0.0],
            [0.2, 0.6, 0.0],
        ],
        n_frames=3,
    )

    assert info.snapped_main_direction == "y+"
    assert info.raw_main_direction == pytest.approx([0.316227766, 0.948683298, 0.0])
    assert info.snap_angle_degrees > 0.0


def test_transform_template_to_map_config_validates(tmp_path: Path) -> None:
    template = load_map_template(_write_template(tmp_path))
    info = estimate_main_direction_from_points(
        [[10.0, 20.0, 0.0], [10.5, 20.0, 0.0]],
        n_frames=2,
    )

    generated = transform_template_to_map_config(
        template,
        [10.0, 20.0, 0.0],
        info.snapped_main_direction,
        direction_info=info,
    )

    assert validate_map_config(generated).is_valid
    assert generated.map_id == "template_l"
    assert generated.coordinate_space == "task"
    assert generated.block_initial_center_task == pytest.approx([10.0, 20.0, 0.0])
    assert generated.metadata["generated"] is True
    assert generated.metadata["template_id"] == "template_l"
    assert generated.metadata["raw_main_direction"] == pytest.approx([1.0, 0.0, 0.0])
    assert generated.metadata["snapped_main_direction"] == "x+"
    assert generated.target_region is not None


def test_different_snapped_directions_generate_different_layouts(tmp_path: Path) -> None:
    template = load_map_template(_write_template(tmp_path))

    x_map = transform_template_to_map_config(template, [0.0, 0.0, 0.0], "x+")
    y_map = transform_template_to_map_config(template, [0.0, 0.0, 0.0], "y+")

    assert validate_map_config(x_map).is_valid
    assert validate_map_config(y_map).is_valid
    assert x_map.track_boxes[0].max[0] == pytest.approx(1.0)
    assert y_map.track_boxes[0].max[1] == pytest.approx(1.0)
    assert x_map.track_boxes[0].max[0] != pytest.approx(y_map.track_boxes[0].max[0])


def _write_template(tmp_path: Path) -> Path:
    path = tmp_path / "template_l.json"
    path.write_text(json.dumps(_template_payload(), indent=2), encoding="utf-8")
    return path


def _template_payload() -> dict:
    return {
        "template_id": "template_l",
        "description": "Simple L template.",
        "coordinate_space": "template",
        "unit": "m",
        "anchor_direction": "x+",
        "block_initial_center_template": [0.0, 0.0, 0.0],
        "block_size": [0.2, 0.2, 0.2],
        "track_boxes": [
            {
                "id": "segment_00",
                "order": 0,
                "label": "main",
                "min": [0.0, -0.2, -0.1],
                "max": [1.0, 0.2, 0.1],
                "metadata": {"direction": "x+"},
            },
            {
                "id": "segment_01",
                "order": 1,
                "label": "turn",
                "min": [0.8, 0.0, -0.1],
                "max": [1.2, 0.8, 0.1],
                "metadata": {"direction": "y+"},
            },
        ],
        "target_region": {
            "id": "target",
            "label": "target",
            "min": [0.8, 0.6, -0.1],
            "max": [1.2, 0.8, 0.1],
            "metadata": {"type": "target_region"},
        },
        "metadata": {"source": "test"},
    }
