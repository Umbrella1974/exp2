"""Display-only labels for live trial status.

This module translates existing TrialController outputs into JSON-safe labels.
It does not change controller, haptic, or trial semantics.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class DashboardSnapshot:
    """One display snapshot consumed by text/GUI subscribers."""

    frame_index: int
    time: float | None
    tracker_valid: bool
    hand_valid: bool
    pinch_valid: bool
    pinch_distance: float | None
    pinch_center_task: list[float] | None
    block_center_task: list[float] | None
    block_size: list[float] | None
    contact_state: str
    block_motion_state: str
    block_visible: bool
    stop_reason: str
    track_state: str
    pinch_state: str
    detach_state: str
    large_delta: bool
    slip_active: bool
    slip_reason: str
    blocked_force_active: bool
    logical_haptic_active: bool
    logical_haptic_label: str
    hardware_haptic_active: bool
    map_id: str
    calibration_id: str
    processing_latency_ms: float | None
    contact_label: str
    release_label: str
    interaction_label: str
    feedback_label: str
    status_line: str
    main_state_label: str
    pinch_label: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary."""

        return asdict(self)

    def to_json(self) -> str:
        """Return a JSON string for logging/debugging."""

        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def build_dashboard_snapshot(
    *,
    frame_index: int,
    trial_result: Any,
    sample: Any,
    hand_valid: bool,
    map_id: str,
    calibration_id: str,
    processing_latency_ms: float | None,
    hardware_haptic_active: bool = False,
) -> DashboardSnapshot:
    """Build a display snapshot from an existing TrialFrameResult."""

    output = trial_result.frame_output
    haptic = trial_result.haptic_feedback_state
    feedback = output.feedback_state
    block_state = output.block_state

    contact_state = _name(output.contact_state)
    block_motion_state = _name(block_state.motion_state)
    stop_reason = _name(feedback.stop_reason) or "NONE"
    track_state = _name(feedback.track_state) or "INSIDE_TRACK"
    pinch_state = _name(output.pinch_state)
    detach_state = _name(feedback.detach_state) or "NONE"
    slip_reason = _name(haptic.slip_reason)
    large_delta = stop_reason == "LARGE_DELTA"
    blocked_force_active = bool(haptic.blocked_force_active)
    slip_active = bool(haptic.slip_active)
    logical_label = logical_haptic_label(
        slip_active=slip_active,
        slip_reason=slip_reason,
        blocked_force_active=blocked_force_active,
    )
    logical_active = logical_label != "NONE"
    pinch_distance = _optional_float(getattr(sample, "pinch_distance", None))
    pinch_valid = bool(_metadata_value(sample, "pinch_valid"))
    tracker_valid = bool(getattr(sample, "tracker_valid", False))
    contact_label = make_contact_label(contact_state, detach_state)
    release_label = make_release_label(contact_state, detach_state)
    interaction_label = make_interaction_label(block_motion_state, stop_reason, track_state)
    feedback_label = make_feedback_label(
        slip_active=slip_active,
        slip_reason=slip_reason,
        blocked_force_active=blocked_force_active,
    )
    pinch_label = make_pinch_label(pinch_state, pinch_distance)
    main_state = make_main_state_label(
        tracker_valid=tracker_valid,
        stop_reason=stop_reason,
        large_delta=large_delta,
        blocked_force_active=blocked_force_active,
        slip_active=slip_active,
        contact_state=contact_state,
        detach_state=detach_state,
        block_motion_state=block_motion_state,
        pinch_state=pinch_state,
    )
    status_line = (
        f"MAIN={main_state} CONTACT={contact_label} PINCH={pinch_label} "
        f"MOTION={interaction_label} STOP={stop_reason} FEEDBACK={logical_label}"
    )
    return DashboardSnapshot(
        frame_index=int(frame_index),
        time=_optional_float(getattr(sample, "time", None)),
        tracker_valid=tracker_valid,
        hand_valid=bool(hand_valid),
        pinch_valid=pinch_valid,
        pinch_distance=pinch_distance,
        pinch_center_task=_vec_to_list(output.pinch_center_task),
        block_center_task=_vec_to_list(block_state.center),
        block_size=_vec_to_list(block_state.size),
        contact_state=contact_state,
        block_motion_state=block_motion_state,
        block_visible=bool(block_state.visible),
        stop_reason=stop_reason,
        track_state=track_state,
        pinch_state=pinch_state,
        detach_state=detach_state,
        large_delta=large_delta,
        slip_active=slip_active,
        slip_reason=slip_reason,
        blocked_force_active=blocked_force_active,
        logical_haptic_active=logical_active,
        logical_haptic_label=logical_label,
        hardware_haptic_active=bool(hardware_haptic_active),
        map_id=str(map_id),
        calibration_id=str(calibration_id),
        processing_latency_ms=processing_latency_ms,
        contact_label=contact_label,
        release_label=release_label,
        interaction_label=interaction_label,
        feedback_label=feedback_label,
        status_line=status_line,
        main_state_label=main_state,
        pinch_label=pinch_label,
    )


