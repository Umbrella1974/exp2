"""Session-level recorder for experiment and offline replay data.

SessionRecorder is an observation and persistence module. It does not change
experiment state and does not implement contact, slip, blocked, or trial logic.
It saves raw, device, processed, event, and haptic data so later tools can
replay, debug, visualize, or archive a session.
"""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


PROCESSED_FRAME_HEADER = [
    "frame_index",
    "raw_timestamp",
    "source_frame_id",
    "combined_monotonic_ms",
    "skeleton_receive_monotonic_ms",
    "tracker_receive_monotonic_ms",
    "sync_delta_ms",
    "sample_time",
    "trial_time",
    "tracker_valid",
    "hand_valid",
    "pinch_valid",
    "input_source",
    "pinch_distance",
    "tracker_world_x",
    "tracker_world_y",
    "tracker_world_z",
    "pinch_center_world_x",
    "pinch_center_world_y",
    "pinch_center_world_z",
    "pinch_center_task_x",
    "pinch_center_task_y",
    "pinch_center_task_z",
    "block_center_task_x",
    "block_center_task_y",
    "block_center_task_z",
    "contact_state",
    "pinch_state",
    "block_motion_state",
    "stop_reason",
    "track_state",
    "detach_state",
    "slip_active",
    "slip_reason",
    "blocked_force_active",
    "large_delta",
    "subject_end",
    "haptic_state",
    "haptic_reason",
]

EVENT_HEADER = [
    "event_index",
    "frame_index",
    "time",
    "event_type",
    "details_json",
]

HAPTIC_HEADER = [
    "frame_index",
    "time",
    "haptic_state",
    "haptic_reason",
    "slip_active",
    "slip_reason",
    "blocked_force_active",
    "command_type",
    "amplitude",
    "duration",
    "pattern",
    "sent_to_hardware",
    "send_success",
    "send_latency_ms",
    "suppressed_by_rate_limit",
    "details_json",
]


