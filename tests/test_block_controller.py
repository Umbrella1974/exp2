"""Tests for the block controller and haptic derivation."""

from __future__ import annotations

import pytest

from block_controller import BlockController
from config import EngineConfig
from data_models import (
    BlockMotionState,
    Box3D,
    ContactState,
    DetachState,
    FrameInput,
    HapticEventType,
    PinchState,
    SlipReason,
    StopReason,
    Surface,
    TrackRegion,
    TrackState,
    Vec3,
)


def make_config(**overrides: float) -> EngineConfig:
    defaults = {
        "block_size_x": 1.0,
        "block_size_y": 1.0,
        "block_size_z": 1.0,
        "track_epsilon": 1e-6,
        "pinch_grab_threshold": 0.02,
        "pinch_release_threshold": 0.04,
        "max_hand_delta_per_frame": 0.5,
        "min_block_move_distance": 1e-5,
        "blocked_feedback_threshold": 0.01,
        "binary_search_iterations": 40,
        "slip_motion_threshold": 0.05,
    }
    defaults.update(overrides)
    return EngineConfig(**defaults)


def make_track(size: float = 4.0) -> TrackRegion:
    return TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(size, size, size)),))


def make_face_touch_track() -> TrackRegion:
    return TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
            Box3D(center=Vec3(2.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
        )
    )


def make_overlap_track() -> TrackRegion:
    return TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
            Box3D(center=Vec3(1.5, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
        )
    )


def make_input(
    time: float,
    x: float,
    *,
    distance: float = 0.01,
    tracker_valid: bool = True,
) -> FrameInput:
    return FrameInput(
        time=time,
        pinch_center_task=Vec3(x, 0.0, 0.0),
        pinch_distance=distance,
        tracker_valid=tracker_valid,
    )


def test_pinch_center_enters_box_hides_block() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    output = controller.update(make_input(0.0, 0.0))
    assert output.contact_state == ContactState.INSIDE_BLOCK
    assert output.block_state.visible is False
    assert output.block_state.motion_state == BlockMotionState.CONTACT_HIDDEN
    assert output.events[0].event_type == HapticEventType.CONTACT_ENTER


def test_pinch_insufficient_hides_block_but_does_not_move_and_emits_slip() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0, distance=0.01))
    output = controller.update(make_input(1.0, 0.2, distance=0.06))
    assert output.block_state.center == Vec3(0.0, 0.0, 0.0)
    assert output.block_state.visible is False
    assert output.block_state.motion_state == BlockMotionState.GRABBED_PINCH_INSUFFICIENT
    assert output.feedback_state.stop_reason == StopReason.PINCH_INSUFFICIENT
    assert output.haptic_feedback.slip_active is True
    assert output.haptic_feedback.slip_reason == SlipReason.PINCH_INSUFFICIENT


def test_pinch_valid_moves_block_by_hand_delta() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0))
    output = controller.update(make_input(1.0, 0.2))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_MOVING
    assert output.block_state.center.x == pytest.approx(0.2)


def test_block_can_move_across_face_touch_track_union() -> None:
    controller = BlockController(
        make_config(max_hand_delta_per_frame=0.5),
        make_face_touch_track(),
        Vec3(0.5, 0.0, 0.0),
    )
    controller.update(make_input(0.0, 0.5))
    controller.update(make_input(1.0, 1.0))
    controller.update(make_input(2.0, 1.5))
    controller.update(make_input(3.0, 2.0))
    output = controller.update(make_input(4.0, 2.5))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_MOVING
    assert output.block_state.center.x == pytest.approx(2.5)


def test_block_can_move_across_overlap_track_union() -> None:
    controller = BlockController(
        make_config(max_hand_delta_per_frame=0.5),
        make_overlap_track(),
        Vec3(0.0, 0.0, 0.0),
    )
    controller.update(make_input(0.0, 0.0))
    controller.update(make_input(1.0, 0.5))
    controller.update(make_input(2.0, 1.0))
    controller.update(make_input(3.0, 1.5))
    output = controller.update(make_input(4.0, 2.0))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_MOVING
    assert output.block_state.center.x == pytest.approx(2.0)