def build_compact_status_line(phase: str, snapshot: DashboardSnapshot) -> str:
    """Build a compact one-line text display for live sessions."""

    distance = "NA" if snapshot.pinch_distance is None else f"{snapshot.pinch_distance:.3f}m"
    return (
        f"PHASE={phase} "
        f"frame={snapshot.frame_index} "
        f"tracker={int(snapshot.tracker_valid)} hand={int(snapshot.hand_valid)} "
        f"pinch={int(snapshot.pinch_valid)} dist={distance} "
        f"CONTACT={_compact(snapshot.contact_label)} "
        f"MOTION={snapshot.block_motion_state} "
        f"STOP={snapshot.stop_reason} "
        f"FEEDBACK={snapshot.logical_haptic_label} "
        f"block={_format_point(snapshot.block_center_task)} "
        f"pinch_task={_format_point(snapshot.pinch_center_task)}"
    )


def logical_haptic_label(
    *,
    slip_active: bool,
    slip_reason: str | None,
    blocked_force_active: bool,
) -> str:
    """Return compact readable logical feedback label."""

    reason = str(slip_reason or "")
    if blocked_force_active:
        return "BLOCKED_FORCE(TRACK_BLOCKED)"
    if slip_active and reason == "TRACK_BLOCKED":
        return "SLIP_TRACK_BLOCKED(TRACK_BLOCKED)"
    if slip_active and reason == "PINCH_INSUFFICIENT":
        return "SLIP_PINCH_INSUFFICIENT(PINCH_INSUFFICIENT)"
    if slip_active:
        return "SLIP"
    return "NONE"


def make_feedback_label(
    *,
    slip_active: bool,
    slip_reason: str | None,
    blocked_force_active: bool,
) -> str:
    """Return full feedback text label for dashboard display."""

    reason = str(slip_reason or "")
    if blocked_force_active:
        return "FEEDBACK: BLOCKED FORCE (TRACK_BLOCKED)"
    if slip_active and reason == "TRACK_BLOCKED":
        return "FEEDBACK: SLIP / TRACK BLOCKED (TRACK_BLOCKED)"
    if slip_active and reason == "PINCH_INSUFFICIENT":
        return "FEEDBACK: SLIP / PINCH INSUFFICIENT (PINCH_INSUFFICIENT)"
    if slip_active:
        return "FEEDBACK: SLIP"
    return "FEEDBACK: NONE"


def make_contact_label(contact_state: str, detach_state: str) -> str:
    """Return display text for contact/release state."""

    if detach_state and detach_state != "NONE":
        return f"CONTACT RELEASE ({detach_state})"
    if contact_state == "INSIDE_BLOCK":
        return "CONTACT (INSIDE_BLOCK)"
    return f"CONTACT RELEASE ({contact_state or 'OUTSIDE_BLOCK'})"


