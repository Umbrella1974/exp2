"""Non-hardware cue command generation, sinks, and final cue logging.

This module observes TrialController outputs. It never changes controller state
or sends real haptic hardware commands.
"""

from __future__ import annotations

import csv
import json
import math
import re
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from cue_config import CueConfig, default_cue_config


CUE_SINK_CHOICES = ("none", "logging", "console", "gui_text")
CUE_TYPES = (
    "contact_enter",
    "contact_exit",
    "slip_pinch_insufficient",
    "slip_track_blocked",
    "blocked_directional",
)
CUE_PRIORITY = {
    "contact_enter": 10,
    "contact_exit": 20,
    "slip_pinch_insufficient": 30,
    "slip_track_blocked": 30,
    "blocked_directional": 40,
}
CUE_CSV_FIELDS = [
    "cue_sequence_index",
    "cue_id",
    "trial_id",
    "cue_type",
    "cue_modality",
    "requested_cue_sink",
    "mode",
    "is_live_cue_timing",
    "trigger_reason",
    "source_frame_index",
    "source_frame_id",
    "source_sample_time",
    "source_trial_time",
    "source_event_type",
    "source_state",
    "detach_state",
    "slip_reason",
    "track_state",
    "primary_blocked_surface",
    "direction",
    "pattern",
    "intensity",
    "duration_ms",
    "message",
    "created_monotonic_ms",
    "issued_monotonic_ms",
    "displayed_monotonic_ms",
    "displayed_frame_index",
    "ack_monotonic_ms",
    "success",
    "display_status",
    "not_displayed_reason",
    "fallback_reason",
    "error",
    "is_hardware_haptic",
    "details_json",
]