def test_candidate_outside_track_moves_to_last_legal_boundary_point() -> None:
    controller = BlockController(make_config(), make_track(size=2.0), Vec3(0.7, 0.0, 0.0))
    controller.update(make_input(0.0, 0.7))
    output = controller.update(make_input(1.0, 1.1))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_BLOCKED
    assert output.block_state.center.x == pytest.approx(1.0, abs=1e-4)
    assert output.feedback_state.track_state == TrackState.BLOCKED_X_POS
    assert output.feedback_state.blocked_info is not None
    assert output.feedback_state.blocked_info.primary_blocked_surface == Surface.X_POS
    assert output.haptic_feedback.slip_active is True
    assert output.haptic_feedback.slip_reason == SlipReason.TRACK_BLOCKED
    assert output.haptic_feedback.blocked_force_active is True
    assert output.haptic_feedback.primary_blocked_surface == Surface.X_POS


def test_pinch_insufficient_without_enough_motion_does_not_trigger_slip() -> None:
    controller = BlockController(make_config(slip_motion_threshold=0.05), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0, distance=0.01))
    output = controller.update(make_input(1.0, 0.03, distance=0.06))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_PINCH_INSUFFICIENT
    assert output.haptic_feedback.slip_active is False
    assert output.haptic_feedback.slip_reason is None


def test_blocked_then_hand_moves_back_can_resume_moving() -> None:
    controller = BlockController(make_config(), make_track(size=2.0), Vec3(0.7, 0.0, 0.0))
    controller.update(make_input(0.0, 0.7))
    controller.update(make_input(1.0, 1.1))
    output = controller.update(make_input(2.0, 0.9))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_MOVING
    assert output.block_state.center.x == pytest.approx(0.8, abs=1e-4)


def test_large_hand_delta_stops_motion() -> None:
    controller = BlockController(
        make_config(max_hand_delta_per_frame=0.25),
        make_track(),
        Vec3(0.0, 0.0, 0.0),
    )
    controller.update(make_input(0.0, 0.0))
    output = controller.update(make_input(1.0, 0.3))
    assert output.block_state.motion_state == BlockMotionState.STOPPED_BY_LARGE_DELTA
    assert output.block_state.center == Vec3(0.0, 0.0, 0.0)
    assert output.feedback_state.stop_reason == StopReason.LARGE_DELTA
    assert output.feedback_state.track_state == TrackState.HAND_DELTA_TOO_LARGE


def test_track_blocked_without_enough_motion_does_not_trigger_slip() -> None:
    controller = BlockController(
        make_config(slip_motion_threshold=0.05, max_hand_delta_per_frame=0.5),
        make_track(size=2.0),
        Vec3(0.98, 0.0, 0.0),
    )
    controller.update(make_input(0.0, 0.98))
    output = controller.update(make_input(1.0, 1.01))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_BLOCKED
    assert output.feedback_state.stop_reason == StopReason.TRACK_BLOCKED
    assert output.haptic_feedback.slip_active is False
    assert output.haptic_feedback.slip_reason is None


def test_stage_one_never_emits_slip_reason_both() -> None:
    pinch_controller = BlockController(make_config(slip_motion_threshold=0.05), make_track(), Vec3(0.0, 0.0, 0.0))
    pinch_controller.update(make_input(0.0, 0.0, distance=0.01))
    pinch_output = pinch_controller.update(make_input(1.0, 0.2, distance=0.06))

    track_controller = BlockController(
        make_config(slip_motion_threshold=0.05, max_hand_delta_per_frame=0.5),
        make_track(size=2.0),
        Vec3(0.7, 0.0, 0.0),
    )
    track_controller.update(make_input(0.0, 0.7))
    track_output = track_controller.update(make_input(1.0, 1.1))

    assert pinch_output.haptic_feedback.slip_reason != SlipReason.BOTH
    assert track_output.haptic_feedback.slip_reason != SlipReason.BOTH


def test_inside_to_outside_edge_triggers_detach_once() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0))
    detach_output = controller.update(make_input(1.0, 0.6))
    outside_output = controller.update(make_input(2.0, 0.7))
    assert detach_output.feedback_state.detach_state == DetachState.UNEXPECTED_DETACH
    assert detach_output.detach_counts.total_detach_count == 1
    assert outside_output.detach_counts.total_detach_count == 1
    assert outside_output.events == ()


def test_active_release_classification() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0))
    controller.update(make_input(1.0, 0.2, distance=0.06))
    output = controller.update(make_input(2.0, 0.7, distance=0.06))
    assert output.feedback_state.detach_state == DetachState.ACTIVE_RELEASE
    assert output.detach_counts.active_release_count == 1
    assert output.events[0].event_type == HapticEventType.CONTACT_EXIT


def test_forced_detach_classification() -> None:
    controller = BlockController(make_config(), make_track(size=2.0), Vec3(0.7, 0.0, 0.0))
    controller.update(make_input(0.0, 0.7))
    controller.update(make_input(1.0, 1.1))
    output = controller.update(make_input(2.0, 1.6))
    assert output.feedback_state.detach_state == DetachState.FORCED_DETACH
    assert output.detach_counts.forced_detach_count == 1


