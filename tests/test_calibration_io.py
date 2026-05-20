"""Tests for task calibration JSON IO."""

from __future__ import annotations

import json

import numpy as np

from calibration_io import (
    load_task_calibration,
    load_task_calibration_metadata,
    save_task_calibration,
)
from task_coordinate_system import build_from_origin_and_x_point


def test_save_and_load_task_calibration_round_trip(tmp_path) -> None:
    system = build_from_origin_and_x_point([1.0, 2.0, 3.0], [2.0, 2.0, 3.0], [0.0, 0.0, 1.0])
    path = tmp_path / "calibration.json"
    save_task_calibration(path, system, metadata={"up_axis_world": [0.0, 0.0, 1.0]})
    loaded = load_task_calibration(path)
    point = [1.5, 3.0, 4.0]
    assert np.allclose(loaded.world_to_task(point), system.world_to_task(point))


def test_metadata_preserves_up_axis_world(tmp_path) -> None:
    system = build_from_origin_and_x_point([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    path = tmp_path / "calibration.json"
    save_task_calibration(path, system, metadata={"up_axis_world": [0.0, 0.0, 1.0]})
    assert load_task_calibration_metadata(path)["up_axis_world"] == [0.0, 0.0, 1.0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["metadata"]["up_axis_world"] == [0.0, 0.0, 1.0]