@dataclass(frozen=True)
class CueCommand:
    """One semantic cue command before sink-specific outcome fields."""

    cue_sequence_index: int
    cue_id: str
    trial_id: str | int
    cue_type: str
    requested_cue_sink: str
    mode: str
    is_live_cue_timing: bool
    trigger_reason: str
    source_frame_index: int | None
    source_frame_id: str | int | None
    source_sample_time: float | None
    source_trial_time: float | None
    source_event_type: str | None
    source_state: str
    detach_state: str | None
    slip_reason: str | None
    track_state: str | None
    primary_blocked_surface: str | None
    direction: str | None
    pattern: str
    intensity: float | None
    duration_ms: float | None
    message: str
    created_monotonic_ms: float
    is_hardware_haptic: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe semantic command dictionary."""

        return _json_safe(asdict(self))


@dataclass
class CueCommandResult:
    """Mutable sink outcome for an immutable CueCommand."""

    command: CueCommand
    cue_modality: str = ""
    issued_monotonic_ms: float | None = None
    displayed_monotonic_ms: float | None = None
    displayed_frame_index: int | None = None
    ack_monotonic_ms: float | None = None
    success: bool | None = None
    display_status: str = "not_displayed"
    not_displayed_reason: str | None = None
    fallback_reason: str | None = None
    error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def mark_accepted(
        self,
        modality: str,
        *,
        display_status: str = "not_displayed",
        fallback_reason: str | None = None,
    ) -> None:
        """Record direct sink acceptance."""

        with self._lock:
            self.cue_modality = modality
            self.issued_monotonic_ms = time.monotonic() * 1000.0
            self.success = True
            self.display_status = display_status
            if fallback_reason is not None:
                self.fallback_reason = fallback_reason

    def mark_submission_error(self, error: str, *, modality: str) -> None:
        """Record a direct sink submission failure."""

        with self._lock:
            self.cue_modality = modality
            self.issued_monotonic_ms = time.monotonic() * 1000.0
            self.success = False
            self.display_status = "not_displayed"
            self.error = str(error)

    def mark_displayed(
        self,
        *,
        monotonic_ms: float | None = None,
        frame_index: int | None = None,
    ) -> None:
        """Record only the first actual console output or GUI render."""

        with self._lock:
            if self.displayed_monotonic_ms is not None or self.not_displayed_reason is not None:
                return
            self.displayed_monotonic_ms = (
                time.monotonic() * 1000.0 if monotonic_ms is None else float(monotonic_ms)
            )
            self.displayed_frame_index = frame_index
            self.display_status = "displayed"
            self.not_displayed_reason = None

    def mark_not_displayed(self, reason: str, *, status: str = "not_displayed") -> None:
        """Record a final non-display reason without changing acceptance."""

        with self._lock:
            if self.displayed_monotonic_ms is not None or self.not_displayed_reason is not None:
                return
            self.display_status = status
            self.not_displayed_reason = str(reason)

    def mark_async_error(self, error: str, *, reason: str) -> None:
        """Record a post-acceptance worker/display failure."""

        with self._lock:
            self.error = str(error)
            if self.displayed_monotonic_ms is None:
                self.display_status = "not_displayed"
                self.not_displayed_reason = str(reason)

    def is_pending_display(self) -> bool:
        """Return whether the accepted command has not reached a final display."""

        with self._lock:
            return (
                self.success is True
                and self.displayed_monotonic_ms is None
                and self.display_status == "not_displayed"
                and self.not_displayed_reason is None
            )

    def to_dict(self) -> dict[str, Any]:
        """Return one final cue_log.csv-compatible record."""

        with self._lock:
            payload = self.command.to_dict()
            details = payload.pop("details", {})
            payload.update(
                {
                    "cue_modality": self.cue_modality,
                    "issued_monotonic_ms": self.issued_monotonic_ms,
                    "displayed_monotonic_ms": self.displayed_monotonic_ms,
                    "displayed_frame_index": self.displayed_frame_index,
                    "ack_monotonic_ms": self.ack_monotonic_ms,
                    "success": self.success,
                    "display_status": self.display_status,
                    "not_displayed_reason": self.not_displayed_reason,
                    "fallback_reason": self.fallback_reason,
                    "error": self.error,
                    "details_json": json.dumps(details, ensure_ascii=False, sort_keys=True),
                }
            )
            return payload


@dataclass(frozen=True)
class CueSinkConfig:
    """Runtime-only sink selection and timing context."""

    cue_sink: str = "logging"
    mode: str = "live"
    is_live_cue_timing: bool = True


@dataclass(frozen=True)
class CueDisplay:
    """Small thread-safe GUI overlay payload."""

    cue_id: str
    cue_type: str
    message: str
    priority: int


class LatestCueStore:
    """Keep the current GUI cue overlay without owning GUI thread behavior."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: CueCommandResult | None = None
        self._gui_closed = False

    def publish(self, result: CueCommandResult) -> None:
        """Replace the current overlay candidate."""

        previous: CueCommandResult | None
        with self._lock:
            previous = self._active
            self._active = result
        if previous is not None and previous.command.cue_id != result.command.cue_id:
            previous.mark_not_displayed("cue_replaced_before_render")

    def clear(self, reason: str) -> None:
        """Clear the current overlay and finalize an unrendered command."""

        with self._lock:
            active = self._active
            self._active = None
        if active is not None:
            active.mark_not_displayed(reason)

    def mark_gui_closed(self) -> None:
        """Close the display target; future GuiTextCueSink cues may fallback."""

        with self._lock:
            self._gui_closed = True
            active = self._active
            self._active = None
        if active is not None:
            active.mark_not_displayed("gui_closed_before_render")

    def is_gui_closed(self) -> bool:
        with self._lock:
            return self._gui_closed

    def get_active(self) -> CueDisplay | None:
        """Return the latest overlay payload without blocking."""

        with self._lock:
            result = self._active
            if result is None:
                return None
            command = result.command
            return CueDisplay(
                cue_id=command.cue_id,
                cue_type=command.cue_type,
                message=command.message,
                priority=CUE_PRIORITY.get(command.cue_type, 0),
            )

    def mark_rendered(
        self,
        cue_id: str,
        *,
        frame_index: int | None,
        monotonic_ms: float,
    ) -> bool:
        """Mark a cue displayed only if it is still the active overlay."""

        with self._lock:
            result = self._active
            if result is None or result.command.cue_id != cue_id:
                return False
        result.mark_displayed(monotonic_ms=monotonic_ms, frame_index=frame_index)
        return True


class CueSink:
    """Base interface for non-hardware cue sinks."""

    def default_modality(self) -> str:
        raise NotImplementedError

    def submit(self, result: CueCommandResult) -> None:
        raise NotImplementedError

    def cancel(self, result: CueCommandResult, reason: str) -> None:
        result.mark_not_displayed(reason)

    def close(self, reason: str = "session_ended_before_display") -> None:
        del reason


class NullCueSink(CueSink):
    """No-op sink used when cue generation is disabled."""

    def default_modality(self) -> str:
        return "none"

    def submit(self, result: CueCommandResult) -> None:
        result.mark_accepted("none", display_status="not_applicable")


