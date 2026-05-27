"""Tests for raw JSONL table-line calibration script."""

from __future__ import annotations

import json
from pathlib import Path

from calibrate_from_raw_jsonl_table import main


def test_generates_formal_calibration_json(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path, _calibration_frames())
    out_path = tmp_path / "calibration.json"
    exit_code = main(
        [
            "--raw-jsonl",
            str(raw_path),
            "--origin-start-frame",
            "0",
            "--long-line-start-frame",
            "10",
            "--width-line-start-frame",
            "20",
            "--diagonal-line-start-frame",
            "30",
            "--sample-window-frames",
            "10",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["calibration_type"] == "formal_table_lines"
    assert payload["is_formal_calibration"] is True
    assert payload["quality"]["plane_fit_rmse_m"] == 0.0
    assert "x_y_angle_degrees" in payload["quality"]
    assert payload["metadata"]["source"] == "raw_jsonl_simulated_table_line_calibration"


def test_time_window_uses_timestamp_when_frame_window_is_not_provided(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path, _calibration_frames())
    out_path = tmp_path / "calibration_time_window.json"
    exit_code = main(
        [
            "--raw-jsonl",
            str(raw_path),
            "--origin-start-frame",
            "0",
            "--long-line-start-frame",
            "10",
            "--width-line-start-frame",
            "20",
            "--diagonal-line-start-frame",
            "30",
            "--sample-duration-seconds",
            "9",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["origin"]["sample_count"] == 10


def test_line_too_short_fails_without_saving(tmp_path: Path) -> None:
    frames = _calibration_frames(long_scale=0.005)
    raw_path = _write_raw_jsonl(tmp_path, frames)
    out_path = tmp_path / "bad_calibration.json"
    exit_code = main(
        [
            "--raw-jsonl",
            str(raw_path),
            "--origin-start-frame",
            "0",
            "--long-line-start-frame",
            "10",
            "--width-line-start-frame",
            "20",
            "--diagonal-line-start-frame",
            "30",
            "--sample-window-frames",
            "10",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 1
    assert not out_path.exists()


def test_missing_timestamp_requires_frame_window(tmp_path: Path) -> None:
    frames = _calibration_frames()
    for frame in frames:
        frame.pop("timestamp")
    raw_path = _write_raw_jsonl(tmp_path, frames)
    out_path = tmp_path / "missing_timestamp.json"
    exit_code = main(
        [
            "--raw-jsonl",
            str(raw_path),
            "--origin-start-frame",
            "0",
            "--long-line-start-frame",
            "10",
            "--width-line-start-frame",
            "20",
            "--diagonal-line-start-frame",
            "30",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 1
    assert not out_path.exists()


def test_too_few_samples_fails_without_saving(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path, _calibration_frames())
    out_path = tmp_path / "few_samples.json"
    exit_code = main(
        [
            "--raw-jsonl",
            str(raw_path),
            "--origin-start-frame",
            "0",
            "--long-line-start-frame",
            "10",
            "--width-line-start-frame",
            "20",
            "--diagonal-line-start-frame",
            "30",
            "--sample-window-frames",
            "10",
            "--min-samples",
            "11",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 1
    assert not out_path.exists()


def _calibration_frames(*, long_scale: float = 1.0) -> list[dict]:
    frames: list[dict] = []
    for index in range(10):
        frames.append(_raw_frame(index, [0.0, 0.0, 0.0]))
    for index in range(10):
        frames.append(_raw_frame(10 + index, [long_scale * index / 9.0, 0.0, 0.0]))
    for index in range(10):
        frames.append(_raw_frame(20 + index, [0.0, index / 9.0, 0.0]))
    for index in range(10):
        frames.append(_raw_frame(30 + index, [index / 9.0, index / 9.0, 0.0]))
    return frames


def _raw_frame(index: int, tracker_position: list[float]) -> dict:
    timestamp = index * 1000.0
    return {
        "timestamp": timestamp,
        "frame": index,
        "subject_end": False,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "nodes": [
                    {"id": 4, "position": [-0.005, 0.0, 0.0]},
                    {"id": 9, "position": [0.005, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": tracker_position,
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": True,
            }
        ],
    }


def _write_raw_jsonl(tmp_path: Path, frames: list[dict]) -> Path:
    path = tmp_path / "raw_frames.jsonl"
    path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")
    return path
