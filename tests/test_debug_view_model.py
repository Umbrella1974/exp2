"""Tests for pure-Python debug GUI view model helpers."""

from __future__ import annotations

from dashboard_snapshot import DashboardSnapshot
from debug_view_model import (
    DebugRuntimeStats,
    calculate_debug_view_range,
    scene_view_from_trial_config,
    snapshot_to_debug_view_model,
)


def test_scene_view_from_trial_config_uses_track_boxes_and_target() -> None:
    scene = scene_view_from_trial_config(
        {
            "map_id": "test_map",
            "block_initial_center_task": [0.0, 0.0, 0.0],
            "block_size": [0.2, 0.2, 0.2],
            "track_boxes": [
                {"id": "seg0", "min": [0.0, -0.1, -0.1], "max": [1.0, 0.1, 0.1], "order": 0}
            ],
            "target_region": {"id": "target", "min": [0.8, -0.1, -0.1], "max": [1.0, 0.1, 0.1]},
        }
    )

    assert scene.map_id == "test_map"
    assert scene.block_initial_center_task == [0.0, 0.0, 0.0]
    assert scene.track_boxes[0].box_id == "seg0"
    assert scene.target_region is not None
    assert scene.warnings == ()


def test_scene_view_from_trial_config_falls_back_to_track_bounds() -> None:
    scene = scene_view_from_trial_config(
        {
            "scene_auto": {
                "block_center_task": [0.1, 0.2, 0.0],
                "track_bounds": {"min": [-1.0, -0.5, -0.1], "max": [1.0, 0.5, 0.1]},
            },
            "block_size": 0.2,
        }
    )

    assert scene.block_initial_center_task == [0.1, 0.2, 0.0]
    assert scene.block_size == [0.2, 0.2, 0.2]
    assert scene.track_boxes[0].box_id == "track_bounds"


def test_snapshot_to_debug_view_model_computes_delta_and_status_text() -> None:
    scene = scene_view_from_trial_config(
        {
            "map_id": "test_map",
            "block_initial_center_task": [0.0, 0.0, 0.0],
            "block_size": [0.2, 0.2, 0.2],
            "track_boxes": [{"id": "track", "min": [-0.5, -0.5, -0.1], "max": [0.5, 0.5, 0.1]}],
        }
    )
    snapshot = _snapshot(
        pinch_center_task=[0.1, 0.2, 0.3],
        block_center_task=[0.0, 0.0, 0.0],
    )

    view_model = snapshot_to_debug_view_model(
        snapshot,
        scene=scene,
        runtime=DebugRuntimeStats(
            mode="replay",
            snapshot_age_seconds=0.05,
            gui_fps=30.0,
            overwritten_snapshot_count=2,
            raw_dropped_frame_count=1,
        ),
    )

    assert view_model.delta_task == [0.1, 0.2, 0.3]
    assert view_model.distance_to_block_center is not None
    assert view_model.main_state_label == "MOVING"
    assert any(line == "mode: replay" for line in view_model.status_lines)
    assert any("snapshot_age" in line for line in view_model.status_lines)
    assert "overwritten snapshots: 2" in view_model.status_lines
    assert "raw_dropped_frames: 1" in view_model.status_lines
    assert all(not line.startswith("dropped_frames:") for line in view_model.status_lines)


def test_calculate_debug_view_range_includes_map_block_and_pinch() -> None:
    scene = scene_view_from_trial_config(
        {
            "block_initial_center_task": [0.0, 0.0, 0.0],
            "block_size": [0.2, 0.2, 0.2],
            "track_boxes": [{"id": "track", "min": [0.0, 0.0, -0.1], "max": [1.0, 1.0, 0.1]}],
        }
    )

    view_range = calculate_debug_view_range(
        scene=scene,
        pinch_center_task=[2.0, 0.0, 0.0],
        block_center_task=[0.5, 0.5, 0.0],
        block_size=[0.2, 0.2, 0.2],
    )

    assert view_range.x_min < 0.0
    assert view_range.x_max > 2.0
    assert view_range.y_min < 0.0
    assert view_range.y_max > 1.0


def _snapshot(
    *,
    pinch_center_task: list[float] | None = None,
    block_center_task: list[float] | None = None,
) -> DashboardSnapshot:
    return DashboardSnapshot(
        frame_index=7,
        time=1.23,
        tracker_valid=True,
        hand_valid=True,
        pinch_valid=True,
        pinch_distance=0.02,
        pinch_center_task=pinch_center_task,
        block_center_task=block_center_task,
        block_size=[0.2, 0.2, 0.2],
        contact_state="INSIDE_BLOCK",
        block_motion_state="GRABBED_MOVING",
        stop_reason="NONE",
        track_state="INSIDE_TRACK",
        pinch_state="PINCH_VALID",
        detach_state="NONE",
        large_delta=False,
        slip_active=False,
        slip_reason="",
        blocked_force_active=False,
        logical_haptic_active=False,
        logical_haptic_label="NONE",
        hardware_haptic_active=False,
        map_id="test_map",
        calibration_id="test_cal",
        processing_latency_ms=1.0,
        contact_label="CONTACT (INSIDE_BLOCK)",
        release_label="NO RELEASE",
        interaction_label="GRABBED / MOVING (GRABBED_MOVING)",
        feedback_label="FEEDBACK: NONE",
        status_line="MAIN=MOVING",
        main_state_label="MOVING",
        pinch_label="PINCH VALID, distance=0.020 m",
    )