class SessionRecorder:
    """Write a standard session directory without influencing experiment logic."""

    def __init__(self, session_dir: str | Path, overwrite: bool = False) -> None:
        self.session_dir = Path(session_dir)
        self.overwrite = overwrite
        self._started = False
        self._event_index = 0

    def __enter__(self) -> "SessionRecorder":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def start_session(
        self,
        session_meta: dict,
        calibration: dict | None = None,
        trial_config: dict | None = None,
    ) -> None:
        """Create session files and write session-level metadata."""

        self._prepare_session_dir()
        self._started = True
        self._write_json(
            "session_meta.json",
            {
                "created_at": _utc_now_iso(),
                "warnings": [],
                **session_meta,
            },
        )
        if calibration is not None:
            self._write_json("calibration.json", calibration)
        if trial_config is not None:
            self._write_json("trial_config.json", trial_config)

        self._path("raw_frames.jsonl").touch()
        self._path("device_frames.jsonl").touch()
        self._write_csv_header("processed_frames.csv", PROCESSED_FRAME_HEADER)
        self._write_csv_header("events.csv", EVENT_HEADER)
        self._write_csv_header("haptic.csv", HAPTIC_HEADER)
        self._path("plots").mkdir(exist_ok=True)

    def record_raw_frame(self, frame_index: int, raw_frame: dict) -> None:
        """Append a raw frame exactly as a JSON object, without frame_index."""

        del frame_index
        self._require_started()
        with self._path("raw_frames.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(raw_frame, default=_json_default, ensure_ascii=False))
            handle.write("\n")

    def record_device_frame(self, frame_index: int, device_frame: Any) -> None:
        """Append a compact DeviceFrame summary."""

        self._require_started()
        tracker = _attr(device_frame, "tracker")
        hand = _attr(device_frame, "hand")
        pose = _attr(tracker, "pose_world")
        row = {
            "frame_index": frame_index,
            "source_timestamp": _attr(device_frame, "source_timestamp"),
            "source_frame_id": _attr(device_frame, "source_frame_id"),
            "combined_monotonic_ms": _attr(device_frame, "combined_monotonic_ms"),
            "skeleton_receive_monotonic_ms": _attr(
                device_frame,
                "skeleton_receive_monotonic_ms",
            ),
            "tracker_receive_monotonic_ms": _attr(
                device_frame,
                "tracker_receive_monotonic_ms",
            ),
            "sync_delta_ms": _attr(device_frame, "sync_delta_ms"),
            "skeleton_callback_index": _attr(device_frame, "skeleton_callback_index"),
            "tracker_callback_index": _attr(device_frame, "tracker_callback_index"),
            "tracker_valid": _attr(tracker, "valid"),
            "tracker_position_world": _vector_to_list(_attr(pose, "position")),
            "tracker_rotation_world": _vector_to_list(_attr(pose, "rotation")),
            "tracker_last_update_time": _attr(tracker, "last_update_time"),
            "hand_valid": _attr(hand, "valid"),
            "node_count": len(_attr(hand, "nodes", {}) or {}),
            "warning": "",
            "error": "",
        }
        with self._path("device_frames.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_json_safe(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def record_processed_frame(
        self,
        frame_index: int,
        raw_frame: dict | None,
        device_frame: Any,
        sample: Any,
        frame_output: Any,
        haptic_state: Any = None,
        extra: dict | None = None,
    ) -> None:
        """Append one processed frame summary row."""

        self._require_started()
        extra = extra or {}
        haptic_state = haptic_state if haptic_state is not None else _attr(frame_output, "haptic_feedback")
        feedback = _attr(frame_output, "feedback_state")
        block_state = _attr(frame_output, "block_state")
        tracker_pose = _attr(_attr(device_frame, "tracker"), "pose_world")

        row = {
            "frame_index": frame_index,
            "raw_timestamp": _raw_value(raw_frame, "timestamp"),
            "source_frame_id": _attr(device_frame, "source_frame_id"),
            "combined_monotonic_ms": _attr(device_frame, "combined_monotonic_ms"),
            "skeleton_receive_monotonic_ms": _attr(
                device_frame,
                "skeleton_receive_monotonic_ms",
            ),
            "tracker_receive_monotonic_ms": _attr(
                device_frame,
                "tracker_receive_monotonic_ms",
            ),
            "sync_delta_ms": _attr(device_frame, "sync_delta_ms"),
            "sample_time": _attr(sample, "time"),
            "trial_time": extra.get("trial_time", ""),
            "tracker_valid": _attr(sample, "tracker_valid"),
            "hand_valid": _metadata_value(sample, "hand_valid"),
            "pinch_valid": _metadata_value(sample, "pinch_valid"),
            "input_source": extra.get("input_source", _metadata_value(sample, "offline_point_source")),
            "pinch_distance": _attr(sample, "pinch_distance"),
            "tracker_world_x": _vector_component(_attr(tracker_pose, "position"), 0),
            "tracker_world_y": _vector_component(_attr(tracker_pose, "position"), 1),
            "tracker_world_z": _vector_component(_attr(tracker_pose, "position"), 2),
            "pinch_center_world_x": _vector_component(_attr(sample, "pinch_center_world"), 0),
            "pinch_center_world_y": _vector_component(_attr(sample, "pinch_center_world"), 1),
            "pinch_center_world_z": _vector_component(_attr(sample, "pinch_center_world"), 2),
            "pinch_center_task_x": _vec_attr(_attr(frame_output, "pinch_center_task"), "x"),
            "pinch_center_task_y": _vec_attr(_attr(frame_output, "pinch_center_task"), "y"),
            "pinch_center_task_z": _vec_attr(_attr(frame_output, "pinch_center_task"), "z"),
            "block_center_task_x": _vec_attr(_attr(block_state, "center"), "x"),
            "block_center_task_y": _vec_attr(_attr(block_state, "center"), "y"),
            "block_center_task_z": _vec_attr(_attr(block_state, "center"), "z"),
            "contact_state": _name(_attr(frame_output, "contact_state")),
            "pinch_state": _name(_attr(frame_output, "pinch_state")),
            "block_motion_state": _name(_attr(block_state, "motion_state")),
            "stop_reason": _name(_attr(feedback, "stop_reason")),
            "track_state": _name(_attr(feedback, "track_state")),
            "detach_state": _name(_attr(feedback, "detach_state")),
            "slip_active": _attr(haptic_state, "slip_active"),
            "slip_reason": _name(_attr(haptic_state, "slip_reason")),
            "blocked_force_active": _attr(haptic_state, "blocked_force_active"),
            "large_delta": _name(_attr(feedback, "stop_reason")) == "LARGE_DELTA",
            "subject_end": _attr(sample, "subject_end"),
            "haptic_state": _name(_attr(haptic_state, "haptic_state")),
            "haptic_reason": _name(_attr(haptic_state, "haptic_reason")),
        }
        self._append_csv_row("processed_frames.csv", PROCESSED_FRAME_HEADER, row)

    def record_events(self, frame_index: int, time: float | None, events: Any) -> None:
        """Append existing pipeline events without deriving new ones."""

        self._require_started()
        for event in events or ():
            row = {
                "event_index": self._event_index,
                "frame_index": frame_index,
                "time": _attr(event, "time", time),
                "event_type": _attr(event, "event_type"),
                "details_json": json.dumps(
                    _json_safe(_attr(event, "details", {})),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
            self._append_csv_row("events.csv", EVENT_HEADER, row)
            self._event_index += 1

    def record_haptic(
        self,
        frame_index: int,
        time: float | None,
        haptic_state: Any,
        haptic_command: Any = None,
        details: dict | None = None,
    ) -> None:
        """Append haptic state and optional command metadata."""

        self._require_started()
        details = details or {}
        row = {
            "frame_index": frame_index,
            "time": time,
            "haptic_state": _name(_attr(haptic_state, "haptic_state")),
            "haptic_reason": _name(_attr(haptic_state, "haptic_reason")),
            "slip_active": _attr(haptic_state, "slip_active"),
            "slip_reason": _name(_attr(haptic_state, "slip_reason")),
            "blocked_force_active": _attr(haptic_state, "blocked_force_active"),
            "command_type": _command_value(haptic_command, "command_type"),
            "amplitude": _command_value(haptic_command, "amplitude"),
            "duration": _command_value(haptic_command, "duration"),
            "pattern": _command_value(haptic_command, "pattern"),
            "sent_to_hardware": _command_value(haptic_command, "sent_to_hardware", False),
            "send_success": _command_value(haptic_command, "send_success"),
            "send_latency_ms": _command_value(haptic_command, "send_latency_ms"),
            "suppressed_by_rate_limit": _command_value(
                haptic_command,
                "suppressed_by_rate_limit",
            ),
            "details_json": json.dumps(_json_safe(details), ensure_ascii=False, sort_keys=True),
        }
        self._append_csv_row("haptic.csv", HAPTIC_HEADER, row)

    def finalize(self, summary: dict) -> None:
        """Write final trial summary."""

        self._require_started()
        self._write_json("trial_summary.json", summary)

    def _prepare_session_dir(self) -> None:
        if self.session_dir.exists():
            if not self.overwrite and any(self.session_dir.iterdir()):
                raise FileExistsError(
                    f"session directory already exists: {self.session_dir}. "
                    "Choose another --session-dir or clear it first."
                )
            if self.overwrite:
                shutil.rmtree(self.session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("start_session() must be called before recording.")

    def _path(self, filename: str) -> Path:
        return self.session_dir / filename

    def _write_json(self, filename: str, payload: dict) -> None:
        self._path(filename).write_text(
            json.dumps(_json_safe(payload), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def _write_csv_header(self, filename: str, header: list[str]) -> None:
        with self._path(filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()

    def _append_csv_row(self, filename: str, header: list[str], row: dict[str, Any]) -> None:
        with self._path(filename).open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writerow({key: _csv_value(row.get(key)) for key in header})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raw_value(raw_frame: dict | None, key: str) -> Any:
    if not isinstance(raw_frame, dict):
        return ""
    return raw_frame.get(key, "")


def _metadata_value(sample: Any, key: str) -> Any:
    metadata = _attr(sample, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return ""
    return metadata.get(key, "")


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _name(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _vec_attr(value: Any, attr_name: str) -> Any:
    if value is None:
        return ""
    return getattr(value, attr_name, "")


def _vector_component(value: Any, index: int) -> Any:
    if value is None:
        return ""
    try:
        return value[index]
    except (IndexError, TypeError, KeyError):
        return ""


def _vector_to_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if hasattr(value, "tolist"):
        return value.tolist()
    try:
        return list(value)
    except TypeError:
        return None


def _command_value(command: Any, name: str, default: Any = "") -> Any:
    if command is None:
        return default
    return _attr(command, name, default)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return value.name
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if is_dataclass(value):
        return json.dumps(_json_safe(asdict(value)), ensure_ascii=False, sort_keys=True)
    if isinstance(value, list | tuple | dict):
        return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)
    return value


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)


def _json_default(value: Any) -> Any:
    return _json_safe(value)
