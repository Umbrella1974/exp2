"""Stage 1 haptic command routing and logging."""

from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from data_models import Vec3
from haptic_config import HapticConfig, default_haptic_config, normalize_direction_key
from haptic_tcp_worker import MatrixSendStep, MatrixTcpWorker
from matrix_haptic_protocol import encode_matrix_channel_packet
from vibration_haptic_protocol import (
    encode_vibration_line_command,
    vibration_payload_to_log_string,
)
from vibration_tcp_worker import VibrationTcpLineWorker


HAPTIC_CSV_FIELDS = [
    "haptic_sequence_index",
    "haptic_id",
    "cue_id",
    "cue_sequence_index",
    "trial_id",
    "target_device",
    "target_transport",
    "source_frame_id",
    "source_frame_index",
    "source_trial_time",
    "cue_type",
    "haptic_type",
    "haptic_phase",
    "direction",
    "primary_blocked_surface",
    "correction_direction",
    "blocked_surface_set",
    "correction_direction_set",
    "matrix_filtered_blocked_surface_set",
    "matrix_filtered_correction_direction_set",
    "matrix_direction_used",
    "matrix_direction_semantics",
    "matrix_ignored_direction_axes",
    "matrix_output_key",
    "previous_matrix_output_key",
    "next_matrix_output_key",
    "channel_list",
    "vibration_command",
    "vibration_command_label",
    "sent_payload",
    "payload_hex",
    "created_monotonic_ms",
    "queued_monotonic_ms",
    "sent_monotonic_ms",
    "success",
    "send_status",
    "not_sent_reason",
    "error",
    "mode",
    "is_live_haptic_timing",
    "details_json",
]


@dataclass
class HapticCommandRecord:
    """One haptic command/log row."""

    haptic_sequence_index: int
    haptic_id: str
    cue_id: str | None
    cue_sequence_index: int | None
    trial_id: str | int
    target_device: str
    target_transport: str | None
    source_frame_id: str | int | None
    source_frame_index: int | None
    source_trial_time: float | None
    cue_type: str
    haptic_type: str
    haptic_phase: str
    direction: str | None
    primary_blocked_surface: str | None = None
    correction_direction: str | None = None
    blocked_surface_set: str | None = None
    correction_direction_set: str | None = None
    matrix_filtered_blocked_surface_set: str | None = None
    matrix_filtered_correction_direction_set: str | None = None
    matrix_direction_used: str | None = None
    matrix_direction_semantics: str | None = None
    matrix_ignored_direction_axes: str | None = None
    matrix_output_key: str | None = None
    previous_matrix_output_key: str | None = None
    next_matrix_output_key: str | None = None
    channel_list: list[int] = field(default_factory=list)
    vibration_command: int | None = None
    vibration_command_label: str | None = None
    sent_payload: str | None = None
    payload_hex: str | None = None
    created_monotonic_ms: float | None = None
    queued_monotonic_ms: float | None = None
    sent_monotonic_ms: float | None = None
    success: bool | None = None
    send_status: str = ""
    not_sent_reason: str | None = None
    error: str | None = None
    mode: str = "live"
    is_live_haptic_timing: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["details_json"] = json.dumps(
            _json_safe(payload.pop("details")),
            ensure_ascii=False,
            sort_keys=True,
        )
        payload["channel_list"] = json.dumps(list(self.channel_list), separators=(",", ":"))
        return payload


@dataclass(frozen=True)
class _FrameContext:
    frame_index: int | None
    source_frame_id: str | int | None
    source_trial_time: float | None
    source_sample_time: float | None


@dataclass(frozen=True)
class _VibrationOneShotFrameState:
    one_shot_sent: bool = False
    contact_exit_queued: bool = False


class HapticStartupError(RuntimeError):
    """Raised when required haptic hardware cannot start before trial."""

    def __init__(self, message: str, *, target_device: str = "haptic") -> None:
        super().__init__(message)
        self.target_device = target_device


@dataclass(frozen=True)
class _MatrixBlockedSignature:
    direction_key: str | None
    primary_surface: str | None
    primary_correction: str | None
    blocked_surface_set: str | None
    correction_direction_set: str | None
    filtered_blocked_surface_set: str | None
    filtered_correction_direction_set: str | None
    ignored_direction_axes: str | None
    semantics: str
    track_state: str
    channels: tuple[int, ...]
    missing_reason: str | None = None


@dataclass(frozen=True)
class _MatrixMainOutput:
    output_key: str | None
    cue_type: str
    haptic_type: str
    channels: tuple[int, ...]
    missing_reason: str | None = None
    blocked: _MatrixBlockedSignature | None = None


