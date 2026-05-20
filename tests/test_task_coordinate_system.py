"""Tests for Stage 2 task coordinate conversion."""

from __future__ import annotations

import numpy as np
import pytest

from task_coordinate_system import build_from_origin_and_x_point


def test_origin_maps_to_zero() -> None:
    system = build_from_origin_and_x_point([1.0, 2.0, 3.0], [2.0, 2.0, 3.0], [0.0, 0.0, 1.0])
    assert np.allclose(system.world_to_task([1.0, 2.0, 3.0]), [0.0, 0.0, 0.0])


def test_axis_movements_map_to_task_axes() -> None:
    system = build_from_origin_and_x_point([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    assert np.allclose(system.world_to_task(system.x_axis_world), [1.0, 0.0, 0.0])
    assert np.allclose(system.world_to_task(system.y_axis_world), [0.0, 1.0, 0.0])
    assert np.allclose(system.world_to_task(system.z_axis_world), [0.0, 0.0, 1.0])


def test_task_to_world_round_trip() -> None:
    system = build_from_origin_and_x_point([1.0, -2.0, 0.5], [3.0, -1.0, 4.0], [0.0, 0.0, 1.0])
    point_world = np.array([2.0, 1.0, 3.0])
    assert np.allclose(system.task_to_world(system.world_to_task(point_world)), point_world)


def test_right_handed_axes() -> None:
    system = build_from_origin_and_x_point([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    assert np.allclose(np.cross(system.x_axis_world, system.y_axis_world), system.z_axis_world)


def test_x_point_too_close_raises() -> None:
    with pytest.raises(ValueError):
        build_from_origin_and_x_point(
            [0.0, 0.0, 0.0],
            [0.01, 0.0, 0.0],
            [0.0, 0.0, 1.0],
            min_x_axis_length=0.1,
        )


def test_x_axis_projection_removes_vertical_component() -> None:
    system = build_from_origin_and_x_point([0.0, 0.0, 0.0], [1.0, 0.0, 5.0], [0.0, 0.0, 1.0])
    assert np.allclose(system.x_axis_world, [1.0, 0.0, 0.0])
    assert np.allclose(system.world_to_task([1.0, 0.0, 5.0]), [1.0, 0.0, 5.0])
