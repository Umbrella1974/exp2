"""CSV logging helpers for frame and event outputs."""

from __future__ import annotations

import csv
from pathlib import Path

from data_models import FrameOutput, HapticEvent


class CsvLogger:
    """Write frame outputs and haptic events to CSV files."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frames_path = self.output_dir / "frames.csv"
        self.events_path = self.output_dir / "events.csv"
        self._frames_header_written = self.frames_path.exists() and self.frames_path.stat().st_size > 0
        self._events_header_written = self.events_path.exists() and self.events_path.stat().st_size > 0

    def log_frame(self, frame_output: FrameOutput) -> None:
        """Append one frame output row."""

        row = {
            "time": frame_output.time,
            "tracking_valid": frame_output.feedback_state.tracking_valid,
            "recovery_frame": frame_output.feedback_state.recovery_frame,
            "contact_state": frame_output.contact_state.name,
            "pinch_state": frame_output.pinch_state.name,
            "motion_state": frame_output.block_state.motion_state.name,
            "stop_reason": frame_output.feedback_state.stop_reason.name,
            "track_state": frame_output.feedback_state.track_state.name,
            "detach_state": frame_output.feedback_state.detach_state.name,
            "block_center_x": frame_output.block_state.center.x,
            "block_center_y": frame_output.block_state.center.y,
            "block_center_z": frame_output.block_state.center.z,
            "visible": frame_output.block_state.visible,
            "slip_active": frame_output.haptic_feedback.slip_active,
            "slip_reason": (
                frame_output.haptic_feedback.slip_reason.name
                if frame_output.haptic_feedback.slip_reason is not None
                else ""
            ),
            "blocked_force_active": frame_output.haptic_feedback.blocked_force_active,
        }
        self._append_row(self.frames_path, row, kind="frame")

    def log_events(self, events: tuple[HapticEvent, ...]) -> None:
        """Append zero or more haptic events."""

        for event in events:
            row = {
                "time": event.time,
                "event_type": event.event_type.name,
                "detach_state": event.detach_state.name,
            }
            self._append_row(self.events_path, row, kind="event")

    def _append_row(self, path: Path, row: dict[str, object], *, kind: str) -> None:
        header_written_attr = (
            "_frames_header_written" if kind == "frame" else "_events_header_written"
        )
        header_written = getattr(self, header_written_attr)
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            if not header_written:
                writer.writeheader()
                setattr(self, header_written_attr, True)
            writer.writerow(row)