class HapticRuntime:
    """Observe existing trial states and route haptic commands.

    This class does not decide whether contact/slip/blocked is true. It only
    consumes fields already produced by TrialController/BlockController and
    records/routes target-device commands.
    """

    def __init__(
        self,
        *,
        trial_id: str | int,
        haptic_config: HapticConfig | None = None,
        trial_config: dict[str, Any] | None = None,
        mode: str = "live",
        is_live_haptic_timing: bool = True,
        worker_factory: Callable[..., MatrixTcpWorker] | None = None,
        vibration_worker_factory: Callable[..., VibrationTcpLineWorker] | None = None,
        monotonic_ms_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.trial_id = trial_id
        self.haptic_config = haptic_config or default_haptic_config()
        self.trial_config = trial_config or {}
        self.mode = mode
        self.is_live_haptic_timing = bool(is_live_haptic_timing)
        self.worker_factory = worker_factory or MatrixTcpWorker
        self.vibration_worker_factory = (
            vibration_worker_factory or VibrationTcpLineWorker
        )
        self.monotonic_ms_fn = monotonic_ms_fn or (lambda: time.monotonic() * 1000.0)
        self.sleep_fn = sleep_fn or time.sleep
        self.warnings: list[str] = []
        self.connect_error: str | None = None
        self.matrix_connect_error: str | None = None
        self.vibration_connect_error: str | None = None
        self.matrix_startup_connected = False
        self.vibration_startup_connected = False
        self._records: list[HapticCommandRecord] = []
        self._sequence = 0
        self._matrix_worker: MatrixTcpWorker | None = None
        self._vibration_worker: VibrationTcpLineWorker | None = None
        self._started = False
        self._session_ended = False
        self._trial_ended = False
        self._previous_slip_signature: tuple[Any, ...] | None = None
        self._previous_gated_slip_signature: tuple[Any, ...] | None = None
        self._pending_slip_reassert_after_one_shot: tuple[Any, ...] | None = None
        self._pending_slip_reassert_frame_index: int | None = None
        self._active_matrix_output: _MatrixMainOutput | None = None
        self._previous_matrix_output_key: str | None = None
        self._matrix_output_key_lock = Lock()
        self._last_matrix_send_monotonic_ms: float | None = None
        self._last_context: _FrameContext | None = None
        self._target_box = _target_box_from_trial_config(self.trial_config)
        self._warned_missing_target_region = False
        self._has_valid_grab_history = False
        self._need_pinch_requires_valid_grab_count = 0

    @property
    def haptic_enabled(self) -> bool:
        return bool(self.haptic_config.enabled)

    @property
    def matrix_haptic_enabled(self) -> bool:
        return bool(self.haptic_config.matrix_enabled)

    @property
    def vibration_haptic_enabled(self) -> bool:
        return bool(self.haptic_config.vibration_enabled)

    def start(self) -> None:
        """Start required hardware before trial begins."""

        if self._started:
            return
        self._started = True
        self._clear_valid_grab_history()
        if not self.haptic_enabled:
            return
        if self.matrix_haptic_enabled:
            self._start_matrix_worker()
        if self.vibration_haptic_enabled:
            self._start_vibration_worker()

    def _start_matrix_worker(self) -> None:
        matrix = self.haptic_config.matrix
        try:
            self._matrix_worker = self.worker_factory(
                host=matrix.host,
                port=matrix.port,
                connect_timeout_s=matrix.connect_timeout_s,
                send_timeout_s=matrix.send_timeout_s,
                max_queue_size=matrix.max_queue_size,
                latest_only=matrix.latest_only,
            )
            self._matrix_worker.start()
            self.matrix_startup_connected = True
        except Exception as exc:
            self.matrix_connect_error = str(exc)
            self.connect_error = self.connect_error or str(exc)
            message = f"matrix haptic connect failed: {exc}"
            if matrix.required:
                raise HapticStartupError(message, target_device="matrix") from exc
            self.warnings.append(message)
            self._matrix_worker = None
            return
        if matrix.startup_settle_seconds > 0.0:
            self.sleep_fn(matrix.startup_settle_seconds)

    def _start_vibration_worker(self) -> None:
        vibration = self.haptic_config.vibration
        try:
            self._vibration_worker = self.vibration_worker_factory(
                host=vibration.host,
                port=vibration.port,
                connect_timeout_s=vibration.connect_timeout_s,
                send_timeout_s=vibration.send_timeout_s,
                max_queue_size=vibration.max_queue_size,
            )
            self._vibration_worker.start()
            self.vibration_startup_connected = True
        except Exception as exc:
            self.vibration_connect_error = str(exc)
            self.connect_error = self.connect_error or str(exc)
            message = f"vibration haptic connect failed: {exc}"
            if vibration.required:
                raise HapticStartupError(message, target_device="vibration") from exc
            self.warnings.append(message)
            self._vibration_worker = None
            return
        if vibration.startup_settle_seconds > 0.0:
            self.sleep_fn(vibration.startup_settle_seconds)

    def process_frame(
        self,
        *,
        frame_index: int | None,
        source_frame_id: str | int | None,
        sample: Any,
        trial_result: Any,
        snapshot: Any,
        terminal_frame: bool = False,
    ) -> tuple[HapticCommandRecord, ...]:
        """Process one frame's existing haptic/contact/blocked state."""

        del snapshot
        if not self.haptic_enabled or self._session_ended:
            return ()
        context = _FrameContext(
            frame_index=frame_index,
            source_frame_id=source_frame_id,
            source_trial_time=_optional_float(getattr(trial_result, "time_since_prompt", None)),
            source_sample_time=_optional_float(getattr(sample, "time", None)),
        )
        self._last_context = context
        if terminal_frame:
            self.end_trial(reason="terminal_frame")
            return ()

        output = getattr(trial_result, "frame_output", None)
        feedback = getattr(output, "feedback_state", None)
        if feedback is None:
            self.handle_input_error("missing_frame_feedback")
            return ()
        if not bool(getattr(feedback, "tracking_valid", True)) or bool(
            getattr(feedback, "recovery_frame", False)
        ):
            self.handle_input_error("invalid_before_haptic")
            return ()

        before = len(self._records)
        self._observe_valid_grab(output)
        one_shot_state = self._process_contact_events(context, trial_result)
        self._process_slip_state(context, trial_result, one_shot_state=one_shot_state)
        self._process_matrix_state(context, trial_result)
        return tuple(self._records[before:])

    def handle_input_error(self, reason: str = "invalid_before_haptic") -> None:
        """End active haptic states when the input stream becomes invalid."""

        if not self.haptic_enabled:
            return
        self._end_active_states(reason=reason)

    def end_trial(self, reason: str = "trial_ended_no_hardware_clear") -> None:
        """Record state_end commands without assuming hardware clear semantics."""

        if self._trial_ended:
            return
        self._trial_ended = True
        if self.haptic_enabled:
            self._end_active_states(reason=reason)

    def end_session(self) -> None:
        """Finalize runtime state and stop the matrix worker."""

        if self._session_ended:
            return
        self.end_trial(reason="session_ended_no_hardware_clear")
        if self._matrix_worker is not None:
            self._matrix_worker.stop()
        if self._vibration_worker is not None:
            self._vibration_worker.stop()
        self._session_ended = True

    def records_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Return JSON-safe haptic records in creation order."""

        return tuple(record.to_dict() for record in self._records)

    def summary(self, *, haptic_log_path: str | Path | None = None) -> dict[str, Any]:
        records = self.records_snapshot()
        type_counts = Counter(str(record.get("haptic_type", "")) for record in records)
        return {
            "haptic_enabled": self.haptic_enabled,
            "matrix_haptic_enabled": self.matrix_haptic_enabled,
            "vibration_haptic_enabled": self.vibration_haptic_enabled,
            "haptic_mode": self.mode,
            "is_live_haptic_timing": self.is_live_haptic_timing,
            "haptic_count": len(records),
            "haptic_type_counts": dict(type_counts),
            "need_pinch_requires_valid_grab_count": int(
                self._need_pinch_requires_valid_grab_count
            ),
            "effective_haptic_config": self.haptic_config.to_dict(),
            "haptic_command_log_path": (
                str(haptic_log_path)
                if self.haptic_enabled and haptic_log_path is not None
                else None
            ),
            "haptic_warnings": list(self.warnings),
            "haptic_connect_error": self.connect_error,
            "matrix_haptic_transport": self.haptic_config.matrix.transport,
            "vibration_haptic_transport": self.haptic_config.vibration.transport,
            "matrix_haptic_startup_connected": bool(self.matrix_startup_connected),
            "vibration_haptic_startup_connected": bool(
                self.vibration_startup_connected
            ),
            "matrix_haptic_connect_error": self.matrix_connect_error,
            "vibration_haptic_connect_error": self.vibration_connect_error,
            "matrix_haptic_connected": bool(
                self._matrix_worker is not None and self._matrix_worker.connected
            ),
            "vibration_haptic_connected": bool(
                self._vibration_worker is not None and self._vibration_worker.connected
            ),
        }

    def write_log(self, path: str | Path) -> Path | None:
        """Write haptic command records when haptic is enabled."""

        if not self.haptic_enabled:
            return None
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HAPTIC_CSV_FIELDS)
            writer.writeheader()
            for record in self.records_snapshot():
                writer.writerow({field: record.get(field, "") for field in HAPTIC_CSV_FIELDS})
        return target

    def _process_contact_events(
        self,
        context: _FrameContext,
        trial_result: Any,
    ) -> _VibrationOneShotFrameState:
        if not self.vibration_haptic_enabled:
            self._clear_valid_grab_history_for_events(trial_result)
            return _VibrationOneShotFrameState()
        one_shot_sent = False
        contact_exit_queued = False
        for event in getattr(trial_result, "events", ()) or ():
            event_type = _event_type_name(getattr(event, "event_type", ""))
            if event_type == "contact_enter":
                enabled = self.haptic_config.vibration.enable_contact
                interrupted_slip = self._one_shot_would_interrupt_slip()
                record = self._record_vibration_command(
                    context,
                    cue_type="contact_enter",
                    haptic_type="vibration_contact_enter",
                    haptic_phase="one_shot",
                    command_label="contact_enter",
                    enabled=enabled,
                    not_sent_reason=None if enabled else "contact_disabled",
                    details={
                        "source_event_type": "contact_enter",
                        "interrupted_slip": interrupted_slip,
                    },
                )
                if _record_was_queued_or_sent(record):
                    one_shot_sent = True
                    self._schedule_slip_reassert_after_one_shot(
                        context,
                        interrupted_slip=interrupted_slip,
                    )
            elif event_type == "contact_exit":
                enabled = self.haptic_config.vibration.enable_release
                details = getattr(event, "details", {}) or {}
                interrupted_slip = self._one_shot_would_interrupt_slip()
                record = self._record_vibration_command(
                    context,
                    cue_type="contact_exit",
                    haptic_type="vibration_contact_exit",
                    haptic_phase="one_shot",
                    command_label="contact_exit",
                    enabled=enabled,
                    not_sent_reason=None if enabled else "release_disabled",
                    details={
                        "source_event_type": "contact_exit",
                        "detach_state": details.get("detach_state"),
                        "interrupted_slip": interrupted_slip,
                    },
                )
                if _record_was_queued_or_sent(record):
                    one_shot_sent = True
                    contact_exit_queued = True
                    self._schedule_slip_reassert_after_one_shot(
                        context,
                        interrupted_slip=interrupted_slip,
                    )
                self._clear_valid_grab_history()
            elif event_type in {"active_release", "forced_detach", "unexpected_detach"}:
                self._clear_valid_grab_history()
        return _VibrationOneShotFrameState(
            one_shot_sent=one_shot_sent,
            contact_exit_queued=contact_exit_queued,
        )

    def _process_slip_state(
        self,
        context: _FrameContext,
        trial_result: Any,
        *,
        one_shot_state: _VibrationOneShotFrameState,
    ) -> None:
        signature = self._slip_signature(trial_result)
        if signature is None:
            if self._previous_slip_signature is not None:
                self._record_slip_end(
                    context,
                    self._previous_slip_signature,
                    "slip_inactive",
                    covered_by_contact_exit=one_shot_state.contact_exit_queued,
                )
            self._previous_slip_signature = None
            self._previous_gated_slip_signature = None
            self._clear_pending_slip_reassert()
            return
        if self._slip_requires_valid_grab_gate(signature):
            if self._previous_slip_signature is not None:
                self._record_slip_end(
                    context,
                    self._previous_slip_signature,
                    "need_pinch_requires_valid_grab",
                    covered_by_contact_exit=one_shot_state.contact_exit_queued,
                )
            self._previous_slip_signature = None
            self._clear_pending_slip_reassert()
            phase = None
            if self._previous_gated_slip_signature is None:
                phase = "state_start"
            elif signature != self._previous_gated_slip_signature:
                phase = "state_update"
            if phase is not None:
                self._record_slip_command(
                    context,
                    signature,
                    phase,
                    gated_by_valid_grab=True,
                )
            self._previous_gated_slip_signature = signature
            return
        self._previous_gated_slip_signature = None
        if one_shot_state.one_shot_sent and self.haptic_config.vibration.one_shot_interrupts_slip:
            if self.haptic_config.vibration.reassert_slip_after_one_shot:
                self._pending_slip_reassert_after_one_shot = signature
                self._pending_slip_reassert_frame_index = context.frame_index
            self._previous_slip_signature = signature
            return
        if self._pending_slip_reassert_after_one_shot is not None:
            pending = self._pending_slip_reassert_after_one_shot
            if signature == pending:
                if context.frame_index == self._pending_slip_reassert_frame_index:
                    self._previous_slip_signature = signature
                    return
                self._record_slip_command(
                    context,
                    signature,
                    "state_reassert",
                    details={"reassert_after_one_shot": True},
                )
                self._clear_pending_slip_reassert()
                self._previous_slip_signature = signature
                return
            self._clear_pending_slip_reassert()
        phase = None
        if self._previous_slip_signature is None:
            phase = "state_start"
        elif signature != self._previous_slip_signature:
            phase = "state_update"
        if phase is not None:
            self._record_slip_command(context, signature, phase)
        self._previous_slip_signature = signature

    def _process_matrix_state(self, context: _FrameContext, trial_result: Any) -> None:
        if not self.matrix_haptic_enabled:
            return
        output = self._resolve_matrix_main_output(trial_result)
        if output is None:
            self._end_matrix_state(context, "matrix_output_inactive")
            return

        now_ms = self.monotonic_ms_fn()
        mode = self.haptic_config.matrix.feedback_mode
        phase: str | None = None
        if self._active_matrix_output is None:
            phase = "state_start"
        elif output != self._active_matrix_output:
            phase = "state_update"
        elif mode == "continuous_resend":
            last = self._last_matrix_send_monotonic_ms
            interval = self.haptic_config.matrix.resend_interval_ms
            if last is None or now_ms - last >= interval:
                phase = "state_update"

        if phase is not None:
            self._queue_matrix_main_output(context, output, phase)
            self._last_matrix_send_monotonic_ms = now_ms
        self._active_matrix_output = output

    def _resolve_matrix_main_output(self, trial_result: Any) -> _MatrixMainOutput | None:
        blocked = self._blocked_signature(trial_result)
        if blocked is not None:
            output_key = (
                f"blocked:{blocked.direction_key}"
                if blocked.direction_key
                else None
            )
            return _MatrixMainOutput(
                output_key=output_key,
                cue_type="blocked_directional",
                haptic_type="matrix_blocked_direction",
                channels=blocked.channels,
                missing_reason=blocked.missing_reason,
                blocked=blocked,
            )

        output = getattr(trial_result, "frame_output", None)
        contact_state = _name(getattr(output, "contact_state", None))
        pinch_state = _name(getattr(output, "pinch_state", None))
        matrix = self.haptic_config.matrix
        if (
            contact_state == "INSIDE_BLOCK"
            and pinch_state == "PINCH_INSUFFICIENT"
            and matrix.pinch_insufficient_feedback.enabled
        ):
            channels = tuple(matrix.pinch_insufficient_feedback.channel_list)
            return _MatrixMainOutput(
                output_key="pinch_insufficient",
                cue_type="pinch_insufficient",
                haptic_type="matrix_pinch_insufficient",
                channels=channels,
                missing_reason=None if channels else "no_channel_mapping",
            )
        if (
            contact_state == "INSIDE_BLOCK"
            and pinch_state == "PINCH_VALID"
            and matrix.contact_valid_feedback.enabled
        ):
            channels = tuple(matrix.contact_valid_feedback.channel_list)
            return _MatrixMainOutput(
                output_key="contact_valid",
                cue_type="contact_valid",
                haptic_type="matrix_contact_valid",
                channels=channels,
                missing_reason=None if channels else "no_channel_mapping",
            )
        return None

    def _end_matrix_state(self, context: _FrameContext, reason: str) -> None:
        if self._active_matrix_output is None:
            return
        self._record_matrix_end(context, self._active_matrix_output, reason)
        reset_config = self.haptic_config.matrix.reset_before_output_change
        if reset_config.enabled and reset_config.apply_on_transition_to_none:
            self._queue_matrix_reset_to_none(context)
        self._active_matrix_output = None
        self._last_matrix_send_monotonic_ms = None

    def _end_active_states(self, *, reason: str) -> None:
        context = self._last_context or _FrameContext(None, None, None, None)
        if self._previous_slip_signature is not None:
            self._record_slip_end(context, self._previous_slip_signature, reason)
            self._previous_slip_signature = None
            self._clear_pending_slip_reassert()
        self._previous_gated_slip_signature = None
        self._end_matrix_state(context, reason)
        self._clear_valid_grab_history()

    def _one_shot_would_interrupt_slip(self) -> bool:
        return bool(
            self._previous_slip_signature is not None
            and self.haptic_config.vibration.one_shot_interrupts_slip
        )

    def _schedule_slip_reassert_after_one_shot(
        self,
        context: _FrameContext,
        *,
        interrupted_slip: bool,
    ) -> None:
        if not interrupted_slip:
            return
        if not self.haptic_config.vibration.reassert_slip_after_one_shot:
            return
        self._pending_slip_reassert_after_one_shot = self._previous_slip_signature
        self._pending_slip_reassert_frame_index = context.frame_index

    def _clear_pending_slip_reassert(self) -> None:
        self._pending_slip_reassert_after_one_shot = None
        self._pending_slip_reassert_frame_index = None

    def _slip_signature(self, trial_result: Any) -> tuple[Any, ...] | None:
        haptic = getattr(trial_result, "haptic_feedback_state", None)
        if not bool(getattr(haptic, "slip_active", False)):
            return None
        reason = _name(getattr(haptic, "slip_reason", None))
        in_target = self._block_center_in_target(trial_result)
        return (reason, in_target)

    def _blocked_signature(self, trial_result: Any) -> _MatrixBlockedSignature | None:
        haptic = getattr(trial_result, "haptic_feedback_state", None)
        if not bool(getattr(haptic, "blocked_force_active", False)):
            return None
        output = getattr(trial_result, "frame_output", None)
        feedback = getattr(output, "feedback_state", None)
        surface_names = _blocked_surface_names(trial_result)
        primary_surface = _name(getattr(haptic, "primary_blocked_surface", None))
        if not primary_surface and surface_names:
            primary_surface = surface_names[0]
        if not primary_surface:
            primary_surface = _surface_from_track_state(_name(getattr(feedback, "track_state", None)))
        correction_names = tuple(
            correction
            for correction in (_opposite_direction(surface) for surface in surface_names)
            if correction
        )
        primary_correction = _opposite_direction(primary_surface)
        blocked_surface_set = _direction_key(surface_names)
        correction_direction_set = _direction_key(correction_names)
        ignored_axes = self.haptic_config.matrix.ignore_direction_axes
        filtered_surface_names = _filter_directions_by_axes(surface_names, ignored_axes)
        filtered_correction_names = tuple(
            correction
            for correction in (_opposite_direction(surface) for surface in filtered_surface_names)
            if correction
        )
        filtered_blocked_surface_set = _direction_key(filtered_surface_names)
        filtered_correction_direction_set = _direction_key(filtered_correction_names)
        ignored_direction_axes = _axis_key(ignored_axes)
        semantics = self.haptic_config.matrix.direction_semantics
        direction_names = (
            filtered_surface_names
            if semantics == "blocked_surface"
            else filtered_correction_names
        )
        direction_key = _direction_key(direction_names)
        track_state = _name(getattr(feedback, "track_state", None))
        channels, missing_reason = self._matrix_channels_for_direction(
            direction_key,
            filtered=True,
            original_surface_names=surface_names,
        )
        return _MatrixBlockedSignature(
            direction_key=direction_key,
            primary_surface=primary_surface or None,
            primary_correction=primary_correction,
            blocked_surface_set=blocked_surface_set,
            correction_direction_set=correction_direction_set,
            filtered_blocked_surface_set=filtered_blocked_surface_set,
            filtered_correction_direction_set=filtered_correction_direction_set,
            ignored_direction_axes=ignored_direction_axes,
            semantics=semantics,
            track_state=track_state,
            channels=tuple(channels),
            missing_reason=missing_reason,
        )

    def _matrix_channels_for_direction(
        self,
        direction_key: str | None,
        *,
        filtered: bool = False,
        original_surface_names: tuple[str, ...] = (),
    ) -> tuple[list[int], str | None]:
        if not direction_key:
            if filtered and original_surface_names:
                return [], "direction_filtered_empty"
            return [], None
        parts = direction_key.split("+")
        if len(parts) == 1:
            return list(self.haptic_config.matrix.direction_channel_map.get(direction_key, [])), None

        if direction_key in self.haptic_config.matrix.combination_channel_map:
            combination = self.haptic_config.matrix.combination_channel_map[direction_key]
            return list(combination), None
        if self.haptic_config.matrix.missing_combination_policy == "union_single_directions":
            channels: list[int] = []
            for part in parts:
                channels.extend(self.haptic_config.matrix.direction_channel_map.get(part, []))
            return channels, None if channels else "missing_combination_mapping"
        return [], "missing_combination_mapping"

    def _block_center_in_target(self, trial_result: Any) -> bool | None:
        if self._target_box is None:
            if not self._warned_missing_target_region:
                self.warnings.append(
                    "target_region missing; haptic track-blocked slip target gating treats block as outside target_region."
                )
                self._warned_missing_target_region = True
            return None
        output = getattr(trial_result, "frame_output", None)
        block_state = getattr(output, "block_state", None)
        block_center = getattr(block_state, "center", None)
        if block_center is None:
            return None
        return _point_in_box(_to_vec3(block_center), self._target_box)

    def _record_slip_command(
        self,
        context: _FrameContext,
        signature: tuple[Any, ...],
        phase: str,
        *,
        details: dict[str, Any] | None = None,
        gated_by_valid_grab: bool = False,
    ) -> HapticCommandRecord | None:
        if not self.vibration_haptic_enabled:
            return None
        reason, in_target = signature
        enabled, disabled_reason = self._slip_enabled(str(reason or ""), in_target)
        command_details = {
            "slip_reason": reason,
            "block_center_in_target_region": in_target,
        }
        if details:
            command_details.update(details)
        if gated_by_valid_grab:
            enabled = False
            disabled_reason = "need_pinch_requires_valid_grab"
            self._need_pinch_requires_valid_grab_count += 1
            command_details.update(
                {
                    "need_pinch_active": True,
                    "pinch_insufficient_slip_policy": (
                        self.haptic_config.vibration.pinch_insufficient_slip_policy
                    ),
                    "has_valid_grab_history": bool(self._has_valid_grab_history),
                }
            )
        return self._record_vibration_command(
            context,
            cue_type=_slip_cue_type(str(reason or "")),
            haptic_type="vibration_slip",
            haptic_phase=phase,
            command_label="slip_start",
            enabled=enabled,
            not_sent_reason=disabled_reason,
            direction=None,
            details=command_details,
        )

    def _record_slip_end(
        self,
        context: _FrameContext,
        signature: tuple[Any, ...],
        reason: str,
        *,
        covered_by_contact_exit: bool = False,
    ) -> None:
        if not self.vibration_haptic_enabled:
            return
        slip_reason, in_target = signature
        enabled, disabled_reason = self._slip_enabled(str(slip_reason or ""), in_target)
        if covered_by_contact_exit:
            record = self._make_record(
                context,
                target_device="vibration",
                target_transport=self.haptic_config.vibration.transport,
                cue_type=_slip_cue_type(str(slip_reason or "")),
                haptic_type="vibration_slip",
                haptic_phase="state_end",
                direction=None,
                vibration_command=self.haptic_config.vibration.command_map.get("slip_end"),
                vibration_command_label="slip_end",
                details={
                    "slip_reason": slip_reason,
                    "block_center_in_target_region": in_target,
                    "end_reason": reason,
                    "covered_by_contact_exit": True,
                    "coverage_basis": "queued",
                },
            )
            self._finish_not_sent(record, "skipped", "covered_by_contact_exit")
            return
        self._record_vibration_command(
            context,
            cue_type=_slip_cue_type(str(slip_reason or "")),
            haptic_type="vibration_slip",
            haptic_phase="state_end",
            command_label="slip_end",
            enabled=enabled,
            not_sent_reason=disabled_reason,
            direction=None,
            priority_stop=True,
            details={
                "slip_reason": slip_reason,
                "block_center_in_target_region": in_target,
                "end_reason": reason,
            },
        )

    def _slip_enabled(self, reason: str, in_target: bool | None) -> tuple[bool, str | None]:
        vibration = self.haptic_config.vibration
        if not vibration.enable_slip:
            return False, "slip_disabled"
        if reason == "PINCH_INSUFFICIENT":
            if vibration.enable_slip_pinch_insufficient:
                return True, None
            return False, "slip_pinch_insufficient_disabled"
        if reason == "TRACK_BLOCKED":
            if bool(in_target):
                if vibration.enable_slip_track_blocked_in_target_region:
                    return True, None
                return False, "slip_track_blocked_in_target_region_disabled"
            if vibration.enable_slip_track_blocked:
                return True, None
            return False, "slip_track_blocked_disabled"
        return False, "unsupported_slip_reason"

    def _slip_requires_valid_grab_gate(self, signature: tuple[Any, ...]) -> bool:
        reason, _in_target = signature
        vibration = self.haptic_config.vibration
        return bool(
            str(reason or "") == "PINCH_INSUFFICIENT"
            and vibration.pinch_insufficient_slip_policy == "requires_prior_grab"
            and not self._has_valid_grab_history
        )

    def _observe_valid_grab(self, output: Any) -> None:
        if (
            _name(getattr(output, "contact_state", None)) == "INSIDE_BLOCK"
            and _name(getattr(output, "pinch_state", None)) == "PINCH_VALID"
        ):
            self._has_valid_grab_history = True

    def _clear_valid_grab_history_for_events(self, trial_result: Any) -> None:
        for event in getattr(trial_result, "events", ()) or ():
            event_type = _event_type_name(getattr(event, "event_type", ""))
            if event_type in {
                "contact_exit",
                "active_release",
                "forced_detach",
                "unexpected_detach",
            }:
                self._clear_valid_grab_history()
                return

    def _clear_valid_grab_history(self) -> None:
        self._has_valid_grab_history = False

    def _queue_matrix_main_output(
        self,
        context: _FrameContext,
        output: _MatrixMainOutput,
        phase: str,
    ) -> bool:
        if not self.matrix_haptic_enabled:
            return False
        invalid_status, invalid_reason = self._matrix_main_output_validation(output)
        if invalid_reason is not None:
            record = self._make_matrix_main_record(
                context,
                output,
                phase,
                previous_key=self._get_previous_matrix_output_key(),
                next_key=output.output_key,
            )
            self._finish_not_sent(record, invalid_status, invalid_reason)
            return False

        assert output.output_key is not None
        try:
            main_packet = encode_matrix_channel_packet(list(output.channels))
        except Exception as exc:
            record = self._make_matrix_main_record(
                context,
                output,
                phase,
                previous_key=self._get_previous_matrix_output_key(),
                next_key=output.output_key,
            )
            record.error = str(exc)
            self._finish_not_sent(record, "skipped", "invalid_channel_list")
            return False

        previous_key = self._get_previous_matrix_output_key()
        reset_config = self.haptic_config.matrix.reset_before_output_change
        reset_required = bool(
            reset_config.enabled
            and previous_key is not None
            and previous_key != output.output_key
        )
        if not reset_required:
            main_record = self._make_matrix_main_record(
                context,
                output,
                phase,
                previous_key=previous_key,
                next_key=output.output_key,
            )
            return self._submit_matrix_sequence(
                previous_key=previous_key,
                next_key=output.output_key,
                steps=(MatrixSendStep(main_record, main_packet, role="main"),),
                has_reset=False,
            )

        reset_entry = reset_config.reset_map.get(previous_key)
        reset_channels = tuple(reset_entry.channel_list) if reset_entry is not None else ()
        if not reset_channels:
            reset_record = self._make_matrix_reset_record(
                context,
                previous_key=previous_key,
                next_key=output.output_key,
                channels=(),
            )
            main_record = self._make_matrix_main_record(
                context,
                output,
                phase,
                previous_key=previous_key,
                next_key=output.output_key,
            )
            if reset_config.missing_reset_policy == "skip_reset":
                self._finish_not_sent(
                    reset_record,
                    "skipped",
                    "missing_matrix_reset_mapping",
                )
                return self._submit_matrix_sequence(
                    previous_key=previous_key,
                    next_key=output.output_key,
                    steps=(MatrixSendStep(main_record, main_packet, role="main"),),
                    has_reset=False,
                )
            self._finish_not_sent(
                reset_record,
                "error",
                "missing_matrix_reset_mapping",
            )
            self._finish_not_sent(
                main_record,
                "skipped",
                "missing_matrix_reset_mapping",
            )
            return False

        reset_record = self._make_matrix_reset_record(
            context,
            previous_key=previous_key,
            next_key=output.output_key,
            channels=reset_channels,
        )
        main_record = self._make_matrix_main_record(
            context,
            output,
            phase,
            previous_key=previous_key,
            next_key=output.output_key,
        )
        try:
            reset_packet = encode_matrix_channel_packet(list(reset_channels))
        except Exception as exc:
            reset_record.error = str(exc)
            self._finish_not_sent(reset_record, "error", "invalid_matrix_reset_channels")
            self._finish_not_sent(main_record, "skipped", "reset_failed")
            return False
        return self._submit_matrix_sequence(
            previous_key=previous_key,
            next_key=output.output_key,
            steps=(
                MatrixSendStep(reset_record, reset_packet, role="reset"),
                MatrixSendStep(main_record, main_packet, role="main"),
            ),
            has_reset=True,
        )

    def _submit_matrix_sequence(
        self,
        *,
        previous_key: str | None,
        next_key: str,
        steps: tuple[MatrixSendStep, ...],
        has_reset: bool,
    ) -> bool:
        if self._matrix_worker is None:
            if has_reset:
                self._finish_not_sent(steps[0].record, "not_connected", "matrix_not_connected")
                self._finish_not_sent(steps[-1].record, "skipped", "reset_failed")
            else:
                self._finish_not_sent(steps[-1].record, "not_connected", "matrix_not_connected")
            return False

        with self._matrix_output_key_lock:
            self._previous_matrix_output_key = next_key
            submit_sequence = getattr(self._matrix_worker, "submit_sequence", None)
            if callable(submit_sequence):
                accepted = submit_sequence(
                    steps,
                    on_reset_failure=(
                        (
                            lambda: self._rollback_matrix_output_key(
                                expected_key=next_key,
                                previous_key=previous_key,
                            )
                        )
                        if has_reset
                        else None
                    ),
                )
            elif not has_reset and len(steps) == 1:
                accepted = self._matrix_worker.submit(steps[0].record, steps[0].packet)
            else:
                self._finish_not_sent(
                    steps[0].record,
                    "error",
                    "matrix_worker_sequence_unsupported",
                )
                self._finish_not_sent(steps[-1].record, "skipped", "reset_failed")
                accepted = False
            if not accepted:
                self._previous_matrix_output_key = previous_key
            return bool(accepted)

    def _queue_matrix_reset_to_none(self, context: _FrameContext) -> None:
        previous_key = self._get_previous_matrix_output_key()
        if previous_key is None:
            return
        reset_config = self.haptic_config.matrix.reset_before_output_change
        reset_entry = reset_config.reset_map.get(previous_key)
        channels = tuple(reset_entry.channel_list) if reset_entry is not None else ()
        record = self._make_matrix_reset_record(
            context,
            previous_key=previous_key,
            next_key=None,
            channels=channels,
        )
        if not channels:
            status = "skipped" if reset_config.missing_reset_policy == "skip_reset" else "error"
            self._finish_not_sent(record, status, "missing_matrix_reset_mapping")
            return
        try:
            packet = encode_matrix_channel_packet(list(channels))
        except Exception as exc:
            record.error = str(exc)
            self._finish_not_sent(record, "error", "invalid_matrix_reset_channels")
            return
        if self._matrix_worker is None:
            self._finish_not_sent(record, "not_connected", "matrix_not_connected")
            return
        self._matrix_worker.submit_sequence(
            (MatrixSendStep(record, packet, role="reset"),),
        )

    def _record_matrix_end(
        self,
        context: _FrameContext,
        output: _MatrixMainOutput,
        reason: str,
    ) -> None:
        if not self.matrix_haptic_enabled:
            return
        record = self._make_matrix_main_record(
            context,
            output,
            "state_end",
            previous_key=self._get_previous_matrix_output_key(),
            next_key=None,
            details={
                "end_reason": reason,
                "hardware_clear_assumed": False,
            },
        )
        self._finish_not_sent(record, "not_sent", "state_end_no_hardware_clear")

    def _make_matrix_main_record(
        self,
        context: _FrameContext,
        output: _MatrixMainOutput,
        phase: str,
        *,
        previous_key: str | None,
        next_key: str | None,
        details: dict[str, Any] | None = None,
    ) -> HapticCommandRecord:
        blocked = output.blocked
        direction = blocked.direction_key if blocked is not None else None
        record_details: dict[str, Any] = {
            "matrix_output_key": output.output_key,
            "previous_matrix_output_key": previous_key,
            "next_matrix_output_key": next_key,
        }
        if blocked is not None:
            record_details.update(
                {
                    "track_state": blocked.track_state,
                    "primary_blocked_surface": blocked.primary_surface,
                    "correction_direction": blocked.primary_correction,
                    "blocked_surface_set": blocked.blocked_surface_set,
                    "correction_direction_set": blocked.correction_direction_set,
                    "matrix_filtered_blocked_surface_set": blocked.filtered_blocked_surface_set,
                    "matrix_filtered_correction_direction_set": blocked.filtered_correction_direction_set,
                    "matrix_direction_used": blocked.direction_key,
                    "matrix_direction_semantics": blocked.semantics,
                    "matrix_ignored_direction_axes": blocked.ignored_direction_axes,
                }
            )
        if details:
            record_details.update(details)
        return self._make_record(
            context,
            target_device="matrix",
            target_transport=self.haptic_config.matrix.transport,
            cue_type=output.cue_type,
            haptic_type=output.haptic_type,
            haptic_phase=phase,
            direction=direction,
            primary_blocked_surface=(blocked.primary_surface if blocked is not None else None),
            correction_direction=(blocked.primary_correction if blocked is not None else None),
            blocked_surface_set=(blocked.blocked_surface_set if blocked is not None else None),
            correction_direction_set=(blocked.correction_direction_set if blocked is not None else None),
            matrix_filtered_blocked_surface_set=(
                blocked.filtered_blocked_surface_set if blocked is not None else None
            ),
            matrix_filtered_correction_direction_set=(
                blocked.filtered_correction_direction_set if blocked is not None else None
            ),
            matrix_direction_used=direction,
            matrix_direction_semantics=(blocked.semantics if blocked is not None else None),
            matrix_ignored_direction_axes=(blocked.ignored_direction_axes if blocked is not None else None),
            matrix_output_key=output.output_key,
            previous_matrix_output_key=previous_key,
            next_matrix_output_key=next_key,
            channel_list=list(output.channels),
            details=record_details,
        )

    def _make_matrix_reset_record(
        self,
        context: _FrameContext,
        *,
        previous_key: str,
        next_key: str | None,
        channels: tuple[int, ...],
    ) -> HapticCommandRecord:
        return self._make_record(
            context,
            target_device="matrix",
            target_transport=self.haptic_config.matrix.transport,
            cue_type="matrix_reset_before_output_change",
            haptic_type="matrix_reset_before_output_change",
            haptic_phase="reset",
            direction=None,
            matrix_output_key=None,
            previous_matrix_output_key=previous_key,
            next_matrix_output_key=next_key,
            channel_list=list(channels),
            details={
                "previous_matrix_output_key": previous_key,
                "next_matrix_output_key": next_key,
                "reset_channel_list": list(channels),
                "hold_ms": 0.0,
                "hardware_reset_semantics_assumed": False,
            },
        )

    def _matrix_main_output_validation(
        self,
        output: _MatrixMainOutput,
    ) -> tuple[str, str | None]:
        if output.missing_reason == "direction_filtered_empty":
            return "skipped", "direction_filtered_empty"
        if output.output_key is None:
            return "skipped", "missing_direction"
        if output.missing_reason == "missing_combination_mapping":
            return "not_sent", "missing_combination_mapping"
        if not output.channels or output.missing_reason == "no_channel_mapping":
            return "skipped", "no_channel_mapping"
        return "", None

    def _get_previous_matrix_output_key(self) -> str | None:
        with self._matrix_output_key_lock:
            return self._previous_matrix_output_key

    def _rollback_matrix_output_key(
        self,
        *,
        expected_key: str,
        previous_key: str | None,
    ) -> None:
        with self._matrix_output_key_lock:
            if self._previous_matrix_output_key == expected_key:
                self._previous_matrix_output_key = previous_key

    def _record_vibration_command(
        self,
        context: _FrameContext,
        *,
        cue_type: str,
        haptic_type: str,
        haptic_phase: str,
        command_label: str,
        enabled: bool,
        not_sent_reason: str | None,
        direction: str | None = None,
        priority_stop: bool = False,
        details: dict[str, Any] | None = None,
    ) -> HapticCommandRecord:
        vibration = self.haptic_config.vibration
        command = vibration.command_map.get(command_label)
        record = self._make_record(
            context,
            target_device="vibration",
            target_transport=vibration.transport,
            cue_type=cue_type,
            haptic_type=haptic_type,
            haptic_phase=haptic_phase,
            direction=direction,
            vibration_command=command,
            vibration_command_label=command_label,
            details=details or {},
        )
        if not enabled:
            self._finish_not_sent(record, "skipped", not_sent_reason or "disabled")
            return record
        if command is None:
            self._finish_not_sent(record, "skipped", "missing_vibration_command")
            return record
        try:
            payload = encode_vibration_line_command(command)
        except Exception as exc:
            record.error = str(exc)
            self._finish_not_sent(record, "skipped", "invalid_vibration_command")
            return record
        record.sent_payload = vibration_payload_to_log_string(payload)
        record.payload_hex = payload.hex()
        if self._vibration_worker is None:
            reason = "stop_slip_send_failed" if priority_stop else "vibration_not_connected"
            self._finish_not_sent(record, "not_connected", reason)
            return record
        self._vibration_worker.submit(record, payload, priority_stop=priority_stop)
        return record

    def _make_record(
        self,
        context: _FrameContext,
        *,
        target_device: str,
        target_transport: str | None,
        cue_type: str,
        haptic_type: str,
        haptic_phase: str,
        direction: str | None,
        primary_blocked_surface: str | None = None,
        correction_direction: str | None = None,
        blocked_surface_set: str | None = None,
        correction_direction_set: str | None = None,
        matrix_filtered_blocked_surface_set: str | None = None,
        matrix_filtered_correction_direction_set: str | None = None,
        matrix_direction_used: str | None = None,
        matrix_direction_semantics: str | None = None,
        matrix_ignored_direction_axes: str | None = None,
        matrix_output_key: str | None = None,
        previous_matrix_output_key: str | None = None,
        next_matrix_output_key: str | None = None,
        channel_list: list[int] | None = None,
        vibration_command: int | None = None,
        vibration_command_label: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> HapticCommandRecord:
        sequence = self._sequence
        self._sequence += 1
        record = HapticCommandRecord(
            haptic_sequence_index=sequence,
            haptic_id=f"{_safe_id(self.trial_id)}:haptic:{sequence:06d}",
            cue_id=None,
            cue_sequence_index=None,
            trial_id=self.trial_id,
            target_device=target_device,
            target_transport=target_transport,
            source_frame_id=context.source_frame_id,
            source_frame_index=context.frame_index,
            source_trial_time=context.source_trial_time,
            cue_type=cue_type,
            haptic_type=haptic_type,
            haptic_phase=haptic_phase,
            direction=direction,
            primary_blocked_surface=primary_blocked_surface,
            correction_direction=correction_direction,
            blocked_surface_set=blocked_surface_set,
            correction_direction_set=correction_direction_set,
            matrix_filtered_blocked_surface_set=matrix_filtered_blocked_surface_set,
            matrix_filtered_correction_direction_set=matrix_filtered_correction_direction_set,
            matrix_direction_used=matrix_direction_used,
            matrix_direction_semantics=matrix_direction_semantics,
            matrix_ignored_direction_axes=matrix_ignored_direction_axes,
            matrix_output_key=matrix_output_key,
            previous_matrix_output_key=previous_matrix_output_key,
            next_matrix_output_key=next_matrix_output_key,
            channel_list=list(channel_list or []),
            vibration_command=vibration_command,
            vibration_command_label=vibration_command_label,
            created_monotonic_ms=self.monotonic_ms_fn(),
            mode=self.mode,
            is_live_haptic_timing=self.is_live_haptic_timing,
            details={"source_sample_time": context.source_sample_time, **(details or {})},
        )
        self._records.append(record)
        return record

    def _finish_not_sent(
        self,
        record: HapticCommandRecord,
        status: str,
        reason: str,
    ) -> None:
        record.send_status = status
        record.not_sent_reason = reason
        record.success = None if status == "not_sent" else False


def disabled_haptic_summary() -> dict[str, Any]:
    """Return summary fields for flows without a HapticRuntime."""

    config = default_haptic_config()
    return {
        "haptic_enabled": False,
        "matrix_haptic_enabled": False,
        "vibration_haptic_enabled": False,
        "haptic_mode": None,
        "is_live_haptic_timing": None,
        "haptic_count": 0,
        "haptic_type_counts": {},
        "need_pinch_requires_valid_grab_count": 0,
        "effective_haptic_config": config.to_dict(),
        "haptic_command_log_path": None,
        "haptic_warnings": [],
        "haptic_connect_error": None,
        "matrix_haptic_transport": config.matrix.transport,
        "vibration_haptic_transport": config.vibration.transport,
        "matrix_haptic_startup_connected": False,
        "vibration_haptic_startup_connected": False,
        "matrix_haptic_connect_error": None,
        "vibration_haptic_connect_error": None,
        "matrix_haptic_connected": False,
        "vibration_haptic_connected": False,
    }


def _record_was_queued_or_sent(record: HapticCommandRecord) -> bool:
    return record.send_status in {"queued", "sent"}


def _target_box_from_trial_config(trial_config: dict[str, Any]) -> tuple[Vec3, Vec3] | None:
    payload = trial_config.get("target_region")
    if not isinstance(payload, dict):
        return None
    minimum = payload.get("min")
    maximum = payload.get("max")
    if minimum is None or maximum is None:
        return None
    try:
        return _to_vec3(minimum), _to_vec3(maximum)
    except Exception:
        return None


def _point_in_box(point: Vec3, box: tuple[Vec3, Vec3]) -> bool:
    minimum, maximum = box
    return (
        minimum.x <= point.x <= maximum.x
        and minimum.y <= point.y <= maximum.y
        and minimum.z <= point.z <= maximum.z
    )


def _to_vec3(value: Any) -> Vec3:
    if isinstance(value, Vec3):
        return value
    if all(hasattr(value, attr) for attr in ("x", "y", "z")):
        return Vec3(float(value.x), float(value.y), float(value.z))
    items = list(value)
    if len(items) != 3:
        raise ValueError("Expected a 3D vector.")
    return Vec3(float(items[0]), float(items[1]), float(items[2]))


def _surface_from_track_state(track_state: str) -> str | None:
    if track_state.startswith("BLOCKED_"):
        return track_state.removeprefix("BLOCKED_")
    return None


def _blocked_surface_names(trial_result: Any) -> tuple[str, ...]:
    output = getattr(trial_result, "frame_output", None)
    feedback = getattr(output, "feedback_state", None)
    blocked_info = getattr(feedback, "blocked_info", None)
    names = [
        _name(surface)
        for surface in getattr(blocked_info, "all_blocked_surfaces", ()) or ()
        if _name(surface)
    ]
    if not names:
        haptic = getattr(trial_result, "haptic_feedback_state", None)
        primary = _name(getattr(haptic, "primary_blocked_surface", None))
        if primary:
            names = [primary]
    if not names:
        track_surface = _surface_from_track_state(_name(getattr(feedback, "track_state", None)))
        if track_surface:
            names = [track_surface]
    return _canonical_direction_names(names)


def _canonical_direction_names(values: list[str]) -> tuple[str, ...]:
    if not values:
        return ()
    key = normalize_direction_key("+".join(values), name="blocked direction set")
    return tuple(key.split("+"))


def _filter_directions_by_axes(
    values: tuple[str, ...],
    ignored_axes: tuple[str, ...],
) -> tuple[str, ...]:
    if not values or not ignored_axes:
        return values
    ignored = set(ignored_axes)
    return tuple(value for value in values if value.split("_", 1)[0] not in ignored)


def _axis_key(values: tuple[str, ...]) -> str | None:
    if not values:
        return None
    return "+".join(values)


def _direction_key(values: tuple[str, ...]) -> str | None:
    if not values:
        return None
    return normalize_direction_key("+".join(values), name="blocked direction set")


def _opposite_direction(value: str | None) -> str | None:
    mapping = {
        "X_POS": "X_NEG",
        "X_NEG": "X_POS",
        "Y_POS": "Y_NEG",
        "Y_NEG": "Y_POS",
        "Z_POS": "Z_NEG",
        "Z_NEG": "Z_POS",
    }
    return mapping.get(str(value)) if value else None


def _slip_cue_type(reason: str) -> str:
    if reason == "PINCH_INSUFFICIENT":
        return "slip_pinch_insufficient"
    if reason == "TRACK_BLOCKED":
        return "slip_track_blocked"
    return "slip"


def _event_type_name(value: Any) -> str:
    return _name(value).lower()


def _name(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if name is not None:
        return str(name)
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_id(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return normalized or "trial"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "name"):
        return str(value.name)
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        return [float(value.x), float(value.y), float(value.z)]
    return value
