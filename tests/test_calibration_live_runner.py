"""Tests for live table-line calibration runner."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from calibration_io import FormalCalibration
from calibration_live_runner import (
    CalibrationLiveConfig,
    CalibrationSegmentSpec,
    collect_calibration_segment,
    run_live_table_calibration,
)
from live_raw_stream import LiveRawFrame
from simulated_live_source import RawJsonlSimulatedLiveSource


def test_raw_jsonl_simulated_live_source_yields_live_frames(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "raw.jsonl", _calibration_raw_frames())
    source = RawJsonlSimulatedLiveSource(raw_path, timestamp_scale=0.001)

    first = source.get_frame()
    second = source.get_frame()

    assert first is not None
    assert second is not None
    assert first.frame_index == 0
    assert first.raw_frame["timestamp"] == 0.0
    assert second.receive_time_monotonic == pytest.approx(0.5)


def test_collect_calibration_segment_static_point() -> None:
    frames = _calibration_raw_frames()[:11]
    config = _config()
    spec = CalibrationSegmentSpec(
        label="origin",
        prompt="origin",
        duration_seconds=5.0,
        min_samples=10,
        segment_type="static_point",
    )

    summary = collect_calibration_segment(iter(frames), spec, config)

    assert summary["valid_sample_count"] == 11
    assert summary["tracker_valid_count"] == 11
    assert summary["hand_valid_count"] == 11
    assert not summary["errors"]


def test_collect_calibration_segment_line() -> None:
    frames = _calibration_raw_frames()[11:21]
    config = _config()
    spec = CalibrationSegmentSpec(
        label="long_axis_line",
        prompt="long",
        duration_seconds=5.0,
        min_samples=10,
        segment_type="line",
    )

    summary = collect_calibration_segment(iter(frames), spec, config)

    assert summary["valid_sample_count"] == 10
    assert summary["points_world"][0][0] < summary["points_world"][-1][0]
    assert not summary["errors"]


def test_real_live_segment_uses_monotonic_time_not_large_raw_timestamp() -> None:
    source = _FastLiveSource(raw_timestamp=9_999_999_999_000.0)
    config = CalibrationLiveConfig(
        collection_mode="live_stream",
        sample_duration_seconds=0.04,
        min_samples=2,
        min_line_length=0.01,
        timestamp_scale=0.001,
        print_every=0,
    )
    spec = CalibrationSegmentSpec(
        label="long_axis_line",
        prompt="long",
        duration_seconds=0.04,
        min_samples=2,
        segment_type="line",
    )

    summary = collect_calibration_segment(source, spec, config)

    assert summary["time_mode"] == "monotonic_live"
    assert summary["valid_sample_count"] >= 2
    assert summary["duration_seconds_measured"] >= 0.03
    assert source.frame_index > 2
    assert not summary["errors"]


def test_run_live_table_calibration_builds_formal_calibration(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "raw.jsonl", _calibration_raw_frames())
    source = RawJsonlSimulatedLiveSource(raw_path, timestamp_scale=0.001)

    result = run_live_table_calibration(source, _config())

    assert not result.errors
    assert isinstance(result.calibration, FormalCalibration)
    assert result.calibration.calibration_type == "formal_table_lines"
    assert result.calibration.metadata["collection_mode"] == "raw_jsonl_simulated_live"
    assert result.calibration.long_line.sample_count >= 10
    assert result.live_metrics_summary["valid_sample_count"] >= 40
    assert result.segment_summaries[0]["time_mode"] == "frame_time_simulated"


def test_run_live_table_calibration_reports_insufficient_samples(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "short.jsonl", _calibration_raw_frames()[:6])
    source = RawJsonlSimulatedLiveSource(raw_path, timestamp_scale=0.001)

    result = run_live_table_calibration(source, _config())

    assert result.calibration is None
    assert any("only" in error and "valid calibration points" in error for error in result.errors)


def test_validation_warnings_enter_result_and_calibration(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(
        tmp_path / "jitter.jsonl",
        _calibration_raw_frames(origin_jitter=True),
    )
    source = RawJsonlSimulatedLiveSource(raw_path, timestamp_scale=0.001)

    result = run_live_table_calibration(source, _config())

    assert result.calibration is not None
    assert any("origin_record.max_deviation_m" in warning for warning in result.warnings)
    assert any("origin_record.max_deviation_m" in warning for warning in result.calibration.warnings)


def test_simulated_source_rejects_missing_timestamp(tmp_path: Path) -> None:
    raw = _raw_frame(0.0, [0.0, 0.0, 0.0])
    raw.pop("timestamp")
    raw_path = _write_raw_jsonl(tmp_path / "bad.jsonl", [raw])
    source = RawJsonlSimulatedLiveSource(raw_path, timestamp_scale=0.001)

    with pytest.raises(ValueError, match="numeric raw timestamp"):
        source.get_frame()


def test_simulated_segment_missing_frame_time_fails_clearly() -> None:
    raw = _raw_frame(0.0, [0.0, 0.0, 0.0])
    raw.pop("timestamp")
    config = _config()
    spec = CalibrationSegmentSpec(
        label="origin",
        prompt="origin",
        duration_seconds=5.0,
        min_samples=1,
        segment_type="static_point",
    )

    with pytest.raises(ValueError, match="numeric raw timestamps"):
        collect_calibration_segment(iter([raw]), spec, config)


class _FastLiveSource:
    def __init__(self, *, raw_timestamp: float) -> None:
        self.raw_timestamp = raw_timestamp
        self.frame_index = 0

    def get_frame(self, timeout: float | None = None) -> LiveRawFrame:
        del timeout
        time.sleep(0.002)
        frame_index = self.frame_index
        self.frame_index += 1
        raw = _raw_frame(self.raw_timestamp / 1000.0, [frame_index * 0.02, 0.0, 0.0], frame=frame_index)
        raw["timestamp"] = self.raw_timestamp + frame_index
        return LiveRawFrame(
            frame_index=frame_index,
            raw_frame=raw,
            receive_time_monotonic=time.monotonic(),
            receive_wall_time=time.time(),
            byte_length=len(json.dumps(raw)),
        )

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "parse_error_count": 0,
            "bad_json_line_count": 0,
            "dropped_frame_count": 0,
            "stop_reason": None,
        }


def _config() -> CalibrationLiveConfig:
    return CalibrationLiveConfig(
        sample_duration_seconds=5.0,
        min_samples=10,
        min_line_length=0.10,
        timestamp_scale=0.001,
        print_every=0,
    )


def _write_raw_jsonl(path: Path, frames: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(frame, ensure_ascii=False) for frame in frames) + "\n",
        encoding="utf-8",
    )
    return path


def _calibration_raw_frames(*, origin_jitter: bool = False) -> list[dict]:
    frames: list[dict] = []
    for index in range(41):
        seconds = index * 0.5
        if seconds <= 5.0:
            x = 0.06 if origin_jitter and index % 2 else 0.0
            position = [x, 0.0, 0.0]
        elif seconds <= 10.0:
            progress = (seconds - 5.0) / 5.0
            position = [progress, 0.0, 0.0]
        elif seconds <= 15.0:
            progress = (seconds - 10.0) / 5.0
            position = [0.0, progress, 0.0]
        else:
            progress = (seconds - 15.0) / 5.0
            position = [progress, progress, 0.0]
        frames.append(_raw_frame(seconds, position, frame=index))
    return frames


def _raw_frame(seconds: float, position: list[float], *, frame: int = 0) -> dict:
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
                "valid": True,
            }
        ],
    }
