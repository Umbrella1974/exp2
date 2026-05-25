"""Integration tests for offline replay session output."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from session_recorder import PROCESSED_FRAME_HEADER


def test_offline_replay_write_session_outputs_standard_session(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw_frames.jsonl"
    raw_frames = [
        _raw_frame(0.0, 0.0),
        _raw_frame(100.0, 0.1),
        _raw_frame(200.0, 0.2),
    ]
    raw_path.write_text("\n".join(json.dumps(frame) for frame in raw_frames), encoding="utf-8")
    out_dir = tmp_path / "offline"
    session_dir = tmp_path / "session"

    completed = subprocess.run(
        [
            sys.executable,
            "offline_replay_autocalibrated.py",
            "--raw-jsonl",
            str(raw_path),
            "--out-dir",
            str(out_dir),
            "--max-frames",
            "3",
            "--calibration-frames",
            "2",
            "--scene-mode",
            "wide-track",
            "--block-size",
            "1.0",
            "--write-session",
            "--session-dir",
            str(session_dir),
            "--session-id",
            "session-test",
            "--subject-id",
            "subject-x",
            "--notes",
            "pytest session",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    expected_files = {
        "session_meta.json",
        "calibration.json",
        "trial_config.json",
        "raw_frames.jsonl",
        "device_frames.jsonl",
        "processed_frames.csv",
        "events.csv",
        "haptic.csv",
        "trial_summary.json",
        "plots",
    }
    assert expected_files == {path.name for path in session_dir.iterdir()}

    session_meta = json.loads((session_dir / "session_meta.json").read_text(encoding="utf-8"))
    assert session_meta["session_id"] == "session-test"
    assert session_meta["mode"] == "offline_autocalibrated"
    assert session_meta["is_formal_calibration"] is False
    assert session_meta["is_formal_scene"] is False
    assert "post-hoc auto calibration" in session_meta["warnings"][0]

    calibration = json.loads((session_dir / "calibration.json").read_text(encoding="utf-8"))
    assert calibration["calibration_type"] == "post_hoc_auto"
    assert calibration["is_formal_calibration"] is False
    assert "calibration_auto" in calibration

    trial_config = json.loads((session_dir / "trial_config.json").read_text(encoding="utf-8"))
    assert trial_config["scene_type"] == "post_hoc_auto"
    assert trial_config["is_formal_scene"] is False
    assert trial_config["block_size"] == 1.0

    raw_lines = (session_dir / "raw_frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in raw_lines] == raw_frames

    with (session_dir / "processed_frames.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert next(reader) == PROCESSED_FRAME_HEADER
        rows = list(reader)
        assert len(rows) == 3

    summary = json.loads((session_dir / "trial_summary.json").read_text(encoding="utf-8"))
    assert summary["total_raw_frames"] == 3
    assert summary["haptic_active_frame_count"] >= 0
    assert summary["haptic_event_count"] >= 0


def test_offline_replay_write_session_refuses_existing_session_dir(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw_frames.jsonl"
    raw_path.write_text(
        "\n".join(json.dumps(frame) for frame in [_raw_frame(0.0, 0.0), _raw_frame(100.0, 0.1)]),
        encoding="utf-8",
    )
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "old.txt").write_text("old", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "offline_replay_autocalibrated.py",
            "--raw-jsonl",
            str(raw_path),
            "--out-dir",
            str(tmp_path / "offline"),
            "--max-frames",
            "2",
            "--calibration-frames",
            "2",
            "--write-session",
            "--session-dir",
            str(session_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "session directory already exists" in completed.stderr


def _raw_frame(timestamp: float, pinch_x: float) -> dict:
    return {
        "timestamp": timestamp,
        "frame": int(timestamp),
        "combined_monotonic_ms": timestamp + 1.0,
        "skeleton_receive_monotonic_ms": timestamp + 2.0,
        "tracker_receive_monotonic_ms": timestamp + 4.5,
        "subject_end": False,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "nodes": [
                    {"id": 4, "position": [pinch_x - 0.005, 0.0, 0.0]},
                    {"id": 9, "position": [pinch_x + 0.005, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "last_update_time": timestamp + 3.0,
                "valid": True,
            }
        ],
    }
