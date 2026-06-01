"""Tests for formal calibration geometry helpers."""

from __future__ import annotations

import numpy as np
import pytest

from calibration_geometry import (
    angle_degrees,
    build_axes_from_table_lines,
    fit_line_3d,
    fit_plane_3d,
    project_vector_to_plane,
)


def test_fit_line_3d_recovers_direction_and_length() -> None:
    points = [[0.0, 0.0, 0.0], [0.5, 1.0, 0.0], [1.0, 2.0, 0.0]]
    fit = fit_line_3d(points)
    expected = np.asarray([1.0, 2.0, 0.0], dtype=float)
    expected = expected / np.linalg.norm(expected)
    assert abs(float(np.dot(fit["direction_world"], expected))) > 0.999
    assert fit["rmse_m"] < 1e-12
    assert fit["line_length_m"] > 2.0


def test_fit_plane_3d_recovers_normal() -> None:
    fit = fit_plane_3d(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
        ]
    )
    assert abs(float(np.dot(fit["normal_world"], [0.0, 0.0, 1.0]))) > 0.999
    assert fit["rmse_m"] < 1e-12


def test_project_vector_to_plane_and_angle() -> None:
    projected = project_vector_to_plane([1.0, 0.0, 1.0], [0.0, 0.0, 1.0])
    assert np.allclose(projected, [1.0, 0.0, 0.0])
    assert angle_degrees([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(90.0)


def test_build_axes_from_table_lines_outputs_right_handed_axes() -> None:
    axes = build_axes_from_table_lines(
        [0.0, 0.0, 0.0],
        {"points_world": [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [1.0, 0.0, 0.0]]},
        {"points_world": [[0.0, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 1.0, 0.0]]},
        {"points_world": [[0.0, 0.0, 0.0], [0.5, 0.5, 0.0], [1.0, 1.0, 0.0]]},
        up_hint=[0.0, 0.0, 1.0],
    )
    assert np.allclose(axes["x_axis_world"], [1.0, 0.0, 0.0])
    assert np.allclose(axes["y_axis_world"], [0.0, 1.0, 0.0])
    assert np.allclose(axes["up_axis_world"], [0.0, 0.0, 1.0])
    assert axes["quality"]["x_y_angle_degrees"] == pytest.approx(90.0)
    assert axes["quality"]["long_line_motion_direction_world"] == pytest.approx([1.0, 0.0, 0.0])
    assert axes["quality"]["long_line_direction_flipped_to_match_motion"] is False
    assert abs(axes["quality"]["width_line_dot_y_axis"]) == pytest.approx(1.0)
    width_angle = axes["quality"]["width_line_angle_to_y_axis_degrees"]
    assert min(abs(width_angle - 0.0), abs(width_angle - 180.0)) < 1e-6
    assert isinstance(axes["quality"]["width_line_direction_matches_y_positive"], bool)


def test_build_axes_flips_long_line_direction_to_match_motion() -> None:
    axes = build_axes_from_table_lines(
        [0.0, 0.0, 0.0],
        {
            "direction_world": [1.0, 0.0, 0.0],
            "points_world": [[1.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.0, 0.0, 0.0]],
            "line_length_m": 1.0,
            "rmse_m": 0.0,
            "sample_count": 3,
        },
        {"points_world": [[0.0, 0.0, 0.0], [0.0, -0.5, 0.0], [0.0, -1.0, 0.0]]},
        {"points_world": [[0.0, 0.0, 0.0], [0.5, -0.5, 0.0], [1.0, -1.0, 0.0]]},
        up_hint=[0.0, 0.0, 1.0],
    )

    assert np.allclose(axes["x_axis_world"], [-1.0, 0.0, 0.0])
    assert axes["quality"]["long_line_motion_direction_world"] == pytest.approx([-1.0, 0.0, 0.0])
    assert axes["quality"]["long_line_fitted_direction_world"] == pytest.approx([1.0, 0.0, 0.0])
    assert axes["quality"]["long_line_direction_flipped_to_match_motion"] is True


def test_build_axes_from_table_lines_accepts_fit_only_records() -> None:
    axes = build_axes_from_table_lines(
        [0.0, 0.0, 0.0],
        {
            "centroid_world": [0.5, 0.0, 0.0],
            "direction_world": [1.0, 0.0, 0.0],
            "line_length_m": 1.0,
            "rmse_m": 0.0,
            "sample_count": 10,
        },
        {
            "centroid_world": [0.0, 0.5, 0.0],
            "direction_world": [0.0, 1.0, 0.0],
            "line_length_m": 1.0,
            "rmse_m": 0.0,
            "sample_count": 10,
        },
        {
            "centroid_world": [0.5, 0.5, 0.0],
            "direction_world": [1.0, 1.0, 0.0],
            "line_length_m": 1.4,
            "rmse_m": 0.0,
            "sample_count": 10,
        },
        up_hint=[0.0, 0.0, 1.0],
    )
    assert np.allclose(axes["x_axis_world"], [1.0, 0.0, 0.0])
    assert np.allclose(axes["up_axis_world"], [0.0, 0.0, 1.0])


def test_project_parallel_vector_to_plane_fails() -> None:
    with pytest.raises(ValueError, match="zero-length"):
        project_vector_to_plane([0.0, 0.0, 1.0], [0.0, 0.0, 1.0])
