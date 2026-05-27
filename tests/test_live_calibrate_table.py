"""Tests for live_calibrate_table CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from live_calibrate_table import main


def test_cli_raw_jsonl_simulated_live_writes_calibration(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "raw.jsonl", _calibration_raw_frames())
    out_path = tmp_path / "calibration.json"

    status = main(
        [
            "--raw-jsonl",
            str(raw_path),
            "--simulate-live",
            "--auto-advance",
            "--no-confirm-save",
            "--sample-duration-seconds",
            "5",
            "--min-samples",
            "10",
            "--print-every",
            "0",
            "--out",
            str(out_path),
        ]
    )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert status == 0
    assert payload["calibration_type"] == "formal_table_lines"
    assert payload["metadata"]["collection_mode"] == "raw_jsonl_simulated_live"
    assert payload["metadata"]["is_formal_calibration"] is True


def test_cli_missing_input_source_errors() -> None:
    with pytest.raises(SystemExit):
        main(["--auto-advance", "--no-confirm-save"])


def test_cli_raw_jsonl_and_live_stream_are_mutually_exclusive(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "raw.jsonl", _calibration_raw_frames())
    with pytest.raises(SystemExit):
        main(
            [
                "--raw-jsonl",
                str(raw_path),
                "--simulate-live",
                "--use-live-stream",
            ]
        )


def test_cli_raw_jsonl_requires_simulate_live(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "raw.jsonl", _calibration_raw_frames())
    with pytest.raises(SystemExit):
        main(["--raw-jsonl", str(raw_path)])


def _write_raw_jsonl(path: Path, frames: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(frame, ensure_ascii=False) for frame in frames) + "\n",
        encoding="utf-8",
    )
    return path


def _calibration_raw_frames() -> list[dict]:
    frames: list[dict] = []
    for index in range(41):
        seconds = index * 0.5
        if seconds <= 5.0:
            position = [0.0, 0.0, 0.0]
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


def _raw_frame(seconds: float, position: list[float], *, frame: int) -> dict:
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
