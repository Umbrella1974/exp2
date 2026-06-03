"""Thread-safe system timing diagnostics for live and replay pipelines.

The collector keeps timing records in memory while a run is active. Callers
write the CSV once the run is complete, so frame processing and GUI rendering
do not perform per-frame file I/O.
"""

from __future__ import annotations

import csv
import math
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


TIMING_CSV_FIELDS = [
    "sequence_index",
    "event_type",
    "mode",
    "is_live_latency",
    "frame_index",
    "phase",
    "consumed_phase",
    "operator_command",
    "frame_published",
    "frame_consumed",
    "frame_processed",
    "overwritten_before_consume",
    "raw_receive_monotonic_ms",
    "combined_monotonic_ms",
    "skeleton_receive_monotonic_ms",
    "tracker_receive_monotonic_ms",
    "skeleton_tracker_sync_delta_ms",
    "frame_published_monotonic_ms",
    "frame_consumed_monotonic_ms",
    "parse_start_monotonic_ms",
    "parse_end_monotonic_ms",
    "adapter_start_monotonic_ms",
    "adapter_end_monotonic_ms",
    "trial_update_start_monotonic_ms",
    "trial_update_end_monotonic_ms",
    "snapshot_created_monotonic_ms",
    "snapshot_published_monotonic_ms",
    "gui_render_monotonic_ms",
    "operator_command_monotonic_ms",
    "trial_end_monotonic_ms",
    "raw_to_frame_publish_latency_ms",
    "frame_wait_age_ms",
    "raw_to_trial_update_latency_ms",
    "frame_to_trial_update_latency_ms",
    "parse_duration_ms",
    "adapter_duration_ms",
    "trial_update_duration_ms",
    "trial_update_to_snapshot_latency_ms",
    "snapshot_publish_to_gui_render_latency_ms",
    "operator_command_to_trial_stop_latency_ms",
]


@dataclass
class TimingRecord:
    """One frame or session-event timing record."""

    sequence_index: int
    event_type: str
    mode: str
    is_live_latency: bool
    frame_index: int | None = None
    phase: str = ""
    consumed_phase: str = ""
    operator_command: str | None = None
    frame_published: bool = False
    frame_consumed: bool = False
    frame_processed: bool = False
    overwritten_before_consume: bool = False
    raw_receive_monotonic_ms: float | None = None
    combined_monotonic_ms: float | None = None
    skeleton_receive_monotonic_ms: float | None = None
    tracker_receive_monotonic_ms: float | None = None
    skeleton_tracker_sync_delta_ms: float | None = None
    frame_published_monotonic_ms: float | None = None
    frame_consumed_monotonic_ms: float | None = None
    parse_start_monotonic_ms: float | None = None
    parse_end_monotonic_ms: float | None = None
    adapter_start_monotonic_ms: float | None = None
    adapter_end_monotonic_ms: float | None = None
    trial_update_start_monotonic_ms: float | None = None
    trial_update_end_monotonic_ms: float | None = None
    snapshot_created_monotonic_ms: float | None = None
    snapshot_published_monotonic_ms: float | None = None
    gui_render_monotonic_ms: float | None = None
    operator_command_monotonic_ms: float | None = None
    trial_end_monotonic_ms: float | None = None

    def to_row(self) -> dict[str, Any]:
        """Return a CSV-safe dictionary including derived latency metrics."""

        row = asdict(self)
        row.update(
            {
                "raw_to_frame_publish_latency_ms": _difference_ms(
                    self.frame_published_monotonic_ms,
                    self.raw_receive_monotonic_ms,
                ),
                "frame_wait_age_ms": _difference_ms(
                    self.frame_consumed_monotonic_ms,
                    self.frame_published_monotonic_ms,
                ),
                "raw_to_trial_update_latency_ms": _difference_ms(
                    self.trial_update_start_monotonic_ms,
                    self.raw_receive_monotonic_ms,
                ),
                "frame_to_trial_update_latency_ms": _difference_ms(
                    self.trial_update_start_monotonic_ms,
                    self.frame_published_monotonic_ms,
                ),
                "parse_duration_ms": _difference_ms(
                    self.parse_end_monotonic_ms,
                    self.parse_start_monotonic_ms,
                ),
                "adapter_duration_ms": _difference_ms(
                    self.adapter_end_monotonic_ms,
                    self.adapter_start_monotonic_ms,
                ),
                "trial_update_duration_ms": _difference_ms(
                    self.trial_update_end_monotonic_ms,
                    self.trial_update_start_monotonic_ms,
                ),
                "trial_update_to_snapshot_latency_ms": _difference_ms(
                    self.snapshot_created_monotonic_ms,
                    self.trial_update_end_monotonic_ms,
                ),
                "snapshot_publish_to_gui_render_latency_ms": _difference_ms(
                    self.gui_render_monotonic_ms,
                    self.snapshot_published_monotonic_ms,
                ),
                "operator_command_to_trial_stop_latency_ms": _difference_ms(
                    self.trial_end_monotonic_ms,
                    self.operator_command_monotonic_ms,
                ),
            }
        )
        return row


