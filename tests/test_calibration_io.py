"""Tests for task calibration JSON IO."""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from calibration_geometry import build_axes_from_table_lines
from calibration_io import (
    FormalCalibration,
    PlaneFitRecord,
    build_task_coordinate_system_from_calibration,
    load_calibration,
    load_task_calibration,
    load_task_calibration_metadata,
    save_calibration,
    save_task_calibration,
    validate_calibration,
)
from calibration_sampling import build_calibration_line_record, build_calibration_point_record
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


def test_formal_calibration_save_load_and_validate(tmp_path) -> None:
    calibration = _valid_formal_calibration()
    validation = validate_calibration(calibration)
    assert validation.is_valid
    assert validation.status == "ok"

    path = tmp_path / "formal_calibration.json"
    save_calibration(calibration, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["calibration_type"] == "formal_table_lines"
    assert payload["is_formal_calibration"] is True
    assert payload["origin"]["point_world"] == [0.0, 0.0, 0.0]
    assert payload["long_axis_line"]["line_fit_rmse_m"] == pytest.approx(0.0)

    loaded = load_calibration(path)
    assert loaded.calibration_id == calibration.calibration_id
    task_system = build_task_coordinate_system_from_calibration(loaded)
    assert np.allclose(task_system.world_to_task([1.0, 0.0, 0.0]), [1.0, 0.0, 0.0])


def test_formal_calibration_duration_too_short_errors() -> None:
    calibration = _valid_formal_calibration(
        origin_duration=0.5,
        long_duration=2.0,
    )
    validation = validate_calibration(calibration)
    assert any("origin_record.duration_seconds" in error for error in validation.errors)
    assert any("long_line.duration_seconds" in warning for warning in validation.warnings)


def test_formal_calibration_line_too_short_errors() -> None:
    calibration = _valid_formal_calibration()
    short_line = replace(calibration.long_line, line_length_m=0.01)
    calibration = replace(calibration, long_line=short_line)
    validation = validate_calibration(calibration)
    assert any("long_line.line_length_m" in error for error in validation.errors)


def test_formal_calibration_non_orthogonal_axes_error() -> None:
    calibration = replace(_valid_formal_calibration(), y_axis_world=[1.0, 0.0, 0.0])
    validation = validate_calibration(calibration)
    assert any("x/y axes" in error for error in validation.errors)


def _valid_formal_calibration(
    *,
    origin_duration: float = 9.0,
    long_duration: float = 9.0,
) -> FormalCalibration:
    origin_points = [[0.0, 0.0, 0.0] for _ in range(10)]
    long_points = [[index / 9.0, 0.0, 0.0] for index in range(10)]
    width_points = [[0.0, index / 9.0, 0.0] for index in range(10)]
    diagonal_points = [[index / 9.0, index / 9.0, 0.0] for index in range(10)]

    origin = build_calibration_point_record(
        "origin",
        origin_points,
        source="tracker_position_world",
        time_start=0.0,
        time_end=origin_duration,
        frame_start=0,
        frame_end=9,
        save_points=True,
    )
    long_line = build_calibration_line_record(
        "long_axis_line",
        long_points,
        source="tracker_position_world",
        time_start=10.0,
        time_end=10.0 + long_duration,
        frame_start=10,
        frame_end=19,
    )
    width_line = build_calibration_line_record(
        "width_axis_line",
        width_points,
        source="tracker_position_world",
        time_start=20.0,
        time_end=29.0,
        frame_start=20,
        frame_end=29,
    )
    diagonal_line = build_calibration_line_record(
        "diagonal_line",
        diagonal_points,
        source="tracker_position_world",
        time_start=30.0,
        time_end=39.0,
        frame_start=30,
        frame_end=39,
    )
    axes = build_axes_from_table_lines(
        origin.mean_world,
        long_line,
        width_line,
        diagonal_line,
        up_hint=[0.0, 0.0, 1.0],
    )
    plane_fit = PlaneFitRecord(
        centroid_world=axes["plane_fit"]["centroid_world"],
        normal_world=axes["plane_fit"]["normal_world"],
        rmse_m=axes["plane_fit"]["rmse_m"],
        plane_fit_rmse_m=axes["plane_fit"]["rmse_m"],
        max_abs_distance_m=axes["plane_fit"]["max_abs_distance_m"],
        sample_count=axes["plane_fit"]["sample_count"],
        source_labels=["long_axis_line", "width_axis_line", "diagonal_line"],
    )
    quality = {
        **axes["quality"],
        "origin_sample_count": origin.sample_count,
        "long_line_sample_count": long_line.sample_count,
        "width_line_sample_count": width_line.sample_count,
        "diagonal_line_sample_count": diagonal_line.sample_count,
        "origin_max_deviation_m": origin.max_deviation_m,
        "origin_std_world": origin.std_world,
        "calibration_quality_status": "ok",
    }
    return FormalCalibration(
        calibration_id="test_formal",
        created_at="2026-01-01T00:00:00+00:00",
        point_source="tracker_position_world",
        origin_world=origin.mean_world,
        x_axis_world=axes["x_axis_world"],
        y_axis_world=axes["y_axis_world"],
        z_axis_world=axes["z_axis_world"],
        up_axis_world=axes["up_axis_world"],
        origin_record=origin,
        long_line=long_line,
        width_line=width_line,
        diagonal_line=diagonal_line,
        plane_fit=plane_fit,
        quality=quality,
        metadata={"source": "unit_test"},
    )
