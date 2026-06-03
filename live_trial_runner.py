"""Reusable live trial control loop for Stage 5C.

LiveTrialRunner owns only the realtime trial loop:
latest raw frame -> parser -> adapter -> TrialController -> session recorder
-> display snapshot. It does not collect calibration, validate maps, draw GUI,
or send haptic hardware commands.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Callable

from block_controller import BlockController
from config import EngineConfig
from dashboard_snapshot import DashboardSnapshot, build_dashboard_snapshot
from data_models import Vec3
from device_frame_models import DeviceAdapterConfig
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from trial_controller import EventRecord, TrialController, TrialState


SnapshotCallback = Callable[[DashboardSnapshot], None]
StatsCallback = Callable[["LiveTrialRunnerStats"], None]
SourceStopReasonGetter = Callable[[], str | None]
SourceStatsGetter = Callable[[], dict[str, Any]]
UserQuitChecker = Callable[[], bool]
OperatorCommandChecker = Callable[[], str | None]


@dataclass(frozen=True)
class LiveTrialRunnerConfig:
    """Configuration for one realtime trial loop."""

    trial_id: str | int = "live_trial"
    control_rate_hz: float = 60.0
    duration_seconds: float | None = None
    max_frames: int | None = None
    no_frame_timeout_seconds: float = 5.0
    print_every: int = 30
    timestamp_scale: float = 0.001
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    pinch_position_mode: str = "tracker_plus_local"
    manual_completion_enabled: bool = True
    timeout_enabled: bool = True
    detach_limit_enabled: bool = True
    haptic_hardware_enabled: bool = False


@dataclass(frozen=True)
class LiveTrialRunnerStats:
    """JSON-safe live trial counters."""

    total_received_frames: int
    total_processed_frames: int
    parse_error_count: int
    adapter_error_count: int
    tracker_invalid_frame_count: int
    hand_invalid_frame_count: int
    pinch_valid_frame_count: int
    large_delta_frame_count: int
    slip_active_frame_count: int
    blocked_frame_count: int
    logical_haptic_label_counts: dict[str, int]
    no_new_frame_count: int
    max_no_new_frame_gap_seconds: float
    mean_processing_latency_ms: float | None
    max_processing_latency_ms: float | None
    callback_error_count: int
    mean_callback_latency_ms: float | None
    max_callback_latency_ms: float | None
    run_stop_reason: str


@dataclass(frozen=True)
class LiveTrialRunnerResult:
    """Result returned by run_until_done()."""

    stats: LiveTrialRunnerStats
    summary: dict[str, Any]
    last_snapshot: DashboardSnapshot | None
    events_count: int
    session_finalized: bool = False


class LiveTrialRunner:
    """Run TrialController at a fixed rate from a LatestFrameBuffer."""

    def __init__(
        self,
        *,
        latest_frame_buffer: Any,
        task_coordinate_system: Any,
        track_region: Any,
        block_initial_center_task: Any,
        block_size: Any,
        engine_config: EngineConfig,
        session_recorder: Any,
        config: LiveTrialRunnerConfig,
        map_config_payload: dict[str, Any] | None = None,
        trial_config: dict[str, Any] | None = None,
        map_id: str = "",
        calibration_id: str = "",
        snapshot_callback: SnapshotCallback | None = None,
        display_callback: SnapshotCallback | None = None,
        stats_callback: StatsCallback | None = None,
        source_stop_reason_getter: SourceStopReasonGetter | None = None,
        source_stats_getter: SourceStatsGetter | None = None,
        user_quit_checker: UserQuitChecker | None = None,
        operator_command_checker: OperatorCommandChecker | None = None,
    ) -> None:
        if config.control_rate_hz <= 0.0:
            raise ValueError("control_rate_hz must be > 0.")
        if config.no_frame_timeout_seconds <= 0.0:
            raise ValueError("no_frame_timeout_seconds must be > 0.")
        if config.max_frames is not None and config.max_frames <= 0:
            raise ValueError("max_frames must be > 0.")
        if config.duration_seconds is not None and config.duration_seconds <= 0.0:
            raise ValueError("duration_seconds must be > 0.")

        self.latest_frame_buffer = latest_frame_buffer
        self.task_coordinate_system = task_coordinate_system
        self.track_region = track_region
        self.block_initial_center_task = _to_vec3(block_initial_center_task)
        self.block_size = _to_vec3(block_size)
        self.engine_config = engine_config
        self.session_recorder = session_recorder
        self.config = config
        self.map_config_payload = map_config_payload or {}
        self.trial_config = trial_config or {}
        self.map_id = str(map_id)
        self.calibration_id = str(calibration_id)
        self.snapshot_callback = snapshot_callback
        self.display_callback = display_callback
        self.stats_callback = stats_callback
        self.source_stop_reason_getter = source_stop_reason_getter
        self.source_stats_getter = source_stats_getter
        self.user_quit_checker = user_quit_checker
        self.operator_command_checker = operator_command_checker

        self.adapter_config = DeviceAdapterConfig(
            skeleton_index=config.skeleton_index,
            tracker_index=config.tracker_index,
            thumb_tip_node_id=config.thumb_node,
            index_tip_node_id=config.index_node,
            pinch_position_mode=config.pinch_position_mode,
            timestamp_scale=config.timestamp_scale,
        )
        self.adapter = ManusViveExperimentAdapter(None, config=self.adapter_config)
        self.trial = TrialController(self._block_controller_factory, task_coordinate_system, engine_config)

        self._stop_requested = False
        self._run_stop_reason = "running"
        self._last_new_frame_time = time.monotonic()
        self._run_started_time: float | None = None
        self._run_end_time: float | None = None
        self._trial_start_sample_time: float | None = None
        self._trial_start_monotonic: float | None = None
        self._trial_end_monotonic: float | None = None
        self._last_snapshot: DashboardSnapshot | None = None
        self._last_trial_result: Any | None = None
        self._last_sample_time: float | None = None
        self._trial_started = False
        self._events_count = 0
        self._operator_command: str | None = None
        self._operator_command_time: float | None = None
        self._trial_outcome: str | None = None
        self._end_reason: str | None = None
        self._first_target_entry_time: float | None = None
        self._first_target_entry_frame_index: int | None = None
        self._target_box = _target_box_from_trial_config(self.trial_config)

        self._total_processed_frames = 0
        self._parse_error_count = 0
        self._adapter_error_count = 0
        self._tracker_invalid_frame_count = 0
        self._hand_invalid_frame_count = 0
        self._pinch_valid_frame_count = 0
        self._large_delta_frame_count = 0
        self._slip_active_frame_count = 0
        self._blocked_frame_count = 0
        self._logical_haptic_label_counts: Counter[str] = Counter()
        self._no_new_frame_count = 0
        self._max_no_new_frame_gap_seconds = 0.0
        self._processing_latency_ms: list[float] = []
        self._callback_latency_ms: list[float] = []
        self._callback_error_count = 0
        self.warnings: list[str] = []
        if self._target_box is None:
            self.warnings.append("target_region missing; target diagnostics are limited.")

    @property
    def trial_started(self) -> bool:
        """Return whether TrialController.start_trial() has been called."""

        return self._trial_started

    @property
    def events_count(self) -> int:
        """Return count of events written through this runner."""

        return self._events_count

    @property
    def last_snapshot(self) -> DashboardSnapshot | None:
        """Return the latest display snapshot."""

        return self._last_snapshot

    def start_trial(self, time_value: float) -> None:
        """Start TrialController exactly once."""

        if self._trial_started:
            return
        self.trial.start_trial(time_value, self.config.trial_id)
        self._trial_started = True
        self._trial_start_sample_time = float(time_value)
        self._trial_start_monotonic = time.monotonic()

    def request_stop(
        self,
        reason: str = "stop_requested",
        *,
        trial_outcome: str | None = None,
        end_reason: str | None = None,
        operator_command: str | None = None,
    ) -> None:
        """Request the loop to stop at the next safe point."""

        self._stop_requested = True
        self._run_stop_reason = reason
        self._run_end_time = time.monotonic()
        self._trial_end_monotonic = self._run_end_time
        if operator_command is not None:
            self._operator_command = operator_command
            self._operator_command_time = self._last_sample_time
        self._set_outcome_for_stop(reason, trial_outcome=trial_outcome, end_reason=end_reason)

    def step_once(self) -> DashboardSnapshot | None:
        """Process the latest available frame once.

        Returns a DashboardSnapshot when a frame reaches TrialController,
        otherwise None. Parse/adapter errors are counted and swallowed.
        KeyboardInterrupt is intentionally not swallowed so the caller can
        perform the same safe shutdown path as any other user interrupt.
        """

        if self._stop_requested:
            return None

        now = time.monotonic()
        live_frame = self.latest_frame_buffer.get_latest()
        if live_frame is None:
            self._handle_no_new_frame(now)
            return None

        self._last_new_frame_time = now
        start_process = time.monotonic()
        processed = self._parse_and_adapt(live_frame)
        device_frame = processed.get("device_frame")
        if device_frame is not None and self.session_recorder is not None:
            self.session_recorder.record_device_frame(live_frame.frame_index, device_frame)
        if not processed["parse_ok"] or not processed["adapter_ok"] or processed["sample"] is None:
            return None

        sample = processed["sample"]
        self._last_sample_time = float(sample.time)
        if not self._trial_started:
            self.start_trial(sample.time)
            self._record_events(live_frame.frame_index, sample.time, self.trial.event_history[-2:])

        result = self.trial.update(sample)
        processing_latency = (time.monotonic() - start_process) * 1000.0
        self._processing_latency_ms.append(processing_latency)
        self._total_processed_frames += 1

        snapshot = build_dashboard_snapshot(
            frame_index=live_frame.frame_index,
            trial_result=result,
            sample=sample,
            hand_valid=bool(processed["hand_valid"]),
            map_id=self.map_id,
            calibration_id=self.calibration_id,
            processing_latency_ms=processing_latency,
            hardware_haptic_active=self.config.haptic_hardware_enabled,
        )
        self._last_snapshot = snapshot
        self._last_trial_result = result
        self._update_target_entry(snapshot)
        self._update_snapshot_stats(snapshot)
        self._record_frame(live_frame, processed, sample, result, snapshot)
        self._emit_callbacks(snapshot)

        if result.trial_state in {
            TrialState.ENDED_BY_SUBJECT,
            TrialState.FAILED_TIMEOUT,
            TrialState.FAILED_TOO_MANY_DETACHES,
        }:
            self.request_stop(
                result.trial_state.name.lower(),
                trial_outcome=_trial_state_outcome(result.trial_state),
                end_reason=_trial_state_end_reason(result.trial_state),
            )
        return snapshot

    def run_until_done(self) -> LiveTrialRunnerResult:
        """Run the realtime loop until a configured stop condition is reached."""

        self._run_started_time = time.monotonic()
        next_tick = time.monotonic()
        while not self._stop_requested:
            if self.config.duration_seconds is not None:
                if time.monotonic() - self._run_started_time >= self.config.duration_seconds:
                    self.request_stop(
                        "duration_reached",
                        trial_outcome="DURATION_REACHED",
                        end_reason="duration_reached",
                    )
                    break
            if self.config.max_frames is not None and self._total_processed_frames >= self.config.max_frames:
                self.request_stop(
                    "max_frames",
                    trial_outcome="MAX_FRAMES_REACHED",
                    end_reason="max_frames",
                )
                break
            command = self._read_operator_command()
            if command == "e":
                if self.config.manual_completion_enabled:
                    self._record_operator_event("operator_manual_complete", command)
                    self.request_stop(
                        "operator_manual_complete",
                        trial_outcome="MANUAL_COMPLETED",
                        end_reason="operator_manual_complete",
                        operator_command=command,
                    )
                    break
                self.warnings.append("operator manual completion command ignored; manual completion is disabled.")
            elif command == "q":
                self._record_operator_event("operator_abort", command)
                self.request_stop(
                    "user_quit",
                    trial_outcome="ABORTED_BY_OPERATOR",
                    end_reason="operator_abort",
                    operator_command=command,
                )
                break
            if self.user_quit_checker is not None and self.user_quit_checker():
                self.request_stop(
                    "user_quit",
                    trial_outcome="ABORTED_BY_OPERATOR",
                    end_reason="operator_abort",
                    operator_command="q",
                )
                break

            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.002, next_tick - now))
                continue
            next_tick = now + (1.0 / self.config.control_rate_hz)
            self.step_once()

        if self._run_stop_reason == "running":
            self._run_stop_reason = "completed"
        self._set_outcome_for_stop(self._run_stop_reason)
        summary = self.build_summary()
        return LiveTrialRunnerResult(
            stats=self.stats_snapshot(),
            summary=summary,
            last_snapshot=self._last_snapshot,
            events_count=self._events_count,
            session_finalized=False,
        )

    def build_summary(self) -> dict[str, Any]:
        """Return a JSON-safe summary for this trial loop."""

        stats = self.stats_snapshot()
        payload = asdict(stats)
        payload.update(
            {
                "trial_id": self.config.trial_id,
                "trial_controller_started": self._trial_started,
                "events_count": self._events_count,
                "callback_error_count": self._callback_error_count,
                "haptic_hardware_enabled": self.config.haptic_hardware_enabled,
                "pinch_position_mode": self.config.pinch_position_mode,
                **self._outcome_summary(),
                **self._target_diagnostics_summary(),
                "warnings": list(self.warnings),
            }
        )
        return payload

    def stats_snapshot(self) -> LiveTrialRunnerStats:
        """Return immutable stats snapshot."""

        total_received = self._total_received_frames()
        return LiveTrialRunnerStats(
            total_received_frames=total_received,
            total_processed_frames=self._total_processed_frames,
            parse_error_count=self._parse_error_count,
            adapter_error_count=self._adapter_error_count,
            tracker_invalid_frame_count=self._tracker_invalid_frame_count,
            hand_invalid_frame_count=self._hand_invalid_frame_count,
            pinch_valid_frame_count=self._pinch_valid_frame_count,
            large_delta_frame_count=self._large_delta_frame_count,
            slip_active_frame_count=self._slip_active_frame_count,
            blocked_frame_count=self._blocked_frame_count,
            logical_haptic_label_counts=dict(self._logical_haptic_label_counts),
            no_new_frame_count=self._no_new_frame_count,
            max_no_new_frame_gap_seconds=self._max_no_new_frame_gap_seconds,
            mean_processing_latency_ms=mean(self._processing_latency_ms) if self._processing_latency_ms else None,
            max_processing_latency_ms=max(self._processing_latency_ms) if self._processing_latency_ms else None,
            callback_error_count=self._callback_error_count,
            mean_callback_latency_ms=mean(self._callback_latency_ms) if self._callback_latency_ms else None,
            max_callback_latency_ms=max(self._callback_latency_ms) if self._callback_latency_ms else None,
            run_stop_reason=self._run_stop_reason,
        )

    def _block_controller_factory(self) -> BlockController:
        return BlockController(self.engine_config, self.track_region, self.block_initial_center_task)

    def _parse_and_adapt(self, live_frame: Any) -> dict[str, Any]:
        device_frame = None
        sample = None
        parse_ok = False
        adapter_ok = False
        hand_valid = False
        try:
            device_frame = parse_raw_manus_vive_frame(live_frame.raw_frame, self.adapter_config)
            parse_ok = True
        except Exception:
            self._parse_error_count += 1
            return {
                "parse_ok": False,
                "adapter_ok": False,
                "device_frame": None,
                "sample": None,
                "hand_valid": False,
            }

        hand = getattr(device_frame, "hand", None)
        hand_valid = bool(getattr(hand, "valid", False))
        try:
            sample = self.adapter.to_experiment_input_sample(device_frame)
            adapter_ok = True
        except Exception:
            self._adapter_error_count += 1
        return {
            "parse_ok": parse_ok,
            "adapter_ok": adapter_ok,
            "device_frame": device_frame,
            "sample": sample,
            "hand_valid": hand_valid,
        }

    def _record_frame(
        self,
        live_frame: Any,
        processed: dict[str, Any],
        sample: Any,
        result: Any,
        snapshot: DashboardSnapshot,
    ) -> None:
        if self.session_recorder is None:
            return
        self.session_recorder.record_raw_frame(live_frame.frame_index, live_frame.raw_frame)
        self.session_recorder.record_processed_frame(
            live_frame.frame_index,
            live_frame.raw_frame,
            processed["device_frame"],
            sample,
            result.frame_output,
            haptic_state=result.haptic_feedback_state,
            extra={
                "input_source": str(self.trial_config.get("mode", "live_trial_runner")),
                "trial_time": result.time_since_prompt,
            },
        )
        self._record_events(live_frame.frame_index, sample.time, result.events)
        self.session_recorder.record_haptic(
            live_frame.frame_index,
            sample.time,
            result.haptic_feedback_state,
            details={
                "mode": str(self.trial_config.get("mode", "live_trial_runner")),
                "logical_haptic_label": snapshot.logical_haptic_label,
                "feedback_label": snapshot.feedback_label,
                "hardware_haptic_active": self.config.haptic_hardware_enabled,
                "sent_to_hardware": False,
            },
        )

    def _record_events(self, frame_index: int, time_value: float | None, events: Any) -> None:
        event_count = len(tuple(events or ()))
        self._events_count += event_count
        if self.session_recorder is not None:
            self.session_recorder.record_events(frame_index, time_value, events)

    def _update_snapshot_stats(self, snapshot: DashboardSnapshot) -> None:
        self._logical_haptic_label_counts[snapshot.logical_haptic_label] += 1
        if not snapshot.tracker_valid:
            self._tracker_invalid_frame_count += 1
        if not snapshot.hand_valid:
            self._hand_invalid_frame_count += 1
        if snapshot.pinch_valid:
            self._pinch_valid_frame_count += 1
        if snapshot.large_delta:
            self._large_delta_frame_count += 1
        if snapshot.slip_active:
            self._slip_active_frame_count += 1
        if snapshot.stop_reason == "TRACK_BLOCKED" or snapshot.blocked_force_active:
            self._blocked_frame_count += 1

    def _emit_callbacks(self, snapshot: DashboardSnapshot) -> None:
        self._safe_callback(self.display_callback, snapshot, "display_callback")
        self._safe_callback(self.snapshot_callback, snapshot, "snapshot_callback")
        if self.stats_callback is not None:
            self._safe_callback(self.stats_callback, self.stats_snapshot(), "stats_callback")

    def _safe_callback(self, callback: Callable[[Any], None] | None, value: Any, label: str) -> None:
        if callback is None:
            return
        start = time.monotonic()
        try:
            callback(value)
        except Exception as exc:
            self._callback_error_count += 1
            self.warnings.append(f"{label} failed: {exc}")
        finally:
            self._callback_latency_ms.append((time.monotonic() - start) * 1000.0)

    def _handle_no_new_frame(self, now: float) -> None:
        self._no_new_frame_count += 1
        gap = now - self._last_new_frame_time
        self._max_no_new_frame_gap_seconds = max(self._max_no_new_frame_gap_seconds, gap)
        source_stop_reason = self._source_stop_reason()
        if source_stop_reason == "client_disconnected":
            self.request_stop(
                "client_disconnected_during_trial",
                trial_outcome="CLIENT_DISCONNECTED",
                end_reason="client_disconnected",
            )
        elif source_stop_reason in {"server_stopped", "socket_error", "eof", "source_stopped"}:
            self.request_stop(
                "source_stopped_during_trial",
                trial_outcome="SOURCE_STOPPED",
                end_reason="source_stopped",
            )
        elif gap >= self.config.no_frame_timeout_seconds:
            self.request_stop(
                "no_new_frame_timeout",
                trial_outcome="NO_NEW_FRAME_TIMEOUT",
                end_reason="no_new_frame_timeout",
            )

    def _read_operator_command(self) -> str | None:
        if self.operator_command_checker is None:
            return None
        try:
            command = self.operator_command_checker()
        except Exception as exc:
            self.warnings.append(f"operator_command_checker failed: {exc}")
            return None
        if command is None:
            return None
        command = str(command).strip().lower()
        if command in {"e", "q"}:
            return command
        if command:
            self.warnings.append(f"unknown operator command ignored: {command}")
        return None

    def _record_operator_event(self, event_type: str, command: str) -> None:
        event_time = self._last_sample_time if self._last_sample_time is not None else time.time()
        self._operator_command = command
        self._operator_command_time = event_time
        event = EventRecord(
            time=float(event_time),
            trial_id=self.config.trial_id,
            event_type=event_type,
            state=self.trial.trial_state if self._trial_started else TrialState.WAITING,
            value=command,
            details={
                "operator_command": command,
                "trial_outcome": "MANUAL_COMPLETED" if command == "e" else "ABORTED_BY_OPERATOR",
            },
        )
        frame_index = self._last_snapshot.frame_index if self._last_snapshot is not None else -1
        self._record_events(frame_index, float(event_time), (event,))

    def _set_outcome_for_stop(
        self,
        reason: str,
        *,
        trial_outcome: str | None = None,
        end_reason: str | None = None,
    ) -> None:
        if trial_outcome is None or end_reason is None:
            mapped_outcome, mapped_reason = _outcome_for_run_stop_reason(reason)
            trial_outcome = trial_outcome or mapped_outcome
            end_reason = end_reason or mapped_reason
        if self._trial_outcome is None and trial_outcome is not None:
            self._trial_outcome = trial_outcome
        if self._end_reason is None and end_reason is not None:
            self._end_reason = end_reason

    def _update_target_entry(self, snapshot: DashboardSnapshot) -> None:
        if self._target_box is None or snapshot.block_center_task is None:
            return
        block_center = _list_to_vec3(snapshot.block_center_task)
        if _point_in_box(block_center, self._target_box) and self._first_target_entry_time is None:
            self._first_target_entry_time = snapshot.time
            self._first_target_entry_frame_index = snapshot.frame_index

    def _outcome_summary(self) -> dict[str, Any]:
        if self._trial_outcome is None:
            self._set_outcome_for_stop(self._run_stop_reason)
        trial_end_time = self._last_sample_time
        duration = None
        if self._trial_start_monotonic is not None:
            end_monotonic = self._trial_end_monotonic or self._run_end_time or time.monotonic()
            duration = max(0.0, end_monotonic - self._trial_start_monotonic)
        elif self._trial_start_sample_time is not None and trial_end_time is not None:
            duration = max(0.0, float(trial_end_time) - float(self._trial_start_sample_time))
        detach_count = _detach_count_from_result(self._last_trial_result)
        return {
            "trial_outcome": self._trial_outcome,
            "end_reason": self._end_reason,
            "operator_command": self._operator_command,
            "operator_command_time": self._operator_command_time,
            "manual_completed": self._end_reason == "operator_manual_complete",
            "operator_aborted": self._trial_outcome == "ABORTED_BY_OPERATOR",
            "trial_start_time": self._trial_start_sample_time,
            "trial_end_time": trial_end_time,
            "trial_duration_seconds": duration,
            "max_trial_duration_seconds": self.engine_config.trial_timeout_seconds,
            "detach_count": detach_count,
            "max_detach_count": self.engine_config.max_detach_count,
            "manual_completion_enabled": self.config.manual_completion_enabled,
            "timeout_enabled": self.config.timeout_enabled,
            "detach_limit_enabled": self.config.detach_limit_enabled,
            "last_snapshot_time": self._last_snapshot.time if self._last_snapshot is not None else None,
            "last_frame_index": self._last_snapshot.frame_index if self._last_snapshot is not None else None,
        }

    def _target_diagnostics_summary(self) -> dict[str, Any]:
        snapshot = self._last_snapshot
        block = snapshot.block_center_task if snapshot is not None else None
        pinch = snapshot.pinch_center_task if snapshot is not None else None
        block_vec = _list_to_vec3(block) if block is not None else None
        in_target = None
        distance = None
        if self._target_box is not None and block_vec is not None:
            in_target = _point_in_box(block_vec, self._target_box)
            distance = _distance_to_box(block_vec, self._target_box)
        return {
            "block_center_task_position_at_end": list(block) if block is not None else None,
            "pinch_task_position_at_end": list(pinch) if pinch is not None else None,
            "block_center_in_target_at_end": in_target,
            "distance_to_target_at_end": distance,
            "contact_state_at_end": snapshot.contact_state if snapshot is not None else None,
            "block_motion_state_at_end": snapshot.block_motion_state if snapshot is not None else None,
            "stop_reason_at_end": snapshot.stop_reason if snapshot is not None else None,
            "detach_state_at_end": snapshot.detach_state if snapshot is not None else None,
            "slip_active_at_end": snapshot.slip_active if snapshot is not None else None,
            "slip_reason_at_end": snapshot.slip_reason if snapshot is not None else None,
            "blocked_force_active_at_end": snapshot.blocked_force_active if snapshot is not None else None,
            "logical_haptic_label_at_end": snapshot.logical_haptic_label if snapshot is not None else None,
            "first_target_entry_time": self._first_target_entry_time,
            "first_target_entry_frame_index": self._first_target_entry_frame_index,
        }

    def _source_stop_reason(self) -> str | None:
        if self.source_stop_reason_getter is None:
            return None
        try:
            value = self.source_stop_reason_getter()
        except Exception as exc:
            self._warn_once(f"source_stop_reason_getter failed: {exc}")
            return None
        return str(value) if value is not None else None

    def _total_received_frames(self) -> int:
        stats: dict[str, Any] = {}
        if self.source_stats_getter is not None:
            try:
                stats = self.source_stats_getter() or {}
            except Exception as exc:
                self._warn_once(f"source_stats_getter failed: {exc}")
                stats = {}
        value = stats.get("total_received_frames")
        if value is None:
            value = stats.get("latest_put_count")
        if value is None and hasattr(self.latest_frame_buffer, "stats_snapshot"):
            try:
                snapshot = self.latest_frame_buffer.stats_snapshot()
                value = getattr(snapshot, "put_count", None)
            except Exception as exc:
                self._warn_once(f"latest_frame_buffer.stats_snapshot failed: {exc}")
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


def _to_vec3(value: Any) -> Vec3:
    if isinstance(value, Vec3):
        return value
    if all(hasattr(value, attr) for attr in ("x", "y", "z")):
        return Vec3(float(value.x), float(value.y), float(value.z))
    items = list(value)
    if len(items) != 3:
        raise ValueError("expected a 3D vector.")
    return Vec3(float(items[0]), float(items[1]), float(items[2]))


def _list_to_vec3(value: Any) -> Vec3:
    return _to_vec3(value)


def _target_box_from_trial_config(trial_config: dict[str, Any]) -> tuple[Vec3, Vec3] | None:
    payload = trial_config.get("target_region")
    if not isinstance(payload, dict):
        return None
    minimum = payload.get("min")
    maximum = payload.get("max")
    if minimum is None or maximum is None:
        return None
    try:
        min_vec = _to_vec3(minimum)
        max_vec = _to_vec3(maximum)
    except Exception:
        return None
    return min_vec, max_vec


def _point_in_box(point: Vec3, box: tuple[Vec3, Vec3]) -> bool:
    minimum, maximum = box
    return (
        minimum.x <= point.x <= maximum.x
        and minimum.y <= point.y <= maximum.y
        and minimum.z <= point.z <= maximum.z
    )


def _distance_to_box(point: Vec3, box: tuple[Vec3, Vec3]) -> float:
    minimum, maximum = box
    dx = _axis_distance(point.x, minimum.x, maximum.x)
    dy = _axis_distance(point.y, minimum.y, maximum.y)
    dz = _axis_distance(point.z, minimum.z, maximum.z)
    return Vec3(dx, dy, dz).norm()


def _axis_distance(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum - value
    if value > maximum:
        return value - maximum
    return 0.0


def _detach_count_from_result(result: Any | None) -> int:
    if result is None:
        return 0
    counts = getattr(getattr(result, "frame_output", None), "detach_counts", None)
    value = getattr(counts, "total_detach_count", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _trial_state_outcome(state: TrialState) -> str | None:
    if state == TrialState.ENDED_BY_SUBJECT:
        return "MANUAL_COMPLETED"
    if state == TrialState.FAILED_TIMEOUT:
        return "FAILED_TIMEOUT"
    if state == TrialState.FAILED_TOO_MANY_DETACHES:
        return "FAILED_TOO_MANY_DETACHES"
    return None


def _trial_state_end_reason(state: TrialState) -> str | None:
    if state == TrialState.ENDED_BY_SUBJECT:
        return "subject_end"
    if state == TrialState.FAILED_TIMEOUT:
        return "trial_timeout"
    if state == TrialState.FAILED_TOO_MANY_DETACHES:
        return "too_many_detaches"
    return None


def _outcome_for_run_stop_reason(reason: str) -> tuple[str | None, str | None]:
    mapping = {
        "operator_manual_complete": ("MANUAL_COMPLETED", "operator_manual_complete"),
        "user_quit": ("ABORTED_BY_OPERATOR", "operator_abort"),
        "failed_timeout": ("FAILED_TIMEOUT", "trial_timeout"),
        "failed_too_many_detaches": ("FAILED_TOO_MANY_DETACHES", "too_many_detaches"),
        "client_disconnected_during_trial": ("CLIENT_DISCONNECTED", "client_disconnected"),
        "source_stopped_during_trial": ("SOURCE_STOPPED", "source_stopped"),
        "no_new_frame_timeout": ("NO_NEW_FRAME_TIMEOUT", "no_new_frame_timeout"),
        "duration_reached": ("DURATION_REACHED", "duration_reached"),
        "max_frames": ("MAX_FRAMES_REACHED", "max_frames"),
        "keyboard_interrupt": ("INTERRUPTED", "keyboard_interrupt"),
        "gui_error": ("INTERRUPTED", "gui_error"),
    }
    return mapping.get(str(reason), (None, str(reason) if reason else None))