class LoggingCueSink(CueSink):
    """Collector-only sink with no participant-facing display."""

    def default_modality(self) -> str:
        return "logging"

    def submit(self, result: CueCommandResult) -> None:
        result.mark_accepted("logging", display_status="not_applicable")


class ConsoleCueSink(CueSink):
    """Bounded latest-only asynchronous console text sink."""

    def __init__(self, *, output_fn: Callable[[str], None] | None = None) -> None:
        self.output_fn = output_fn or print
        self._condition = threading.Condition()
        self._pending: CueCommandResult | None = None
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="ConsoleCueSink", daemon=True)
        self._worker.start()

    def default_modality(self) -> str:
        return "console_text"

    def submit(self, result: CueCommandResult) -> None:
        result.mark_accepted("console_text")
        with self._condition:
            if self._closed:
                result.mark_not_displayed("session_ended_before_display")
                return
            previous = self._pending
            self._pending = result
            self._condition.notify()
        if previous is not None and previous.command.cue_id != result.command.cue_id:
            previous.mark_not_displayed("console_queue_replaced")

    def cancel(self, result: CueCommandResult, reason: str) -> None:
        with self._condition:
            if self._pending is result:
                self._pending = None
        result.mark_not_displayed(reason)

    def close(self, reason: str = "session_ended_before_display") -> None:
        with self._condition:
            self._closed = True
            pending = self._pending
            self._pending = None
            self._condition.notify_all()
        if pending is not None:
            pending.mark_not_displayed(reason)
        self._worker.join(timeout=1.0)

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait(timeout=0.1)
                if self._closed and self._pending is None:
                    return
                result = self._pending
                self._pending = None
            if result is None or not result.is_pending_display():
                continue
            try:
                self.output_fn(f"[CUE] {result.command.message}")
            except Exception as exc:
                result.mark_async_error(str(exc), reason="console_output_failed")
                continue
            result.mark_displayed()


class GuiTextCueSink(CueSink):
    """GUI overlay sink with console fallback only for future post-close cues."""

    def __init__(
        self,
        *,
        cue_store: LatestCueStore | None = None,
        fallback_console_sink: ConsoleCueSink | None = None,
    ) -> None:
        self.cue_store = cue_store or LatestCueStore()
        self.fallback_console_sink = fallback_console_sink or ConsoleCueSink()

    def default_modality(self) -> str:
        return "console_text" if self.cue_store.is_gui_closed() else "gui_text"

    def submit(self, result: CueCommandResult) -> None:
        if self.cue_store.is_gui_closed():
            result.fallback_reason = "gui_closed"
            self.fallback_console_sink.submit(result)
            return
        result.mark_accepted("gui_text")
        self.cue_store.publish(result)

    def cancel(self, result: CueCommandResult, reason: str) -> None:
        self.cue_store.clear(reason)
        self.fallback_console_sink.cancel(result, _render_to_display_reason(reason))

    def mark_gui_closed(self) -> None:
        self.cue_store.mark_gui_closed()

    def close(self, reason: str = "session_ended_before_render") -> None:
        self.cue_store.clear(reason)
        self.fallback_console_sink.close(_render_to_display_reason(reason))


@dataclass(frozen=True)
class _CueCandidate:
    cue_type: str
    trigger_reason: str
    source_event_type: str | None
    source_state: str
    detach_state: str | None
    slip_reason: str | None
    track_state: str | None
    primary_blocked_surface: str | None
    direction: str | None
    lifecycle_kind: str
    lifecycle_signature: tuple[Any, ...]
    details: dict[str, Any]


