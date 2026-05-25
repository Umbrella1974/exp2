"""Tests for the session-level recorder."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from data_models import (
    BlockMotionState,
    BlockState,
    ContactState,
    FeedbackState,
    HapticFeedbackState,
    PinchState,
    SlipReason,
    Vec3,
)
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from session_recorder import PROCESSED_FRAME_HEADER, SessionRecorder
from trial_controller import ExperimentInputSample


def test_session_recorder_writes_standard_files_and_rows(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    raw = _raw_frame()
    device_frame = parse_raw_manus_vive_frame(raw)
    sample = ExperimentInputSample(
        time=1.25,
        pinch_distance=0.02,
        tracker_valid=True,
        pinch_center_world=np.array([0.0, 0.0, 0.0]),
        subject_end=False,
        metadata={"hand_valid": True, "pinch_valid": True},
    )
    haptic = HapticFeedbackState(slip_active=True, slip_reason=SlipReason.PINCH_INSUFFICIENT)
    frame_output = SimpleNamespace(
        pinch_center_task=Vec3(0.1, 0.2, 0.3),
        contact_state=ContactState.INSIDE_BLOCK,
        pinch_state=PinchState.PINCH_VALID,
        block_state=BlockState(
            center=Vec3(0.4, 0.5, 0.6),
            size=Vec3(1.0, 1.0, 1.0),
            visible=True,
            motion_state=BlockMotionState.GRABBED_MOVING,
        ),
        feedback_state=FeedbackState(tracking_valid=True, recovery_frame=False),
        haptic_feedback=haptic,
    )
    event = SimpleNamespace(
        time=1.25,
        event_type="contact_enter",
        details={"array": np.array([1.0, 2.0]), "vec": Vec3(1.0, 2.0, 3.0)},
    )

    with SessionRecorder(session_dir) as recorder:
        recorder.start_session(
            {"session_id": "s1", "mode": "test", "trial_id": "t1"},
            calibration={"calibration_type": "test"},
            trial_config={"scene_type": "test"},
        )
        recorder.record_raw_frame(99, raw)
        recorder.record_device_frame(0, device_frame)
        recorder.record_processed_frame(
            0,
            raw,
            device_frame,
            sample,
            frame_output,
            haptic_state=haptic,
            extra={"input_source": "pinch", "trial_time": 0.25},
        )
        recorder.record_events(0, sample.time, [event])
        recorder.record_haptic(0, sample.time, haptic_state=haptic)
        recorder.finalize({"total_raw_frames": 1, "safe_vec": Vec3(1.0, 2.0, 3.0)})

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

    raw_lines = (session_dir / "raw_frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(raw_lines[0]) == raw
    assert "frame_index" not in raw_lines[0]

    device_row = json.loads((session_dir / "device_frames.jsonl").read_text(encoding="utf-8"))
    assert device_row["frame_index"] == 0
    assert device_row["tracker_valid"] is True
    assert device_row["node_count"] == 2

    with (session_dir / "processed_frames.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        assert rows[0]["trial_time"] == "0.25"
        assert rows[0]["block_motion_state"] == "GRABBED_MOVING"
        assert rows[0]["slip_reason"] == "PINCH_INSUFFICIENT"

    with (session_dir / "events.csv").open(newline="", encoding="utf-8") as handle:
        event_rows = list(csv.DictReader(handle))
        assert event_rows[0]["event_index"] == "0"
        assert event_rows[0]["event_type"] == "contact_enter"

    with (session_dir / "haptic.csv").open(newline="", encoding="utf-8") as handle:
        haptic_rows = list(csv.DictReader(handle))
        assert haptic_rows[0]["sent_to_hardware"] == "False"
        assert haptic_rows[0]["slip_active"] == "True"

    summary = json.loads((session_dir / "trial_summary.json").read_text(encoding="utf-8"))
    assert summary["safe_vec"] == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_processed_frame_header_is_stable(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    recorder = SessionRecorder(session_dir)
    recorder.start_session({"session_id": "s1", "mode": "test", "trial_id": "t1"})

    with (session_dir / "processed_frames.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        assert next(reader) == PROCESSED_FRAME_HEADER


def test_existing_session_dir_requires_explicit_overwrite(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "old.txt").write_text("old", encoding="utf-8")

    recorder = SessionRecorder(session_dir)
    try:
        recorder.start_session({"session_id": "s1", "mode": "test", "trial_id": "t1"})
    except FileExistsError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected FileExistsError")

    overwrite_recorder = SessionRecorder(session_dir, overwrite=True)
    overwrite_recorder.start_session({"session_id": "s1", "mode": "test", "trial_id": "t1"})
    assert not (session_dir / "old.txt").exists()


def _raw_frame() -> dict:
    return {
        "timestamp": 1250,
        "frame": 7,
        "combined_monotonic_ms": 11.5,
        "skeleton_receive_monotonic_ms": 20.0,
        "tracker_receive_monotonic_ms": 23.0,
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
                "position": [1.0, 2.0, 3.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "last_update_time": 10.0,
                "valid": True,
            }
        ],
    }
