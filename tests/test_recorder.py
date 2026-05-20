"""Tests for Stage 2 CSV recording."""

from __future__ import annotations

import csv
import json

from block_controller import BlockController
from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from experiment_recorder import ExperimentRecorder
from trial_controller import EventRecord, ExperimentInputSample, TrialController, TrialState


def test_recorder_writes_frames_and_events_csv(tmp_path) -> None:
    config = EngineConfig()
    track = TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(4.0, 4.0, 4.0)),))

    def factory() -> BlockController:
        return BlockController(config, track, Vec3(0.0, 0.0, 0.0))

    recorder = ExperimentRecorder(tmp_path)
    controller = TrialController(factory, None, config, recorder=recorder)
    controller.start_trial(time=0.0, trial_id="trial-csv")
    controller.update(
        ExperimentInputSample(
            time=0.0,
            pinch_center_task=Vec3(0.0, 0.0, 0.0),
            pinch_distance=0.01,
            tracker_valid=True,
        )
    )
    recorder.log_events(
        (
            EventRecord(
                time=1.0,
                trial_id="trial-csv",
                event_type="custom",
                state=TrialState.RUNNING,
                details={"nested": {"ok": True}},
            ),
        )
    )

    with (tmp_path / "frames.csv").open(newline="", encoding="utf-8") as handle:
        frame_rows = list(csv.DictReader(handle))
    assert frame_rows
    assert "trial_id" in frame_rows[0]
    assert "block_center_task_x" in frame_rows[0]
    assert "slip_active" in frame_rows[0]

    with (tmp_path / "events.csv").open(newline="", encoding="utf-8") as handle:
        event_rows = list(csv.DictReader(handle))
    assert event_rows
    assert "details_json" in event_rows[0]
    custom = [row for row in event_rows if row["event_type"] == "custom"][0]
    assert json.loads(custom["details_json"]) == {"nested": {"ok": True}}