class CueRuntime:
    """Generate edge-only cue commands and manage non-hardware display lifecycle."""

    def __init__(
        self,
        *,
        trial_id: str | int,
        cue_config: CueConfig | None = None,
        sink_config: CueSinkConfig | None = None,
        console_output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.trial_id = trial_id
        self.cue_config = cue_config or default_cue_config()
        self.sink_config = sink_config or CueSinkConfig()
        if self.sink_config.cue_sink not in CUE_SINK_CHOICES:
            raise ValueError("cue_sink must be one of: " + ", ".join(CUE_SINK_CHOICES))

        self._records: list[CueCommandResult] = []
        self._records_lock = threading.Lock()
        self._sequence = 0
        self._previous_slip_signature: tuple[Any, ...] | None = None
        self._previous_blocked_signature: tuple[Any, ...] | None = None
        self._active_display_result: CueCommandResult | None = None
        self._lifecycles: dict[str, tuple[str, tuple[Any, ...]]] = {}
        self._last_emitted_by_rate_key: dict[tuple[str, str], CueCommand] = {}
        self._suppressed_counts: Counter[str] = Counter()
        self._suppressed_reason_counts: Counter[str] = Counter()
        self.warnings: list[str] = []
        self._closed = False

        if self.sink_config.cue_sink == "none":
            self.sink: CueSink = NullCueSink()
        elif self.sink_config.cue_sink == "logging":
            self.sink = LoggingCueSink()
        elif self.sink_config.cue_sink == "console":
            self.sink = ConsoleCueSink(output_fn=console_output_fn)
        else:
            self.sink = GuiTextCueSink(
                fallback_console_sink=ConsoleCueSink(output_fn=console_output_fn)
            )

    @property
    def cue_enabled(self) -> bool:
        return self.sink_config.cue_sink != "none"

    @property
    def gui_cue_store(self) -> LatestCueStore | None:
        if isinstance(self.sink, GuiTextCueSink):
            return self.sink.cue_store
        return None

    def process_frame(
        self,
        *,
        frame_index: int | None,
        source_frame_id: str | int | None,
        sample: Any,
        trial_result: Any,
        snapshot: Any,
        terminal_frame: bool = False,
    ) -> tuple[CueCommand, ...]:
        """Observe one processed frame and submit any edge-only cue commands."""

        if self._closed or not self.cue_enabled:
            return ()
        output = trial_result.frame_output
        feedback = output.feedback_state
        if terminal_frame:
            self._clear_active("trial_ended_before_render")
            return ()
        if not _snapshot_is_valid(snapshot, feedback):
            self._clear_active("invalid_before_render")
            self._previous_slip_signature = None
            self._previous_blocked_signature = None
            return ()

        current_slip_signature = _slip_signature(trial_result)
        current_blocked_signature = _blocked_signature(trial_result)
        if bool(getattr(feedback, "recovery_frame", False)):
            self._clear_active("invalid_before_render")
            self._previous_slip_signature = current_slip_signature
            self._previous_blocked_signature = current_blocked_signature
            return ()

        candidates = self._build_candidates(
            trial_result=trial_result,
            current_slip_signature=current_slip_signature,
            current_blocked_signature=current_blocked_signature,
        )
        accepted: list[tuple[CueCommandResult, _CueCandidate]] = []
        for candidate in candidates:
            if not self._candidate_enabled(candidate):
                self._suppress(candidate.cue_type, "config")
                continue
            command = self._make_command(
                candidate=candidate,
                frame_index=frame_index,
                source_frame_id=source_frame_id,
                sample=sample,
                trial_result=trial_result,
            )
            if self._is_rate_limited(command):
                self._suppress(candidate.cue_type, "rate_limit")
                continue
            result = CueCommandResult(command=command)
            self._append_record(result)
            self._last_emitted_by_rate_key[_rate_limit_key(command)] = command
            self._lifecycles[command.cue_id] = (
                candidate.lifecycle_kind,
                candidate.lifecycle_signature,
            )
            accepted.append((result, candidate))

        contact_state = _name(output.contact_state)
        current_lifecycles = {
            "contact_enter": ("INSIDE_BLOCK",) if contact_state == "INSIDE_BLOCK" else None,
            "contact_exit": ("OUTSIDE_BLOCK",) if contact_state == "OUTSIDE_BLOCK" else None,
            "slip": current_slip_signature,
            "blocked": current_blocked_signature,
        }
        self._refresh_active_for_frame(current_lifecycles, accepted)
        self._emit_accepted(accepted)
        self._previous_slip_signature = current_slip_signature
        self._previous_blocked_signature = current_blocked_signature
        return tuple(result.command for result, _ in accepted)

    def handle_input_error(self, reason: str = "invalid_before_render") -> None:
        """Clear stale display state without advancing cue edge signatures."""

        if self._closed:
            return
        self._clear_active(reason)

    def handle_gui_closed(self) -> None:
        """Close the GUI target while allowing future gui_text cues to fallback."""

        if isinstance(self.sink, GuiTextCueSink):
            self.sink.mark_gui_closed()

    def end_trial(self) -> None:
        """Clear overlays, cancel pending output, and bound worker shutdown."""

        if self._closed:
            return
        self._clear_active("trial_ended_before_render")
        self.sink.close("trial_ended_before_render")
        self._closed = True

    def end_session(self) -> None:
        """Session-level idempotent finalization."""

        if self._closed:
            return
        self._clear_active("session_ended_before_render")
        self.sink.close("session_ended_before_render")
        self._closed = True

    def records_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Return cue records in deterministic creation order."""

        with self._records_lock:
            records = list(self._records)
        return tuple(result.to_dict() for result in records)

    def summary(self, *, cue_log_path: str | Path | None = None) -> dict[str, Any]:
        """Return JSON-safe cue summary fields."""

        records = self.records_snapshot()
        type_counts = Counter(str(record.get("cue_type", "")) for record in records)
        return {
            "cue_enabled": self.cue_enabled,
            "cue_sink": self.sink_config.cue_sink,
            "cue_mode": self.sink_config.mode,
            "is_live_cue_timing": self.sink_config.is_live_cue_timing,
            "cue_log_path": (
                str(cue_log_path)
                if self.cue_enabled and cue_log_path is not None
                else None
            ),
            "cue_count": len(records),
            "cue_type_counts": dict(type_counts),
            "suppressed_cue_count": sum(self._suppressed_counts.values()),
            "suppressed_cue_type_counts": dict(self._suppressed_counts),
            "suppressed_cue_reason_counts": dict(self._suppressed_reason_counts),
            "effective_cue_config": self.cue_config.to_dict(),
            "cue_warnings": list(self.warnings),
        }

    def write_log(self, path: str | Path) -> Path | None:
        """Write final cue records for non-none sinks."""

        if not self.cue_enabled:
            return None
        return write_cue_log_csv(path, self.records_snapshot())

    def _build_candidates(
        self,
        *,
        trial_result: Any,
        current_slip_signature: tuple[Any, ...] | None,
        current_blocked_signature: tuple[Any, ...] | None,
    ) -> list[_CueCandidate]:
        output = trial_result.frame_output
        haptic = trial_result.haptic_feedback_state
        event_types = {
            str(getattr(event, "event_type", "")).lower()
            for event in getattr(trial_result, "events", ()) or ()
        }
        candidates: list[_CueCandidate] = []

        for event in getattr(trial_result, "events", ()) or ():
            event_type = str(getattr(event, "event_type", "")).lower()
            if event_type == "contact_enter":
                candidates.append(
                    _CueCandidate(
                        cue_type="contact_enter",
                        trigger_reason="edge_start",
                        source_event_type="contact_enter",
                        source_state="INSIDE_BLOCK",
                        detach_state=None,
                        slip_reason=None,
                        track_state=_name(output.feedback_state.track_state),
                        primary_blocked_surface=None,
                        direction=None,
                        lifecycle_kind="contact_enter",
                        lifecycle_signature=("INSIDE_BLOCK",),
                        details={},
                    )
                )
            elif event_type == "contact_exit":
                details = getattr(event, "details", {}) or {}
                detach_state = str(details.get("detach_state") or _name(output.feedback_state.detach_state) or "")
                candidates.append(
                    _CueCandidate(
                        cue_type="contact_exit",
                        trigger_reason="edge_start",
                        source_event_type="contact_exit",
                        source_state="OUTSIDE_BLOCK",
                        detach_state=detach_state or None,
                        slip_reason=None,
                        track_state=_name(output.feedback_state.track_state),
                        primary_blocked_surface=None,
                        direction=None,
                        lifecycle_kind="contact_exit",
                        lifecycle_signature=("OUTSIDE_BLOCK",),
                        details={"detach_state": detach_state or None},
                    )
                )

        blocked_trigger = _signature_trigger(
            self._previous_blocked_signature,
            current_blocked_signature,
        )
        slip_trigger = _signature_trigger(
            self._previous_slip_signature,
            current_slip_signature,
        )
        direction = _direction_from_result(trial_result)
        blocked_active = bool(getattr(haptic, "blocked_force_active", False))
        slip_active = bool(getattr(haptic, "slip_active", False))

        if blocked_active:
            if direction is not None and blocked_trigger is not None:
                blocked_candidate = _blocked_candidate(
                    trial_result,
                    direction=direction,
                    trigger_reason=blocked_trigger,
                    source_event_type=(
                        "blocked_force_start"
                        if blocked_trigger == "edge_start" and "blocked_force_start" in event_types
                        else None
                    ),
                    signature=current_blocked_signature,
                )
                candidates.append(blocked_candidate)
            if (
                direction is not None
                and not self.cue_config.enable_blocked_directional_cue
                and slip_active
                and slip_trigger is not None
            ):
                candidates.append(
                    _slip_candidate(
                        trial_result,
                        trigger_reason=slip_trigger,
                        source_event_type=(
                            "slip_start"
                            if slip_trigger == "edge_start" and "slip_start" in event_types
                            else None
                        ),
                        signature=current_slip_signature,
                    )
                )
            elif direction is None and slip_active and slip_trigger is not None:
                candidates.append(
                    _slip_candidate(
                        trial_result,
                        trigger_reason=slip_trigger,
                        source_event_type=(
                            "slip_start"
                            if slip_trigger == "edge_start" and "slip_start" in event_types
                            else None
                        ),
                        signature=current_slip_signature,
                    )
                )
        elif slip_active and slip_trigger is not None:
            candidates.append(
                _slip_candidate(
                    trial_result,
                    trigger_reason=slip_trigger,
                    source_event_type=(
                        "slip_start"
                        if slip_trigger == "edge_start" and "slip_start" in event_types
                        else None
                    ),
                    signature=current_slip_signature,
                )
            )
        return candidates

    def _candidate_enabled(self, candidate: _CueCandidate) -> bool:
        if candidate.cue_type == "contact_enter":
            return self.cue_config.enable_contact_cue
        if candidate.cue_type == "contact_exit":
            return self.cue_config.enable_contact_exit_cue
        if candidate.cue_type == "blocked_directional":
            return self.cue_config.enable_blocked_directional_cue
        return self.cue_config.enable_slip_cue

    def _make_command(
        self,
        *,
        candidate: _CueCandidate,
        frame_index: int | None,
        source_frame_id: str | int | None,
        sample: Any,
        trial_result: Any,
    ) -> CueCommand:
        sequence = self._sequence
        self._sequence += 1
        cue_id = f"{_safe_id(self.trial_id)}:cue:{sequence:06d}"
        return CueCommand(
            cue_sequence_index=sequence,
            cue_id=cue_id,
            trial_id=self.trial_id,
            cue_type=candidate.cue_type,
            requested_cue_sink=self.sink_config.cue_sink,
            mode=self.sink_config.mode,
            is_live_cue_timing=self.sink_config.is_live_cue_timing,
            trigger_reason=candidate.trigger_reason,
            source_frame_index=frame_index,
            source_frame_id=source_frame_id,
            source_sample_time=_optional_float(getattr(sample, "time", None)),
            source_trial_time=_optional_float(getattr(trial_result, "time_since_prompt", None)),
            source_event_type=candidate.source_event_type,
            source_state=candidate.source_state,
            detach_state=candidate.detach_state,
            slip_reason=candidate.slip_reason,
            track_state=candidate.track_state,
            primary_blocked_surface=candidate.primary_blocked_surface,
            direction=candidate.direction,
            pattern=candidate.cue_type,
            intensity=None,
            duration_ms=None,
            message=_cue_message(candidate.cue_type, candidate.direction),
            created_monotonic_ms=time.monotonic() * 1000.0,
            is_hardware_haptic=False,
            details=candidate.details,
        )

    def _is_rate_limited(self, command: CueCommand) -> bool:
        interval = self.cue_config.min_cue_interval_ms
        if interval <= 0.0:
            return False
        previous = self._last_emitted_by_rate_key.get(_rate_limit_key(command))
        if previous is None:
            return False
        if (
            command.source_trial_time is not None
            and previous.source_trial_time is not None
            and command.source_trial_time >= previous.source_trial_time
        ):
            elapsed_ms = (command.source_trial_time - previous.source_trial_time) * 1000.0
        else:
            elapsed_ms = command.created_monotonic_ms - previous.created_monotonic_ms
            self._warn_once(
                "cue rate limit fell back to created_monotonic_ms because source_trial_time "
                "was unavailable or non-monotonic."
            )
        return elapsed_ms < interval

    def _emit_accepted(self, accepted: list[tuple[CueCommandResult, _CueCandidate]]) -> None:
        if not accepted:
            return
        if isinstance(self.sink, LoggingCueSink):
            for result, _ in accepted:
                self._safe_submit(result)
            return

        ranked = sorted(
            accepted,
            key=lambda item: (-CUE_PRIORITY.get(item[0].command.cue_type, 0), item[0].command.cue_sequence_index),
        )
        selected_result, _ = ranked[0]
        for result, _ in ranked[1:]:
            self._accept_not_displayed(result, "same_frame_lower_priority")

        active = self._active_display_result
        if active is not None:
            active_priority = CUE_PRIORITY.get(active.command.cue_type, 0)
            selected_priority = CUE_PRIORITY.get(selected_result.command.cue_type, 0)
            if selected_priority < active_priority:
                self._accept_not_displayed(selected_result, "active_higher_priority")
                return
            reason = (
                "higher_priority_before_render"
                if selected_priority > active_priority
                else "cue_replaced_before_render"
            )
            self.sink.cancel(active, self._sink_reason(reason))

        self._safe_submit(selected_result)
        if selected_result.success is True:
            self._active_display_result = selected_result

    def _accept_not_displayed(self, result: CueCommandResult, reason: str) -> None:
        modality = self.sink.default_modality()
        fallback_reason = "gui_closed" if isinstance(self.sink, GuiTextCueSink) and modality == "console_text" else None
        result.mark_accepted(
            modality,
            display_status="not_displayed_lower_priority",
            fallback_reason=fallback_reason,
        )
        result.mark_not_displayed(reason, status="not_displayed_lower_priority")

    def _safe_submit(self, result: CueCommandResult) -> None:
        try:
            self.sink.submit(result)
        except Exception as exc:
            result.mark_submission_error(str(exc), modality=self.sink.default_modality())

    def _refresh_active_for_frame(
        self,
        current_lifecycles: dict[str, tuple[Any, ...] | None],
        accepted: list[tuple[CueCommandResult, _CueCandidate]],
    ) -> None:
        active = self._active_display_result
        if active is None:
            return
        lifecycle = self._lifecycles.get(active.command.cue_id)
        if lifecycle is None:
            return
        kind, signature = lifecycle
        if current_lifecycles.get(kind) == signature:
            return
        replacing_kinds = {candidate.lifecycle_kind for _, candidate in accepted}
        if kind in replacing_kinds:
            return
        self._clear_active("state_cleared_before_render")

    def _clear_active(self, reason: str) -> None:
        active = self._active_display_result
        if active is None:
            return
        self.sink.cancel(active, self._sink_reason(reason))
        self._active_display_result = None

    def _append_record(self, result: CueCommandResult) -> None:
        with self._records_lock:
            self._records.append(result)

    def _suppress(self, cue_type: str, reason: str) -> None:
        self._suppressed_counts[cue_type] += 1
        self._suppressed_reason_counts[reason] += 1

    def _warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _sink_reason(self, reason: str) -> str:
        if isinstance(self.sink, ConsoleCueSink):
            return _render_to_display_reason(reason)
        return reason


def write_cue_log_csv(
    path: str | Path,
    records: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> Path:
    """Write cue records in creation order, including a header-only empty log."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CUE_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow({field: _csv_value(record.get(field)) for field in CUE_CSV_FIELDS})
    return output


def _snapshot_is_valid(snapshot: Any, feedback: Any) -> bool:
    return bool(
        getattr(snapshot, "tracker_valid", False)
        and getattr(snapshot, "hand_valid", False)
        and getattr(snapshot, "pinch_valid", False)
        and getattr(snapshot, "pinch_center_task", None) is not None
        and getattr(feedback, "tracking_valid", False)
    )


def _slip_signature(trial_result: Any) -> tuple[Any, ...] | None:
    haptic = trial_result.haptic_feedback_state
    if not bool(getattr(haptic, "slip_active", False)):
        return None
    reason = _name(getattr(haptic, "slip_reason", None))
    cue_type = _slip_cue_type(reason)
    if cue_type is None:
        return None
    return cue_type, reason, reason


def _blocked_signature(trial_result: Any) -> tuple[Any, ...] | None:
    haptic = trial_result.haptic_feedback_state
    if not bool(getattr(haptic, "blocked_force_active", False)):
        return None
    output = trial_result.frame_output
    direction = _direction_from_result(trial_result)
    return (
        "blocked_directional",
        _name(output.feedback_state.track_state),
        _name(getattr(haptic, "primary_blocked_surface", None)),
        direction,
    )


def _signature_trigger(
    previous: tuple[Any, ...] | None,
    current: tuple[Any, ...] | None,
) -> str | None:
    if current is None:
        return None
    if previous is None:
        return "edge_start"
    if previous != current:
        return "state_signature_changed"
    return None


def _blocked_candidate(
    trial_result: Any,
    *,
    direction: str | None,
    trigger_reason: str,
    source_event_type: str | None,
    signature: tuple[Any, ...] | None,
) -> _CueCandidate:
    output = trial_result.frame_output
    haptic = trial_result.haptic_feedback_state
    return _CueCandidate(
        cue_type="blocked_directional",
        trigger_reason=trigger_reason,
        source_event_type=source_event_type,
        source_state="TRACK_BLOCKED",
        detach_state=_none_if_empty(_name(output.feedback_state.detach_state)),
        slip_reason=_none_if_empty(_name(getattr(haptic, "slip_reason", None))),
        track_state=_none_if_empty(_name(output.feedback_state.track_state)),
        primary_blocked_surface=_none_if_empty(_name(getattr(haptic, "primary_blocked_surface", None))),
        direction=direction,
        lifecycle_kind="blocked",
        lifecycle_signature=signature or (),
        details=_continuous_details(trial_result),
    )


def _slip_candidate(
    trial_result: Any,
    *,
    trigger_reason: str,
    source_event_type: str | None,
    signature: tuple[Any, ...] | None,
) -> _CueCandidate:
    output = trial_result.frame_output
    haptic = trial_result.haptic_feedback_state
    slip_reason = _name(getattr(haptic, "slip_reason", None))
    cue_type = _slip_cue_type(slip_reason) or "slip_track_blocked"
    return _CueCandidate(
        cue_type=cue_type,
        trigger_reason=trigger_reason,
        source_event_type=source_event_type,
        source_state=slip_reason or "TRACK_BLOCKED",
        detach_state=_none_if_empty(_name(output.feedback_state.detach_state)),
        slip_reason=_none_if_empty(slip_reason),
        track_state=_none_if_empty(_name(output.feedback_state.track_state)),
        primary_blocked_surface=_none_if_empty(_name(getattr(haptic, "primary_blocked_surface", None))),
        direction=None,
        lifecycle_kind="slip",
        lifecycle_signature=signature or (),
        details=_continuous_details(trial_result),
    )


def _continuous_details(trial_result: Any) -> dict[str, Any]:
    haptic = trial_result.haptic_feedback_state
    output = trial_result.frame_output
    blocked_info = getattr(output.feedback_state, "blocked_info", None)
    return {
        "force_vector_task": _vec_to_list(getattr(haptic, "force_vector_task", None)),
        "force_magnitude": _optional_float(getattr(haptic, "force_magnitude", None)),
        "primary_blocked_amount": _optional_float(getattr(haptic, "primary_blocked_amount", None)),
        "all_blocked_surfaces": [
            _name(surface)
            for surface in getattr(blocked_info, "all_blocked_surfaces", ()) or ()
        ],
    }


def _direction_from_result(trial_result: Any) -> str | None:
    haptic = trial_result.haptic_feedback_state
    surface = _name(getattr(haptic, "primary_blocked_surface", None))
    if surface:
        return _opposite_direction(surface)
    track_state = _name(trial_result.frame_output.feedback_state.track_state)
    if track_state.startswith("BLOCKED_"):
        return _opposite_direction(track_state.removeprefix("BLOCKED_"))
    return None


def _opposite_direction(value: str) -> str | None:
    mapping = {
        "X_POS": "X_NEG",
        "X_NEG": "X_POS",
        "Y_POS": "Y_NEG",
        "Y_NEG": "Y_POS",
        "Z_POS": "Z_NEG",
        "Z_NEG": "Z_POS",
    }
    return mapping.get(value)


def _slip_cue_type(reason: str) -> str | None:
    if reason == "PINCH_INSUFFICIENT":
        return "slip_pinch_insufficient"
    if reason == "TRACK_BLOCKED":
        return "slip_track_blocked"
    return None


def _cue_message(cue_type: str, direction: str | None) -> str:
    messages = {
        "contact_enter": "CONTACT",
        "contact_exit": "CONTACT EXIT",
        "slip_pinch_insufficient": "PINCH INSUFFICIENT",
        "slip_track_blocked": "TRACK BLOCKED",
    }
    if cue_type == "blocked_directional":
        return f"MOVE {direction}" if direction else "TRACK BLOCKED"
    return messages[cue_type]


def _rate_limit_key(command: CueCommand) -> tuple[str, str]:
    return command.cue_type, str(command.direction or command.source_state or "")


def _safe_id(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return normalized or "trial"


def _render_to_display_reason(reason: str) -> str:
    return str(reason).replace("_before_render", "_before_display")


def _name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return value.name
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _none_if_empty(value: str) -> str | None:
    return value if value else None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list | tuple):
        return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)
    return value


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    return str(value)
