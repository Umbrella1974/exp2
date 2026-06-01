"""Tests for the Stage 5C integrated live session runner."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import run_live_integrated_session as runner
from live_raw_stream import LiveRawFrame
from live_session_state import LiveSessionPhase
from run_live_integrated_session import (
    LiveIntegratedSessionConfig,
    run_live_integrated_session,
)


def test_integrated_session_completes_and_keeps_calibration_consistent(tmp_path: Path) -> None:
    source = FakeLiveSource()
    config = _config(tmp_path, calibration_id="cal_consistent", max_frames=3)

    result = run_live_integrated_session(
        config,
        source=source,
        input_fn=_mode_input(source),
    )

    session_dir = Path(result.summary["session_dir"])
    calibration = _read_json(session_dir / "calibration.json")
    meta = _read_json(session_dir / "session_meta.json")
    trial_config = _read_json(session_dir / "trial_config.json")
    out_calibration = _read_json(tmp_path / "out" / "calibration.json")

    assert result.summary["run_stop_reason"] == "max_frames"
    assert result.summary["session_finalized"] is True
    assert result.summary["trial_controller_started"] is True
    assert result.calibration is not None
    assert result.calibration.metadata["collection_mode"] == "live_stream_integrated"
    assert calibration["calibration_id"] == "cal_consistent"
    assert out_calibration["calibration_id"] == "cal_consistent"
    assert meta["calibration_id"] == "cal_consistent"
    assert trial_config["calibration_id"] == "cal_consistent"
    assert trial_config["task_coordinate_system"] == calibration["task_coordinate_system"]
    assert result.summary["map_anchor_mode"] == "none"
    assert meta["map_anchor_mode"] == "none"
    assert trial_config["map_anchor_mode"] == "none"
    assert meta["calibration_collection_mode"] == "live_stream_integrated"
    assert meta["haptic_hardware_enabled"] is False
    assert result.summary["source_stop_reason"] is None
    assert "pump_stop_reason" in result.summary
    assert result.summary["latest_buffer_last_frame_index"] is not None
    assert result.summary["latest_buffer_overwritten_frame_count"] >= 0
    assert result.summary["calibration_segment_time_mode"] == "monotonic_live"
    assert result.summary["live_trial_runner_summary"]["trial_id"] == "trial_001"
    assert result.summary["live_trial_runner_summary"]["total_processed_frames"] == 3


def test_debug_anchor_is_explicitly_marked(tmp_path: Path) -> None:
    source = FakeLiveSource()
    config = _config(
        tmp_path,
        calibration_id="cal_anchor",
        max_frames=1,
        anchor_current_pinch_debug=True,
    )

    result = run_live_integrated_session(config, source=source, input_fn=_mode_input(source))

    session_dir = Path(result.summary["session_dir"])
    meta = _read_json(session_dir / "session_meta.json")
    trial_config = _read_json(session_dir / "trial_config.json")

    assert result.summary["run_stop_reason"] == "max_frames"
    assert result.summary["map_anchor_mode"] == "current_pinch_debug"
    assert meta["map_anchor_mode"] == "current_pinch_debug"
    assert trial_config["map_anchor_mode"] == "current_pinch_debug"
    assert meta["is_formal_experiment"] is False
    assert any("not a formal calibrated trial" in warning for warning in meta["warnings"])


def test_keyboard_interrupt_at_trial_prompt_finalizes_session(tmp_path: Path) -> None:
    source = FakeLiveSource()
    config = _config(tmp_path, calibration_id="cal_interrupt", max_frames=3)

    def input_fn(message: str) -> str:
        _set_mode_from_prompt(source, message)
        if "start trial" in message:
            raise KeyboardInterrupt
        return ""

    result = run_live_integrated_session(config, source=source, input_fn=input_fn)

    session_dir = Path(result.summary["session_dir"])
    trial_summary = _read_json(session_dir / "trial_summary.json")
    assert result.summary["run_stop_reason"] == "keyboard_interrupt"
    assert result.summary["session_finalized"] is True
    assert result.summary["trial_controller_started"] is False
    assert trial_summary["run_stop_reason"] == "keyboard_interrupt"


def test_keyboard_interrupt_at_calibration_prompt_writes_partial_summary(tmp_path: Path) -> None:
    source = FakeLiveSource()
    config = _config(tmp_path, calibration_id="cal_interrupt_prompt")

    def input_fn(message: str) -> str:
        if "[origin]" in message:
            raise KeyboardInterrupt
        return ""

    result = run_live_integrated_session(config, source=source, input_fn=input_fn)
    summary_path = tmp_path / "out" / "summary.json"

    assert summary_path.exists()
    assert result.summary["run_stop_reason"] == "keyboard_interrupt"
    assert result.summary["session_finalized"] is False
    assert result.summary["trial_controller_started"] is False


def test_keyboard_interrupt_during_trial_finalizes_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeLiveSource()
    config = _config(tmp_path, calibration_id="cal_interrupt_trial", max_frames=None)
    trial_started = {"value": False}
    original_step_once = runner.LiveTrialRunner.step_once

    def input_fn(message: str) -> str:
        _set_mode_from_prompt(source, message)
        if "start trial" in message:
            trial_started["value"] = True
        return ""

    def raising_step_once(self: Any) -> Any:
        if trial_started["value"]:
            raise KeyboardInterrupt
        return original_step_once(self)

    monkeypatch.setattr(runner.LiveTrialRunner, "step_once", raising_step_once)

    result = runner.run_live_integrated_session(config, source=source, input_fn=input_fn)

    session_dir = Path(result.summary["session_dir"])
    assert result.summary["run_stop_reason"] == "keyboard_interrupt"
    assert result.summary["session_finalized"] is True
    assert _read_json(session_dir / "trial_summary.json")["run_stop_reason"] == "keyboard_interrupt"


def test_calibration_error_stops_before_trial(tmp_path: Path) -> None:
    source = FakeLiveSource()
    config = _config(tmp_path, calibration_id="cal_bad", min_line_length=10.0)

    result = run_live_integrated_session(config, source=source, input_fn=_mode_input(source))

    assert result.summary["run_stop_reason"] == "calibration_failed"
    assert result.summary["phase_at_stop"] == LiveSessionPhase.CALIBRATION_FAILED.name
    assert result.summary["trial_controller_started"] is False
    assert result.summary["session_finalized"] is False
    assert result.summary["errors"]


def test_stream_wait_timeout_writes_summary(tmp_path: Path) -> None:
    source = NoFrameLiveSource()
    config = _config(
        tmp_path,
        calibration_id="cal_stream_timeout",
        stream_wait_timeout_seconds=0.05,
    )

    result = run_live_integrated_session(config, source=source, input_fn=_mode_input(source))
    disk_summary = _read_json(tmp_path / "out" / "summary.json")

    assert result.summary["run_stop_reason"] == "stream_wait_timeout"
    assert result.summary["phase_at_stop"] == LiveSessionPhase.WAITING_FOR_STREAM.name
    assert disk_summary["run_stop_reason"] == "stream_wait_timeout"
    assert result.summary["stream_wait_timeout_seconds"] == pytest.approx(0.05)


def test_valid_tracker_timeout_writes_summary(tmp_path: Path) -> None:
    source = FakeLiveSource(tracker_valid=False)
    config = _config(
        tmp_path,
        calibration_id="cal_tracker_timeout",
        valid_tracker_timeout_seconds=0.05,
    )

    result = run_live_integrated_session(config, source=source, input_fn=_mode_input(source))

    assert result.summary["run_stop_reason"] == "valid_tracker_timeout"
    assert result.summary["phase_at_stop"] == LiveSessionPhase.WAITING_FOR_VALID_TRACKER.name
    assert result.summary["valid_tracker_timeout_seconds"] == pytest.approx(0.05)
    assert result.summary["session_finalized"] is False


def test_trial_client_disconnected_exits_and_writes_summary(tmp_path: Path) -> None:
    source = FakeLiveSource(disconnect_after_trial_frames=2)
    config = _config(
        tmp_path,
        calibration_id="cal_disconnect_trial",
        max_frames=None,
        no_frame_timeout_seconds=0.2,
    )

    result = run_live_integrated_session(config, source=source, input_fn=_mode_input(source))

    assert result.summary["run_stop_reason"] == "client_disconnected_during_trial"
    assert result.summary["session_finalized"] is True
    assert result.summary["source_stop_reason"] == "client_disconnected"
    assert result.summary["no_new_frame_count"] > 0


def test_map_validation_error_stops_before_trial(tmp_path: Path) -> None:
    source = FakeLiveSource()
    config = _config(
        tmp_path,
        calibration_id="cal_map_bad",
        map_path=_write_invalid_map(tmp_path / "bad_map.json"),
    )

    result = run_live_integrated_session(config, source=source, input_fn=_mode_input(source))

    assert result.summary["run_stop_reason"] == "map_validation_failed"
    assert result.summary["trial_controller_started"] is False
    assert result.summary["session_finalized"] is False
    assert any("inside at least one track box" in error for error in result.summary["errors"])


def test_text_display_does_not_block_control_loop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = FakeLiveSource()
    config = _config(
        tmp_path,
        calibration_id="cal_text",
        max_frames=2,
        display_mode="text",
        print_every=1,
    )

    result = run_live_integrated_session(config, source=source, input_fn=_mode_input(source))

    captured = capsys.readouterr()
    assert result.summary["run_stop_reason"] == "max_frames"
    assert "PHASE=TRIAL_RUNNING" in captured.out


class FakeLiveSource:
    """Small latest-frame compatible source for integrated-session tests."""

    def __init__(
        self,
        *,
        frame_interval: float = 0.0015,
        tracker_valid: bool = True,
        disconnect_after_trial_frames: int | None = None,
    ) -> None:
        self.frame_interval = frame_interval
        self.tracker_valid = tracker_valid
        self.disconnect_after_trial_frames = disconnect_after_trial_frames
        self.stop_event = threading.Event()
        self.mode = "origin"
        self.frame_index = 0
        self.mode_frame_index = 0
        self.total_received_frames = 0
        self.dropped_frame_count = 0
        self.parse_error_count = 0
        self.bad_json_line_count = 0
        self.stop_reason: str | None = None
        self._running = False
        self._last_emit_time = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self.stop_event.clear()
        self.stop_reason = None

    def stop(self, reason: str = "stopped") -> None:
        self.stop_reason = reason
        self._running = False
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode
            self.mode_frame_index = 0

    def get_frame(self, timeout: float | None = None) -> LiveRawFrame | None:
        if self.stop_event.is_set() or not self._running:
            return None
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.stop_event.is_set():
            now = time.monotonic()
            wait = self.frame_interval - (now - self._last_emit_time)
            if wait <= 0.0:
                return self._make_live_frame(now)
            if timeout == 0.0:
                return None
            if deadline is not None:
                remaining = deadline - now
                if remaining <= 0.0:
                    return None
                wait = min(wait, remaining)
            time.sleep(min(wait, 0.001))
        return None

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "total_received_frames": self.total_received_frames,
            "parse_error_count": self.parse_error_count,
            "bad_json_line_count": self.bad_json_line_count,
            "dropped_frame_count": self.dropped_frame_count,
            "stop_reason": self.stop_reason,
            "running": self._running,
        }

    def _make_live_frame(self, now: float) -> LiveRawFrame:
        with self._lock:
            mode = self.mode
            mode_index = self.mode_frame_index
            self.mode_frame_index += 1
        raw = _raw_frame(
            now,
            _position_for_mode(mode, mode_index),
            frame=self.frame_index,
            tracker_valid=self.tracker_valid,
        )
        live_frame = LiveRawFrame(
            frame_index=self.frame_index,
            raw_frame=raw,
            receive_time_monotonic=now,
            receive_wall_time=time.time(),
            byte_length=len(json.dumps(raw)),
        )
        self.frame_index += 1
        self.total_received_frames += 1
        self._last_emit_time = now
        if (
            self.disconnect_after_trial_frames is not None
            and mode == "trial"
            and mode_index + 1 >= self.disconnect_after_trial_frames
        ):
            self.stop_reason = "client_disconnected"
            self._running = False
            self.stop_event.set()
        return live_frame


class NoFrameLiveSource:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.stop_reason: str | None = None
        self.total_received_frames = 0
        self.dropped_frame_count = 0

    def start(self) -> None:
        self.stop_event.clear()
        self.stop_reason = None

    def stop(self, reason: str = "stopped") -> None:
        self.stop_reason = reason
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def get_frame(self, timeout: float | None = None) -> None:
        if timeout:
            time.sleep(min(float(timeout), 0.002))
        return None

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "total_received_frames": self.total_received_frames,
            "parse_error_count": 0,
            "bad_json_line_count": 0,
            "dropped_frame_count": self.dropped_frame_count,
            "stop_reason": self.stop_reason,
        }


def _mode_input(source: FakeLiveSource):
    def input_fn(message: str) -> str:
        _set_mode_from_prompt(source, message)
        return ""

    return input_fn


def _set_mode_from_prompt(source: FakeLiveSource, message: str) -> None:
    if "[origin]" in message:
        source.set_mode("origin")
    elif "[long_axis_line]" in message:
        source.set_mode("long_axis_line")
    elif "[width_axis_line]" in message:
        source.set_mode("width_axis_line")
    elif "[diagonal_line]" in message:
        source.set_mode("diagonal_line")
    elif "start trial" in message:
        source.set_mode("trial")


def _position_for_mode(mode: str, frame_index: int) -> list[float]:
    progress = float(frame_index) * 0.04
    if mode == "long_axis_line":
        return [progress, 0.0, 0.0]
    if mode == "width_axis_line":
        return [0.0, progress, 0.0]
    if mode == "diagonal_line":
        return [progress, progress, 0.0]
    if mode == "trial":
        return [min(progress, 0.08), 0.0, 0.0]
    return [0.0, 0.0, 0.0]


def _raw_frame(
    seconds: float,
    position: list[float],
    *,
    frame: int,
    tracker_valid: bool = True,
) -> dict[str, Any]:
    return {
        "timestamp": seconds * 1000.0,
        "frame": frame,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "side": "left",
                "nodes": [
                    {"id": 4, "position": [-0.005, 0.0, 0.0]},
                    {"id": 9, "position": [0.005, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": position,
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": tracker_valid,
            }
        ],
    }


def _config(
    tmp_path: Path,
    *,
    calibration_id: str,
    map_path: Path | None = None,
    max_frames: int | None = 1,
    min_line_length: float = 0.02,
    display_mode: str = "none",
    print_every: int = 0,
    anchor_current_pinch_debug: bool = False,
    stream_wait_timeout_seconds: float = 60.0,
    valid_tracker_timeout_seconds: float = 60.0,
    no_frame_timeout_seconds: float = 5.0,
) -> LiveIntegratedSessionConfig:
    return LiveIntegratedSessionConfig(
        map_config=map_path or _write_valid_map(tmp_path / "map.json"),
        out_dir=tmp_path / "out",
        session_dir=tmp_path / "out" / "session",
        overwrite_session=True,
        trial_id="trial_001",
        calibration_id=calibration_id,
        sample_duration_seconds=1.02,
        min_samples=2,
        min_line_length=min_line_length,
        confirm_calibration=False,
        allow_calibration_warnings=True,
        control_rate_hz=240.0,
        max_frames=max_frames,
        display_mode=display_mode,
        print_every=print_every,
        anchor_current_pinch_debug=anchor_current_pinch_debug,
        anchor_timeout_seconds=1.0,
        stream_wait_timeout_seconds=stream_wait_timeout_seconds,
        valid_tracker_timeout_seconds=valid_tracker_timeout_seconds,
        no_frame_timeout_seconds=no_frame_timeout_seconds,
    )


def _write_valid_map(path: Path) -> Path:
    payload = {
        "map_id": "test_map",
        "description": "test",
        "coordinate_space": "task",
        "unit": "m",
        "block_initial_center_task": [0.0, 0.0, 0.0],
        "block_size": [0.2, 0.2, 0.2],
        "track_boxes": [
            {
                "id": "track",
                "min": [-0.2, -0.2, -0.2],
                "max": [0.6, 0.2, 0.2],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_invalid_map(path: Path) -> Path:
    payload = {
        "map_id": "bad_map",
        "description": "bad",
        "coordinate_space": "task",
        "unit": "m",
        "block_initial_center_task": [10.0, 0.0, 0.0],
        "block_size": [0.2, 0.2, 0.2],
        "track_boxes": [
            {
                "id": "track",
                "min": [-0.2, -0.2, -0.2],
                "max": [0.6, 0.2, 0.2],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