class TimingDiagnostics:
    """Collect per-frame timing records without file I/O in realtime paths."""

    def __init__(self, *, mode: str, is_live_latency: bool) -> None:
        self.mode = str(mode)
        self.is_live_latency = bool(is_live_latency)
        self._lock = threading.RLock()
        self._records_by_frame: dict[int, TimingRecord] = {}
        self._session_records: list[TimingRecord] = []
        self._next_sequence_index = 0

    def record_frame_published(
        self,
        frame: Any,
        *,
        phase: str,
        monotonic_time: float | None = None,
        overwritten_frame: Any | None = None,
    ) -> None:
        """Record that a frame entered the latest-frame buffer."""

        published_ms = _monotonic_ms(monotonic_time)
        frame_index = _frame_index(frame)
        raw = getattr(frame, "raw_frame", None)
        raw = raw if isinstance(raw, dict) else {}
        with self._lock:
            if overwritten_frame is not None:
                overwritten_index = _frame_index(overwritten_frame)
                if overwritten_index is not None:
                    overwritten = self._record_for_frame(overwritten_index)
                    overwritten.overwritten_before_consume = True
            record = self._record_for_frame(frame_index) if frame_index is not None else self._new_session_record()
            record.phase = str(phase)
            record.frame_published = True
            record.frame_published_monotonic_ms = published_ms
            record.raw_receive_monotonic_ms = _seconds_to_ms(
                getattr(frame, "receive_time_monotonic", None)
            )
            record.combined_monotonic_ms = _optional_float(raw.get("combined_monotonic_ms"))
            record.skeleton_receive_monotonic_ms = _optional_float(
                raw.get("skeleton_receive_monotonic_ms")
            )
            record.tracker_receive_monotonic_ms = _optional_float(
                raw.get("tracker_receive_monotonic_ms")
            )
            record.skeleton_tracker_sync_delta_ms = _sync_delta_ms(
                record.skeleton_receive_monotonic_ms,
                record.tracker_receive_monotonic_ms,
            )

    def record_frame_consumed(
        self,
        frame: Any,
        *,
        phase: str,
        monotonic_time: float | None = None,
    ) -> None:
        """Record that a consumer took a previously published frame."""

        frame_index = _frame_index(frame)
        if frame_index is None:
            return
        with self._lock:
            record = self._record_for_frame(frame_index)
            record.frame_consumed = True
            record.consumed_phase = str(phase)
            record.frame_consumed_monotonic_ms = _monotonic_ms(monotonic_time)

    def record_parse(
        self,
        frame_index: int,
        *,
        start_monotonic: float,
        end_monotonic: float,
    ) -> None:
        self._update_interval(
            frame_index,
            "parse_start_monotonic_ms",
            "parse_end_monotonic_ms",
            start_monotonic,
            end_monotonic,
        )

    def record_adapter(
        self,
        frame_index: int,
        *,
        start_monotonic: float,
        end_monotonic: float,
    ) -> None:
        self._update_interval(
            frame_index,
            "adapter_start_monotonic_ms",
            "adapter_end_monotonic_ms",
            start_monotonic,
            end_monotonic,
        )

    def record_trial_update(
        self,
        frame_index: int,
        *,
        start_monotonic: float,
        end_monotonic: float,
    ) -> None:
        self._update_interval(
            frame_index,
            "trial_update_start_monotonic_ms",
            "trial_update_end_monotonic_ms",
            start_monotonic,
            end_monotonic,
        )

    def record_snapshot_created(self, frame_index: int, *, monotonic_time: float | None = None) -> None:
        self._update_field(frame_index, "snapshot_created_monotonic_ms", _monotonic_ms(monotonic_time))

    def record_snapshot_published(self, frame_index: int, *, monotonic_time: float | None = None) -> None:
        self._update_field(frame_index, "snapshot_published_monotonic_ms", _monotonic_ms(monotonic_time))

    def record_gui_render(self, snapshot: Any, monotonic_time: float | None = None) -> None:
        """Record the first GUI render of a frame."""

        frame_index = _frame_index(snapshot)
        if frame_index is None:
            return
        with self._lock:
            record = self._record_for_frame(frame_index)
            if record.gui_render_monotonic_ms is None:
                record.gui_render_monotonic_ms = _monotonic_ms(monotonic_time)

    def record_frame_processed(self, frame_index: int) -> None:
        self._update_field(frame_index, "frame_processed", True)

    def record_operator_command(
        self,
        command: str,
        *,
        frame_index: int | None,
        phase: str,
        monotonic_time: float | None = None,
    ) -> None:
        """Attach an operator command to the last frame or a session-event row."""

        with self._lock:
            record = (
                self._record_for_frame(frame_index)
                if frame_index is not None
                else self._new_session_record(event_type="session_event")
            )
            record.event_type = "operator_command"
            if not record.phase:
                record.phase = str(phase)
            record.operator_command = str(command)
            record.operator_command_monotonic_ms = _monotonic_ms(monotonic_time)

    def record_trial_end(
        self,
        *,
        frame_index: int | None,
        phase: str,
        monotonic_time: float | None = None,
    ) -> None:
        """Attach the trial stop time to the last frame or a session-event row."""

        with self._lock:
            if frame_index is not None:
                record = self._record_for_frame(frame_index)
            else:
                record = self._latest_open_operator_record() or self._new_session_record(
                    event_type="session_event"
                )
            if not record.phase:
                record.phase = str(phase)
            record.trial_end_monotonic_ms = _monotonic_ms(monotonic_time)

    def records_snapshot(self) -> list[TimingRecord]:
        """Return immutable copies ordered by record creation."""

        with self._lock:
            records = [*self._records_by_frame.values(), *self._session_records]
            return [replace(record) for record in sorted(records, key=lambda item: item.sequence_index)]

    def rows_snapshot(self) -> list[dict[str, Any]]:
        """Return derived rows ordered by record creation."""

        return [record.to_row() for record in self.records_snapshot()]

    def summary(self) -> dict[str, Any]:
        """Return lightweight record counts for run summaries."""

        records = self.records_snapshot()
        frame_records = [record for record in records if record.frame_index is not None]
        return {
            "timing_enabled": True,
            "timing_mode": self.mode,
            "timing_is_live_latency": self.is_live_latency,
            "timing_record_count": len(records),
            "published_frame_count": sum(record.frame_published for record in frame_records),
            "consumed_frame_count": sum(record.frame_consumed for record in frame_records),
            "processed_frame_count": sum(record.frame_processed for record in frame_records),
            "overwritten_before_consume_count": sum(
                record.overwritten_before_consume for record in frame_records
            ),
        }

    def write_csv(self, path: str | Path) -> Path:
        """Write all current timing rows to a CSV file."""

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.rows_snapshot()
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=TIMING_CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _csv_value(row.get(key)) for key in TIMING_CSV_FIELDS})
        return output_path

    def _record_for_frame(self, frame_index: int) -> TimingRecord:
        index = int(frame_index)
        record = self._records_by_frame.get(index)
        if record is None:
            record = TimingRecord(
                sequence_index=self._allocate_sequence_index(),
                event_type="frame",
                mode=self.mode,
                is_live_latency=self.is_live_latency,
                frame_index=index,
            )
            self._records_by_frame[index] = record
        return record

    def _new_session_record(self, *, event_type: str = "session_event") -> TimingRecord:
        record = TimingRecord(
            sequence_index=self._allocate_sequence_index(),
            event_type=event_type,
            mode=self.mode,
            is_live_latency=self.is_live_latency,
        )
        self._session_records.append(record)
        return record

    def _latest_open_operator_record(self) -> TimingRecord | None:
        for record in reversed(self._session_records):
            if (
                record.operator_command_monotonic_ms is not None
                and record.trial_end_monotonic_ms is None
            ):
                return record
        return None

    def _allocate_sequence_index(self) -> int:
        value = self._next_sequence_index
        self._next_sequence_index += 1
        return value

    def _update_interval(
        self,
        frame_index: int,
        start_field: str,
        end_field: str,
        start_monotonic: float,
        end_monotonic: float,
    ) -> None:
        with self._lock:
            record = self._record_for_frame(frame_index)
            setattr(record, start_field, _seconds_to_ms(start_monotonic))
            setattr(record, end_field, _seconds_to_ms(end_monotonic))

    def _update_field(self, frame_index: int, field_name: str, value: Any) -> None:
        with self._lock:
            record = self._record_for_frame(frame_index)
            setattr(record, field_name, value)


def _frame_index(value: Any) -> int | None:
    raw = getattr(value, "frame_index", value if isinstance(value, int) else None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _monotonic_ms(value: float | None = None) -> float:
    return _seconds_to_ms(time.monotonic() if value is None else value)


def _seconds_to_ms(value: Any) -> float | None:
    result = _optional_float(value)
    return result * 1000.0 if result is not None else None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sync_delta_ms(skeleton_ms: float | None, tracker_ms: float | None) -> float | None:
    if skeleton_ms is None or tracker_ms is None:
        return None
    return abs(skeleton_ms - tracker_ms)


def _difference_ms(end_ms: float | None, start_ms: float | None) -> float | None:
    if end_ms is None or start_ms is None:
        return None
    return end_ms - start_ms


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value
