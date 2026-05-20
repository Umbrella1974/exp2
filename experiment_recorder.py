"""CSV recorder for Stage 2 trial outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from data_models import BlockedInfo, Vec3
from trial_controller import EventRecord, TrialFrameResult


class ExperimentRecorder:
    """Write TrialFrameResult and EventRecord objects to CSV files."""

    def __init__(self, output_dir: str | Path, *, record_frames: bool = True, record_events: bool = True) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frames_path = self.output_dir / "frames.csv"
        self.events_path = self.output_dir / "events.csv"
        self.record_frames = record_frames
        self.record_events = record_events
        self._frames_header_written = self.frames_path.exists() and self.frames_path.stat().st_size > 0
        self._events_header_written = self.events_path.exists() and self.events_path.stat().st_size > 0

    def log_frame(self, result: TrialFrameResult) -> None:
        """Append one frame row."""

        if not self.record_frames:
            return

        output = result.frame_output
        feedback = output.feedback_state
        haptic = result.haptic_feedback_state
        blocked_info = feedback.blocked_info

        row = {
            "time": result.frame_output.time,
            "trial_id": result.trial_id,
            "trial_state": result.trial_state.name,
            "time_since_prompt": result.time_since_prompt,
            "tracker_valid": feedback.tracking_valid,
            "recovery_frame": feedback.recovery_frame,
            "pinch_center_task_x": _vec_value(output.pinch_center_task, "x"),
            "pinch_center_task_y": _vec_value(output.pinch_center_task, "y"),
            "pinch_center_task_z": _vec_value(output.pinch_center_task, "z"),
            "pinch_distance": output.pinch_distance if output.pinch_distance is not None else "",
            "block_center_task_x": output.block_state.center.x,
            "block_center_task_y": output.block_state.center.y,
            "block_center_task_z": output.block_state.center.z,
            "block_visible": output.block_state.visible,
            "contact_state": output.contact_state.name,
            "pinch_state": output.pinch_state.name,
            "block_motion_state": output.block_state.motion_state.name,
            "stop_reason": feedback.stop_reason.name,
            "track_state": feedback.track_state.name,
            "detach_state": feedback.detach_state.name,
            "active_release_count": output.detach_counts.active_release_count,
            "forced_detach_count": output.detach_counts.forced_detach_count,
            "unexpected_detach_count": output.detach_counts.unexpected_detach_count,
            "total_detach_count": output.detach_counts.total_detach_count,
            "hand_delta_x": _vec_value(feedback.hand_delta, "x"),
            "hand_delta_y": _vec_value(feedback.hand_delta, "y"),
            "hand_delta_z": _vec_value(feedback.hand_delta, "z"),
            "candidate_block_center_x": _vec_value(feedback.candidate_block_center, "x"),
            "candidate_block_center_y": _vec_value(feedback.candidate_block_center, "y"),
            "candidate_block_center_z": _vec_value(feedback.candidate_block_center, "z"),
            "primary_blocked_surface": _blocked_surface(blocked_info),
            "primary_blocked_amount": _blocked_value(blocked_info, "primary_blocked_amount"),
            "blocked_distance": _blocked_value(blocked_info, "blocked_distance"),
            "blocked_vector_x": _vec_value(blocked_info.blocked_vector if blocked_info is not None else None, "x"),
            "blocked_vector_y": _vec_value(blocked_info.blocked_vector if blocked_info is not None else None, "y"),
            "blocked_vector_z": _vec_value(blocked_info.blocked_vector if blocked_info is not None else None, "z"),
            "slip_active": haptic.slip_active,
            "slip_reason": haptic.slip_reason.name if haptic.slip_reason is not None else "",
            "blocked_force_active": haptic.blocked_force_active,
            "force_vector_task_x": _vec_value(haptic.force_vector_task, "x"),
            "force_vector_task_y": _vec_value(haptic.force_vector_task, "y"),
            "force_vector_task_z": _vec_value(haptic.force_vector_task, "z"),
            "force_magnitude": haptic.force_magnitude,
        }
        self._append_row(self.frames_path, row, kind="frame")

    def log_events(self, events: tuple[EventRecord, ...]) -> None:
        """Append event rows."""

        if not self.record_events:
            return

        for event in events:
            row = {
                "time": event.time,
                "trial_id": event.trial_id,
                "event_type": event.event_type,
                "trial_state": event.state.name,
                "value": "" if event.value is None else event.value,
                "details_json": json.dumps(event.details, sort_keys=True),
            }
            self._append_row(self.events_path, row, kind="event")

    def _append_row(self, path: Path, row: dict[str, object], *, kind: str) -> None:
        header_attr = "_frames_header_written" if kind == "frame" else "_events_header_written"
        header_written = getattr(self, header_attr)
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            if not header_written:
                writer.writeheader()
                setattr(self, header_attr, True)
            writer.writerow(row)


def _vec_value(vector: Vec3 | None, component: str) -> float | str:
    if vector is None:
        return ""
    return getattr(vector, component)


def _blocked_surface(blocked_info: BlockedInfo | None) -> str:
    if blocked_info is None or blocked_info.primary_blocked_surface is None:
        return ""
    return blocked_info.primary_blocked_surface.name


def _blocked_value(blocked_info: BlockedInfo | None, attribute: str) -> float | str:
    if blocked_info is None:
        return ""
    return getattr(blocked_info, attribute)
