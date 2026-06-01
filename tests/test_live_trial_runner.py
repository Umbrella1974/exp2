"""Tests for the reusable Stage 5C live trial loop."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from latest_frame_buffer import LatestFrameBufferStats
from live_raw_stream import LiveRawFrame
from live_trial_runner import LiveTrialRunner, LiveTrialRunnerConfig
from session_recorder import SessionRecorder
from task_coordinate_system import TaskCoordinateSystem


def test_step_once_processes_one_frame_and_builds_snapshot(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])

    snapshot = runner.step_once()

    assert snapshot is not None
    assert snapshot.frame_index == 0
    assert runner.trial_started is True
    assert runner.stats_snapshot().total_processed_frames == 1


def test_run_until_done_stops_at_max_frames(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0), _live_frame(1, 0.02)],
        max_frames=1,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.stats.total_processed_frames == 1
    assert result.summary["trial_controller_started"] is True


def test_parse_error_increments_stats_without_crashing(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0, raw_frame="bad")])

    snapshot = runner.step_once()

    assert snapshot is None
    assert runner.stats_snapshot().parse_error_count == 1
    assert runner.stats_snapshot().total_processed_frames == 0


def test_adapter_error_increments_stats_without_crashing(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])
    runner.adapter = _RaisingAdapter()

    snapshot = runner.step_once()

    assert snapshot is None
    assert runner.stats_snapshot().adapter_error_count == 1
    assert runner.stats_snapshot().total_processed_frames == 0


def test_no_new_frame_timeout_exits(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [],
        control_rate_hz=1000.0,
        no_frame_timeout_seconds=0.01,
        max_frames=None,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "no_new_frame_timeout"
    assert result.stats.no_new_frame_count > 0


def test_request_stop_is_safe_before_run(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)], max_frames=None)

    runner.request_stop("manual_stop")
    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "manual_stop"
    assert result.stats.total_processed_frames == 0


def test_snapshot_callback_is_called(tmp_path: Path) -> None:
    snapshots = []
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0)],
        snapshot_callback=snapshots.append,
    )

    runner.run_until_done()

    assert len(snapshots) == 1
    assert snapshots[0].frame_index == 0


def test_snapshot_callback_exception_is_counted(tmp_path: Path) -> None:
    def failing_callback(snapshot: Any) -> None:
        del snapshot
        raise RuntimeError("display boom")

    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0)],
        snapshot_callback=failing_callback,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.stats.callback_error_count == 1
    assert "snapshot_callback failed" in result.summary["warnings"][0]


def test_logical_haptic_label_counts_are_reported(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])

    result = runner.run_until_done()

    assert result.stats.logical_haptic_label_counts["NONE"] == 1


def test_session_recorder_can_finalize_runner_summary(tmp_path: Path) -> None:
    runner, recorder, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])

    result = runner.run_until_done()
    recorder.finalize(result.summary)

    trial_summary = json.loads((tmp_path / "session" / "trial_summary.json").read_text(encoding="utf-8"))
    assert trial_summary["total_processed_frames"] == 1
    assert (tmp_path / "session" / "processed_frames.csv").exists()


class _FakeLatestFrameBuffer:
    def __init__(self, frames: list[LiveRawFrame]) -> None:
        self.frames = list(frames)
        self.put_count = len(frames)
        self.consumed_count = 0

    def get_latest(
        self,
        *,
        allow_already_consumed: bool = False,
        consume: bool = True,
    ) -> LiveRawFrame | None:
        del allow_already_consumed, consume
        if not self.frames:
            return None
        self.consumed_count += 1
        return self.frames.pop(0)

    def stats_snapshot(self) -> LatestFrameBufferStats:
        return LatestFrameBufferStats(
            put_count=self.put_count,
            consumed_count=self.consumed_count,
            overwritten_frame_count=0,
            dropped_old_frame_count=0,
            last_frame_index=self.consumed_count - 1 if self.consumed_count else None,
            last_receive_time=None,
            has_unconsumed_frame=bool(self.frames),
        )


class _RaisingAdapter:
    def to_experiment_input_sample(self, device_frame: Any) -> None:
        del device_frame
        raise RuntimeError("adapter boom")


def _make_runner(
    tmp_path: Path,
    frames: list[LiveRawFrame],
    *,
    snapshot_callback: Any = None,
    control_rate_hz: float = 1000.0,
    no_frame_timeout_seconds: float = 0.05,
    max_frames: int | None = 1,
) -> tuple[LiveTrialRunner, SessionRecorder, _FakeLatestFrameBuffer]:
    buffer = _FakeLatestFrameBuffer(frames)
    recorder = SessionRecorder(tmp_path / "session", overwrite=True)
    recorder.start_session(
        session_meta={"mode": "test_live_trial_runner"},
        calibration=None,
        trial_config={"mode": "test_live_trial_runner"},
    )
    runner = LiveTrialRunner(
        latest_frame_buffer=buffer,
        task_coordinate_system=_task_system(),
        track_region=_track_region(),
        block_initial_center_task=Vec3(0.0, 0.0, 0.0),
        block_size=Vec3(0.2, 0.2, 0.2),
        engine_config=EngineConfig(
            block_size_x=0.2,
            block_size_y=0.2,
            block_size_z=0.2,
            trial_timeout_seconds=1e9,
            max_detach_count=1_000_000,
        ),
        session_recorder=recorder,
        config=LiveTrialRunnerConfig(
            trial_id="trial_test",
            control_rate_hz=control_rate_hz,
            max_frames=max_frames,
            no_frame_timeout_seconds=no_frame_timeout_seconds,
        ),
        map_id="test_map",
        calibration_id="test_calibration",
        trial_config={"mode": "test_live_trial_runner"},
        snapshot_callback=snapshot_callback,
        source_stats_getter=lambda: {"total_received_frames": buffer.put_count},
    )
    return runner, recorder, buffer


def _task_system() -> TaskCoordinateSystem:
    return TaskCoordinateSystem.build_from_origin_and_x_point(
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    )


def _track_region() -> TrackRegion:
    return TrackRegion(
        boxes=(
            Box3D(
                center=Vec3(0.25, 0.0, 0.0),
                size=Vec3(1.0, 1.0, 1.0),
            ),
        )
    )


def _live_frame(
    frame_index: int,
    x_position: float,
    *,
    raw_frame: Any | None = None,
) -> LiveRawFrame:
    raw = raw_frame if raw_frame is not None else _raw_frame(float(frame_index), [x_position, 0.0, 0.0])
    return LiveRawFrame(
        frame_index=frame_index,
        raw_frame=raw,
        receive_time_monotonic=time.monotonic(),
        receive_wall_time=time.time(),
        byte_length=len(json.dumps(raw, default=str)),
    )


def _raw_frame(seconds: float, position: list[float]) -> dict[str, Any]:
    return {
        "timestamp": seconds * 1000.0,
        "frame": int(seconds),
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
                "valid": True,
            }
        ],
    }
