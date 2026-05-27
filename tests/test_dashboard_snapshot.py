"""Tests for display-only dashboard snapshots."""

from __future__ import annotations

import json
from types import SimpleNamespace

from dashboard_snapshot import build_dashboard_snapshot, make_main_state_label
from data_models import (
    BlockMotionState,
    BlockState,
    ContactState,
    FeedbackState,
    HapticFeedbackState,
    PinchState,
    SlipReason,
    StopReason,
    TrackState,
    Vec3,
)
from trial_controller import ExperimentInputSample


def test_contact_inside_label_contains_contact() -> None:
    snapshot = _snapshot(contact_state=ContactState.INSIDE_BLOCK)

    assert "CONTACT" in snapshot.contact_label
    assert "INSIDE_BLOCK" in snapshot.contact_label
    assert json.loads(snapshot.to_json())["contact_label"] == snapshot.contact_label


def test_outside_or_detach_label_contains_contact_release() -> None:
    outside = _snapshot(contact_state=ContactState.OUTSIDE_BLOCK)
    detach = _snapshot(contact_state=ContactState.INSIDE_BLOCK, detach_state="UNEXPECTED_DETACH")

    assert "CONTACT RELEASE" in outside.contact_label
    assert "CONTACT RELEASE" in detach.contact_label
    assert "UNEXPECTED_DETACH" in detach.contact_label


def test_slip_pinch_insufficient_feedback_label_is_readable() -> None:
    snapshot = _snapshot(
        slip_active=True,
        slip_reason=SlipReason.PINCH_INSUFFICIENT,
        stop_reason=StopReason.PINCH_INSUFFICIENT,
    )

    assert "SLIP / PINCH INSUFFICIENT" in snapshot.feedback_label
    assert snapshot.logical_haptic_label == "SLIP_PINCH_INSUFFICIENT(PINCH_INSUFFICIENT)"


def test_blocked_force_feedback_label_is_readable() -> None:
    snapshot = _snapshot(
        blocked_force_active=True,
        stop_reason=StopReason.TRACK_BLOCKED,
        motion_state=BlockMotionState.GRABBED_BLOCKED,
    )

    assert "BLOCKED FORCE" in snapshot.feedback_label
    assert snapshot.main_state_label == "BLOCKED"
    assert snapshot.logical_haptic_label == "BLOCKED_FORCE(TRACK_BLOCKED)"


def test_main_state_priority() -> None:
    assert _main(tracker_valid=False) == "TRACKING INVALID"
    assert _main(large_delta=True) == "LARGE DELTA"
    assert _main(blocked_force_active=True, stop_reason="TRACK_BLOCKED") == "BLOCKED"
    assert _main(slip_active=True) == "SLIP"
    assert _main(contact_state="OUTSIDE_BLOCK") == "CONTACT RELEASE"
    assert _main(block_motion_state="GRABBED_MOVING") == "MOVING"
    assert _main(pinch_state="PINCH_INSUFFICIENT") == "PINCH INSUFFICIENT"


def _main(**overrides) -> str:
    kwargs = {
        "tracker_valid": True,
        "stop_reason": "NONE",
        "large_delta": False,
        "blocked_force_active": False,
        "slip_active": False,
        "contact_state": "INSIDE_BLOCK",
        "detach_state": "NONE",
        "block_motion_state": "CONTACT_HIDDEN",
        "pinch_state": "PINCH_VALID",
    }
    kwargs.update(overrides)
    return make_main_state_label(**kwargs)


def _snapshot(
    *,
    contact_state=ContactState.INSIDE_BLOCK,
    detach_state="NONE",
    motion_state=BlockMotionState.CONTACT_HIDDEN,
    stop_reason=StopReason.NONE,
    slip_active: bool = False,
    slip_reason=None,
    blocked_force_active: bool = False,
):
    if isinstance(detach_state, str):
        feedback_state = FeedbackState(
            tracking_valid=True,
            recovery_frame=False,
            stop_reason=stop_reason,
            track_state=TrackState.INSIDE_TRACK,
        )
        object.__setattr__(feedback_state, "detach_state", SimpleNamespace(name=detach_state))
    else:
        feedback_state = FeedbackState(
            tracking_valid=True,
            recovery_frame=False,
            stop_reason=stop_reason,
            track_state=TrackState.INSIDE_TRACK,
            detach_state=detach_state,
        )
    frame_output = SimpleNamespace(
        time=1.0,
        pinch_center_task=Vec3(0.0, 0.0, 0.0),
        pinch_distance=0.02,
        block_state=BlockState(
            center=Vec3(0.0, 0.0, 0.0),
            size=Vec3(0.2, 0.2, 0.2),
            visible=True,
            motion_state=motion_state,
        ),
        contact_state=contact_state,
        pinch_state=PinchState.PINCH_VALID,
        feedback_state=feedback_state,
    )
    haptic = HapticFeedbackState(
        slip_active=slip_active,
        slip_reason=slip_reason,
        blocked_force_active=blocked_force_active,
    )
    result = SimpleNamespace(frame_output=frame_output, haptic_feedback_state=haptic)
    sample = ExperimentInputSample(
        time=1.0,
        pinch_distance=0.02,
        tracker_valid=True,
        pinch_center_world=[0.0, 0.0, 0.0],
        coordinate_space="world",
        metadata={"pinch_valid": True},
    )
    return build_dashboard_snapshot(
        frame_index=1,
        trial_result=result,
        sample=sample,
        hand_valid=True,
        map_id="map",
        calibration_id="cal",
        processing_latency_ms=1.2,
    )
