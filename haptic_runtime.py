"""Stage 1 haptic command routing and logging."""

from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from data_models import Vec3
from haptic_config import HapticConfig, default_haptic_config
from haptic_tcp_worker import MatrixHapticConnectionError, MatrixTcpWorker
from matrix_haptic_protocol import encode_matrix_channel_packet


HAPTIC_CSV_FIELDS = [
    "haptic_sequence_index",
    "haptic_id",
    "cue_id",
    "cue_sequence_index",
    "trial_id",
    "target_device",
    "source_frame_id",
    "source_frame_index",
    "source_trial_time",
    "cue_type",
    "haptic_type",
    "haptic_phase",
    "direction",
    "primary_blocked_surface",
    "correction_direction",
    "matrix_direction_used",
    "matrix_direction_semantics",
    "channel_list",
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
    source_frame_id: str | int | None
    source_frame_index: int | None
    source_trial_time: float | None
    cue_type: str
    haptic_type: str
    haptic_phase: str
    direction: str | None
    primary_blocked_surface: str | None = None
    correction_direction: str | None = None
    matrix_direction_used: str | None = None
    matrix_direction_semantics: str | None = None
    channel_list: list[int] = field(default_factory=list)
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


class HapticStartupError(RuntimeError):
    """Raised when required haptic hardware cannot start before trial."""


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
        monotonic_ms_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.trial_id = trial_id
        self.haptic_config = haptic_config or default_haptic_config()
        self.trial_config = trial_config or {}
        self.mode = mode
        self.is_live_haptic_timing = bool(is_live_haptic_timing)
        self.worker_factory = worker_factory or MatrixTcpWorker
        self.monotonic_ms_fn = monotonic_ms_fn or (lambda: time.monotonic() * 1000.0)
        self.sleep_fn = sleep_fn or time.sleep
        self.warnings: list[str] = []
        self.connect_error: str | None = None
        self._records: list[HapticCommandRecord] = []
        self._sequence = 0
        self._matrix_worker: MatrixTcpWorker | None = None
        self._started = False
        self._session_ended = False
        self._trial_ended = False
        self._previous_slip_signature: tuple[Any, ...] | None = None
        self._previous_blocked_signature: tuple[Any, ...] | None = None
        self._last_matrix_send_monotonic_ms: float | None = None
        self._last_context: _FrameContext | None = None
        self._target_box = _target_box_from_trial_config(self.trial_config)
        self._warned_missing_target_region = False

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
        if not self.matrix_haptic_enabled:
            return
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
        except Exception as exc:
            self.connect_error = str(exc)
            message = f"matrix haptic connect failed: {exc}"
            if matrix.required:
                raise HapticStartupError(message) from exc
            self.warnings.append(message)
            self._matrix_worker = None
            return
        if matrix.startup_settle_seconds > 0.0:
            self.sleep_fn(matrix.startup_settle_seconds)

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
        self._process_contact_events(context, trial_result)
        self._process_slip_state(context, trial_result)
        self._process_blocked_state(context, trial_result)
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
            "effective_haptic_config": self.haptic_config.to_dict(),
            "haptic_command_log_path": (
                str(haptic_log_path)
                if self.haptic_enabled and haptic_log_path is not None
                else None
            ),
            "haptic_warnings": list(self.warnings),
            "haptic_connect_error": self.connect_error,
            "matrix_haptic_connected": bool(
                self._matrix_worker is not None and self._matrix_worker.connected
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

    def _process_contact_events(self, context: _FrameContext, trial_result: Any) -> None:
        if not self.vibration_haptic_enabled:
            return
        for event in getattr(trial_result, "events", ()) or ():
            event_type = str(getattr(event, "event_type", "")).lower()
            if event_type == "contact_enter":
                enabled = self.haptic_config.vibration.enable_contact
                self._record_vibration_command(
                    context,
                    cue_type="contact_enter",
                    haptic_type="vibration_contact_enter",
                    haptic_phase="one_shot",
                    enabled=enabled,
                    not_sent_reason=None if enabled else "contact_disabled",
                    details={"source_event_type": "contact_enter"},
                )
            elif event_type == "contact_exit":
                enabled = self.haptic_config.vibration.enable_release
                details = getattr(event, "details", {}) or {}
                self._record_vibration_command(
                    context,
                    cue_type="contact_exit",
                    haptic_type="vibration_contact_exit",
                    haptic_phase="one_shot",
                    enabled=enabled,
                    not_sent_reason=None if enabled else "release_disabled",
                    details={
                        "source_event_type": "contact_exit",
                        "detach_state": details.get("detach_state"),
                    },
                )

    def _process_slip_state(self, context: _FrameContext, trial_result: Any) -> None:
        signature = self._slip_signature(trial_result)
        if signature is None:
            if self._previous_slip_signature is not None:
                self._record_slip_end(context, self._previous_slip_signature, "slip_inactive")
            self._previous_slip_signature = None
            return
        phase = None
        if self._previous_slip_signature is None:
            phase = "state_start"
        elif signature != self._previous_slip_signature:
            phase = "state_update"
        if phase is not None:
            self._record_slip_command(context, signature, phase)
        self._previous_slip_signature = signature

    def _process_blocked_state(self, context: _FrameContext, trial_result: Any) -> None:
        signature = self._blocked_signature(trial_result)
        if signature is None:
            if self._previous_blocked_signature is not None:
                self._record_matrix_end(context, self._previous_blocked_signature, "blocked_inactive_no_clear")
            self._previous_blocked_signature = None
            self._last_matrix_send_monotonic_ms = None
            return

        now_ms = self.monotonic_ms_fn()
        mode = self.haptic_config.matrix.feedback_mode
        phase: str | None = None
        if self._previous_blocked_signature is None:
            phase = "state_start"
        elif signature != self._previous_blocked_signature:
            phase = "state_update"
        elif mode == "continuous_resend":
            last = self._last_matrix_send_monotonic_ms
            interval = self.haptic_config.matrix.resend_interval_ms
            if last is None or now_ms - last >= interval:
                phase = "state_update"

        if phase is not None:
            self._record_matrix_command(context, signature, phase)
            self._last_matrix_send_monotonic_ms = now_ms
        self._previous_blocked_signature = signature

    def _end_active_states(self, *, reason: str) -> None:
        context = self._last_context or _FrameContext(None, None, None, None)
        if self._previous_slip_signature is not None:
            self._record_slip_end(context, self._previous_slip_signature, reason)
            self._previous_slip_signature = None
        if self._previous_blocked_signature is not None:
            self._record_matrix_end(context, self._previous_blocked_signature, reason)
            self._previous_blocked_signature = None
            self._last_matrix_send_monotonic_ms = None

    def _slip_signature(self, trial_result: Any) -> tuple[Any, ...] | None:
        haptic = getattr(trial_result, "haptic_feedback_state", None)
        if not bool(getattr(haptic, "slip_active", False)):
            return None
        reason = _name(getattr(haptic, "slip_reason", None))
        in_target = self._block_center_in_target(trial_result)
        return (reason, in_target)

    def _blocked_signature(self, trial_result: Any) -> tuple[Any, ...] | None:
        haptic = getattr(trial_result, "haptic_feedback_state", None)
        if not bool(getattr(haptic, "blocked_force_active", False)):
            return None
        output = getattr(trial_result, "frame_output", None)
        feedback = getattr(output, "feedback_state", None)
        primary_surface = _name(getattr(haptic, "primary_blocked_surface", None))
        if not primary_surface:
            primary_surface = _surface_from_track_state(_name(getattr(feedback, "track_state", None)))
        correction = _opposite_direction(primary_surface)
        semantics = self.haptic_config.matrix.direction_semantics
        direction_used = primary_surface if semantics == "blocked_surface" else correction
        track_state = _name(getattr(feedback, "track_state", None))
        channels = (
            self.haptic_config.matrix.direction_channel_map.get(direction_used or "", [])
            if direction_used
            else []
        )
        return (
            direction_used,
            primary_surface,
            correction,
            semantics,
            track_state,
            tuple(channels),
        )

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
    ) -> None:
        if not self.vibration_haptic_enabled:
            return
        reason, in_target = signature
        enabled, disabled_reason = self._slip_enabled(str(reason or ""), in_target)
        self._record_vibration_command(
            context,
            cue_type=_slip_cue_type(str(reason or "")),
            haptic_type="vibration_slip",
            haptic_phase=phase,
            enabled=enabled,
            not_sent_reason=disabled_reason,
            direction=None,
            details={
                "slip_reason": reason,
                "block_center_in_target_region": in_target,
            },
        )

    def _record_slip_end(
        self,
        context: _FrameContext,
        signature: tuple[Any, ...],
        reason: str,
    ) -> None:
        if not self.vibration_haptic_enabled:
            return
        slip_reason, in_target = signature
        enabled, disabled_reason = self._slip_enabled(str(slip_reason or ""), in_target)
        self._record_vibration_command(
            context,
            cue_type=_slip_cue_type(str(slip_reason or "")),
            haptic_type="vibration_slip",
            haptic_phase="state_end",
            enabled=enabled,
            not_sent_reason=disabled_reason,
            direction=None,
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

    def _record_matrix_command(
        self,
        context: _FrameContext,
        signature: tuple[Any, ...],
        phase: str,
    ) -> None:
        if not self.matrix_haptic_enabled:
            return
        direction, primary, correction, semantics, track_state, channels_tuple = signature
        channels = list(channels_tuple)
        details = {
            "track_state": track_state,
            "primary_blocked_surface": primary,
            "correction_direction": correction,
            "matrix_direction_used": direction,
            "matrix_direction_semantics": semantics,
        }
        record = self._make_record(
            context,
            target_device="matrix",
            cue_type="blocked_directional",
            haptic_type="matrix_blocked_direction",
            haptic_phase=phase,
            direction=str(direction) if direction else None,
            primary_blocked_surface=str(primary) if primary else None,
            correction_direction=str(correction) if correction else None,
            matrix_direction_used=str(direction) if direction else None,
            matrix_direction_semantics=str(semantics) if semantics else None,
            channel_list=channels,
            details=details,
        )
        if not direction:
            self._finish_not_sent(record, "skipped", "missing_direction")
            return
        if not channels:
            self._finish_not_sent(record, "skipped", "no_channel_mapping")
            return
        try:
            packet = encode_matrix_channel_packet(channels)
        except Exception as exc:
            record.error = str(exc)
            self._finish_not_sent(record, "skipped", "invalid_channel_list")
            return
        if self._matrix_worker is None:
            self._finish_not_sent(record, "not_connected", "matrix_not_connected")
            return
        self._matrix_worker.submit(record, packet)

    def _record_matrix_end(
        self,
        context: _FrameContext,
        signature: tuple[Any, ...],
        reason: str,
    ) -> None:
        if not self.matrix_haptic_enabled:
            return
        direction, primary, correction, semantics, track_state, channels_tuple = signature
        record = self._make_record(
            context,
            target_device="matrix",
            cue_type="blocked_directional",
            haptic_type="matrix_blocked_direction",
            haptic_phase="state_end",
            direction=str(direction) if direction else None,
            primary_blocked_surface=str(primary) if primary else None,
            correction_direction=str(correction) if correction else None,
            matrix_direction_used=str(direction) if direction else None,
            matrix_direction_semantics=str(semantics) if semantics else None,
            channel_list=list(channels_tuple),
            details={
                "track_state": track_state,
                "end_reason": reason,
                "hardware_clear_assumed": False,
            },
        )
        self._finish_not_sent(record, "not_sent", "state_end_no_hardware_clear")

    def _record_vibration_command(
        self,
        context: _FrameContext,
        *,
        cue_type: str,
        haptic_type: str,
        haptic_phase: str,
        enabled: bool,
        not_sent_reason: str | None,
        direction: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> HapticCommandRecord:
        record = self._make_record(
            context,
            target_device="vibration",
            cue_type=cue_type,
            haptic_type=haptic_type,
            haptic_phase=haptic_phase,
            direction=direction,
            details=details or {},
        )
        if not enabled:
            self._finish_not_sent(record, "skipped", not_sent_reason or "disabled")
        else:
            self._finish_not_sent(record, "protocol_pending", "not_implemented")
        return record

    def _make_record(
        self,
        context: _FrameContext,
        *,
        target_device: str,
        cue_type: str,
        haptic_type: str,
        haptic_phase: str,
        direction: str | None,
        primary_blocked_surface: str | None = None,
        correction_direction: str | None = None,
        matrix_direction_used: str | None = None,
        matrix_direction_semantics: str | None = None,
        channel_list: list[int] | None = None,
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
            source_frame_id=context.source_frame_id,
            source_frame_index=context.frame_index,
            source_trial_time=context.source_trial_time,
            cue_type=cue_type,
            haptic_type=haptic_type,
            haptic_phase=haptic_phase,
            direction=direction,
            primary_blocked_surface=primary_blocked_surface,
            correction_direction=correction_direction,
            matrix_direction_used=matrix_direction_used,
            matrix_direction_semantics=matrix_direction_semantics,
            channel_list=list(channel_list or []),
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
        record.success = None if status in {"not_sent", "protocol_pending"} else False


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
        "effective_haptic_config": config.to_dict(),
        "haptic_command_log_path": None,
        "haptic_warnings": [],
        "haptic_connect_error": None,
        "matrix_haptic_connected": False,
    }


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
