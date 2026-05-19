"""Derive haptic feedback signals from controller outputs."""

from __future__ import annotations

from config import EngineConfig
from data_models import (
    ContactState,
    FrameOutput,
    HapticEvent,
    HapticEventType,
    HapticFeedbackState,
    SlipReason,
    StopReason,
)


def build_haptic_output(
    frame_output: FrameOutput,
    previous_contact_state: ContactState,
    config: EngineConfig,
) -> tuple[tuple[HapticEvent, ...], HapticFeedbackState]:
    """Build discrete haptic events and continuous haptic state."""

    events = _build_events(frame_output, previous_contact_state)
    continuous_state = _build_continuous_state(frame_output, config)
    return events, continuous_state


def _build_events(
    frame_output: FrameOutput,
    previous_contact_state: ContactState,
) -> tuple[HapticEvent, ...]:
    if not frame_output.feedback_state.tracking_valid:
        return ()

    if frame_output.feedback_state.recovery_frame:
        return ()

    if (
        previous_contact_state == ContactState.OUTSIDE_BLOCK
        and frame_output.contact_state == ContactState.INSIDE_BLOCK
    ):
        return (
            HapticEvent(
                time=frame_output.time,
                event_type=HapticEventType.CONTACT_ENTER,
            ),
        )

    if (
        previous_contact_state == ContactState.INSIDE_BLOCK
        and frame_output.contact_state == ContactState.OUTSIDE_BLOCK
    ):
        return (
            HapticEvent(
                time=frame_output.time,
                event_type=HapticEventType.CONTACT_EXIT,
                detach_state=frame_output.feedback_state.detach_state,
            ),
        )

    return ()


def _build_continuous_state(
    frame_output: FrameOutput,
    config: EngineConfig,
) -> HapticFeedbackState:
    hand_delta = frame_output.feedback_state.hand_delta
    hand_delta_norm = hand_delta.norm() if hand_delta is not None else 0.0

    slip_reason = None
    if (
        frame_output.contact_state == ContactState.INSIDE_BLOCK
        and hand_delta is not None
        and hand_delta_norm > config.slip_motion_threshold
    ):
        if frame_output.feedback_state.stop_reason == StopReason.PINCH_INSUFFICIENT:
            slip_reason = SlipReason.PINCH_INSUFFICIENT
        elif frame_output.feedback_state.stop_reason == StopReason.TRACK_BLOCKED:
            slip_reason = SlipReason.TRACK_BLOCKED

    blocked_info = frame_output.feedback_state.blocked_info
    blocked_force_active = (
        frame_output.contact_state == ContactState.INSIDE_BLOCK
        and blocked_info is not None
        and frame_output.pinch_center_task is not None
        and frame_output.feedback_state.stop_reason == StopReason.TRACK_BLOCKED
    )

    force_vector = None
    force_magnitude = 0.0
    primary_surface = None
    primary_amount = 0.0
    if blocked_force_active and frame_output.pinch_center_task is not None and blocked_info is not None:
        force_vector = frame_output.block_state.center - frame_output.pinch_center_task
        force_magnitude = force_vector.norm()
        primary_surface = blocked_info.primary_blocked_surface
        primary_amount = blocked_info.primary_blocked_amount

    return HapticFeedbackState(
        slip_active=slip_reason is not None,
        slip_reason=slip_reason,
        blocked_force_active=blocked_force_active,
        force_vector_task=force_vector,
        force_magnitude=force_magnitude,
        primary_blocked_surface=primary_surface,
        primary_blocked_amount=primary_amount,
    )