def make_release_label(contact_state: str, detach_state: str) -> str:
    """Return explicit release label."""

    if detach_state and detach_state != "NONE":
        return f"CONTACT RELEASE ({detach_state})"
    if contact_state == "OUTSIDE_BLOCK":
        return "CONTACT RELEASE (OUTSIDE_BLOCK)"
    return "NO RELEASE"


def make_interaction_label(block_motion_state: str, stop_reason: str, track_state: str) -> str:
    """Return readable motion/interaction text."""

    if block_motion_state == "GRABBED_MOVING":
        return "GRABBED / MOVING (GRABBED_MOVING)"
    if block_motion_state == "GRABBED_BLOCKED":
        return f"BLOCKED: TRACK WALL ({stop_reason or 'TRACK_BLOCKED'} / {track_state})"
    if block_motion_state == "GRABBED_PINCH_INSUFFICIENT":
        return "SLIP: PINCH INSUFFICIENT (PINCH_INSUFFICIENT)"
    if block_motion_state == "FREE_VISIBLE":
        return "FREE (FREE_VISIBLE)"
    if block_motion_state == "CONTACT_HIDDEN":
        return "CONTACT (CONTACT_HIDDEN)"
    if block_motion_state:
        return block_motion_state
    return "UNKNOWN"


def make_pinch_label(pinch_state: str, pinch_distance: float | None) -> str:
    """Return readable pinch text."""

    distance = "NA" if pinch_distance is None else f"{pinch_distance:.3f} m"
    if pinch_state == "PINCH_INSUFFICIENT":
        return f"PINCH INSUFFICIENT, distance={distance}"
    if pinch_state == "PINCH_VALID":
        return f"PINCH VALID, distance={distance}"
    return f"{pinch_state or 'PINCH UNKNOWN'}, distance={distance}"


def make_main_state_label(
    *,
    tracker_valid: bool,
    stop_reason: str,
    large_delta: bool,
    blocked_force_active: bool,
    slip_active: bool,
    contact_state: str,
    detach_state: str,
    block_motion_state: str,
    pinch_state: str,
) -> str:
    """Return top-level display state without changing controller semantics."""

    if not tracker_valid or stop_reason == "TRACKING_INVALID":
        return "TRACKING INVALID"
    if large_delta or stop_reason == "LARGE_DELTA":
        return "LARGE DELTA"
    if blocked_force_active or stop_reason == "TRACK_BLOCKED":
        return "BLOCKED"
    if slip_active:
        return "SLIP"
    if detach_state and detach_state != "NONE":
        return "CONTACT RELEASE"
    if contact_state == "OUTSIDE_BLOCK":
        return "CONTACT RELEASE"
    if block_motion_state == "GRABBED_MOVING":
        return "MOVING"
    if pinch_state == "PINCH_INSUFFICIENT":
        return "PINCH INSUFFICIENT"
    return "FREE"


def _name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return value.name
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _metadata_value(sample: Any, key: str) -> Any:
    metadata = getattr(sample, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return None
    return metadata.get(key)


def _vec_to_list(value: Any) -> list[float] | None:
    if value is None:
        return None
    if all(hasattr(value, attr) for attr in ("x", "y", "z")):
        return [float(value.x), float(value.y), float(value.z)]
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        items = list(value)
    except TypeError:
        return None
    if len(items) != 3:
        return None
    return [float(items[0]), float(items[1]), float(items[2])]


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _format_point(point: Any) -> str:
    if point is None:
        return "NA"
    try:
        return f"({float(point[0]):.3f},{float(point[1]):.3f},{float(point[2]):.3f})"
    except (TypeError, ValueError, IndexError):
        return "NA"


def _compact(value: str) -> str:
    return value.replace(" ", "_")