def test_unexpected_detach_classification_after_normal_motion() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0))
    controller.update(make_input(1.0, 0.2))
    output = controller.update(make_input(2.0, 0.8))
    assert output.feedback_state.detach_state == DetachState.UNEXPECTED_DETACH
    assert output.detach_counts.unexpected_detach_count == 1


def test_tracking_recovery_frame_resets_reference_without_contact_event_or_motion() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0, tracker_valid=False))
    recovery = controller.update(make_input(1.0, 0.0, tracker_valid=True))
    resumed = controller.update(make_input(2.0, 0.2, tracker_valid=True))
    assert recovery.feedback_state.recovery_frame is True
    assert recovery.events == ()
    assert recovery.block_state.center == Vec3(0.0, 0.0, 0.0)
    assert resumed.events == ()
    assert resumed.block_state.motion_state == BlockMotionState.GRABBED_MOVING
    assert resumed.block_state.center.x == pytest.approx(0.2)


def test_tracking_invalid_keeps_state_and_recovery_suppresses_exit_until_next_frame() -> None:
    controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    controller.update(make_input(0.0, 0.0))
    previous_reference = controller._state.previous_pinch_center

    invalid = controller.update(make_input(1.0, 0.8, tracker_valid=False))
    assert invalid.block_state.center == Vec3(0.0, 0.0, 0.0)
    assert invalid.contact_state == ContactState.INSIDE_BLOCK
    assert invalid.pinch_state == PinchState.PINCH_UNKNOWN
    assert invalid.feedback_state.stop_reason == StopReason.TRACKING_INVALID
    assert invalid.feedback_state.detach_state == DetachState.NONE
    assert invalid.events == ()
    assert controller._state.previous_pinch_center == previous_reference

    recovery = controller.update(make_input(2.0, 0.8, tracker_valid=True))
    assert recovery.feedback_state.recovery_frame is True
    assert recovery.contact_state == ContactState.OUTSIDE_BLOCK
    assert recovery.events == ()
    assert recovery.detach_counts.total_detach_count == 0
    assert recovery.block_state.center == Vec3(0.0, 0.0, 0.0)

    reenter = controller.update(make_input(3.0, 0.0, tracker_valid=True))
    assert reenter.events[0].event_type == HapticEventType.CONTACT_ENTER
    assert reenter.feedback_state.detach_state == DetachState.NONE


def test_clamped_motion_below_min_block_move_distance_keeps_block_center() -> None:
    controller = BlockController(
        make_config(min_block_move_distance=0.05, max_hand_delta_per_frame=0.5),
        make_track(size=2.0),
        Vec3(0.98, 0.0, 0.0),
    )
    controller.update(make_input(0.0, 0.98))
    output = controller.update(make_input(1.0, 1.02))
    assert output.block_state.motion_state == BlockMotionState.GRABBED_BLOCKED
    assert output.block_state.center == Vec3(0.98, 0.0, 0.0)
    assert output.feedback_state.stop_reason == StopReason.TRACK_BLOCKED
    assert output.feedback_state.blocked_info is not None
    assert output.feedback_state.blocked_info.primary_blocked_surface == Surface.X_POS


def test_stage_one_uses_stop_reason_instead_of_deprecated_stopped_motion_states() -> None:
    pinch_controller = BlockController(make_config(), make_track(), Vec3(0.0, 0.0, 0.0))
    pinch_controller.update(make_input(0.0, 0.0))
    pinch_output = pinch_controller.update(make_input(1.0, 0.2, distance=0.06))

    track_controller = BlockController(make_config(), make_track(size=2.0), Vec3(0.7, 0.0, 0.0))
    track_controller.update(make_input(0.0, 0.7))
    track_output = track_controller.update(make_input(1.0, 1.1))

    assert pinch_output.block_state.motion_state == BlockMotionState.GRABBED_PINCH_INSUFFICIENT
    assert pinch_output.block_state.motion_state != BlockMotionState.STOPPED_BY_PINCH
    assert pinch_output.feedback_state.stop_reason == StopReason.PINCH_INSUFFICIENT

    assert track_output.block_state.motion_state == BlockMotionState.GRABBED_BLOCKED
    assert track_output.block_state.motion_state != BlockMotionState.STOPPED_BY_TRACK
    assert track_output.feedback_state.stop_reason == StopReason.TRACK_BLOCKED
