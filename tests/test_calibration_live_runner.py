"""Tests for live table-line calibration runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from calibration_io import FormalCalibration
from calibration_live_runner import (
    CalibrationLiveConfig,
    CalibrationSegmentSpec,
    collect_calibration_segment,
    run_live_table_calibration,
)
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
