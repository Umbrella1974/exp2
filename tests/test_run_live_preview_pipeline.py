"""Tests for run_live_preview smoke pipeline wiring."""

from __future__ import annotations

from raw_frame_source import IterableRawFrameSource
from run_live_preview import run_preview


def raw_frame(timestamp: float, pinch_x: float, *, subject_end: bool = False) -> dict:
    return {
        "timestamp": timestamp,
        "frame": int(timestamp * 100),
        "subject_end": subject_end,
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
                "valid": True,
            }
        ],
    }


def test_run_live_preview_uses_raw_source_pipeline(capsys) -> None:
    source = IterableRawFrameSource(
        [
            raw_frame(0.0, 0.0),
            raw_frame(0.1, 0.2),
            raw_frame(0.2, 0.35, subject_end=True),
        ]
    )
    run_preview(source, max_frames=3, print_every=2)
    output = capsys.readouterr().out
    assert "frame_index" in output
    assert "trial_state" in output
    assert "pinch_center_task" in output
