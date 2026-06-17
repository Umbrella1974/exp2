"""Tests for pinch-distance threshold calibration helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from device_frame_models import DeviceAdapterConfig
from live_raw_stream import LiveRawFrame
from manus_vive_adapter import ManusViveExperimentAdapter
from pinch_threshold_calibration import (
    PinchThresholdCalibrationConfig,
    build_pinch_node_config_payload,
    build_pinch_threshold_calibration_payload,
    collect_pinch_distance_window,
    load_pinch_threshold_config,
    load_pinch_threshold_json,
)


def test_pinch_threshold_calibration_uses_repeat_medians_and_formula() -> None:
    config = PinchThresholdCalibrationConfig()

    payload = build_pinch_threshold_calibration_payload(
        open_repeat_values_m=[0.08, 0.09, 0.085],
        closed_repeat_values_m=[0.02, 0.018, 0.019],
        config=config,
        node_config=_node_config(),
        tracker_valid_sample_fraction=0.5,
    )

    range_m = 0.085 - 0.019
    assert payload["open_distance_m"] == pytest.approx(0.085)
    assert payload["closed_distance_m"] == pytest.approx(0.019)
    assert payload["pinch_on_threshold_m"] == pytest.approx(0.019 + 0.40 * range_m)
    assert payload["pinch_off_threshold_m"] == pytest.approx(0.019 + 0.50 * range_m)
    assert payload["pinch_on_threshold_m"] < payload["pinch_off_threshold_m"]
    assert payload["quality"]["open_repeat_values_m"] == [0.08, 0.09, 0.085]
    assert payload["tracker_valid_sample_fraction"] == pytest.approx(0.5)


def test_pinch_threshold_calibration_fails_when_open_not_greater_than_closed() -> None:
    with pytest.raises(ValueError, match="open_distance_m"):
        build_pinch_threshold_calibration_payload(
            open_repeat_values_m=[0.02, 0.02, 0.02],
            closed_repeat_values_m=[0.03, 0.03, 0.03],
            config=PinchThresholdCalibrationConfig(),
            node_config=_node_config(),
            tracker_valid_sample_fraction=1.0,
        )


def test_pinch_threshold_calibration_fails_when_range_is_too_small() -> None:
    with pytest.raises(ValueError, match="range_m"):
        build_pinch_threshold_calibration_payload(
            open_repeat_values_m=[0.03, 0.031, 0.032],
            closed_repeat_values_m=[0.02, 0.021, 0.022],
            config=PinchThresholdCalibrationConfig(),
            node_config=_node_config(),
            tracker_valid_sample_fraction=1.0,
        )


def test_pinch_threshold_calibration_fails_when_repeat_spread_is_too_large() -> None:
    with pytest.raises(ValueError, match="open repeat spread"):
        build_pinch_threshold_calibration_payload(
            open_repeat_values_m=[0.06, 0.09, 0.12],
            closed_repeat_values_m=[0.02, 0.021, 0.022],
            config=PinchThresholdCalibrationConfig(),
            node_config=_node_config(),
            tracker_valid_sample_fraction=1.0,
        )


def test_collect_pinch_distance_window_reports_insufficient_valid_samples() -> None:
    cursor = _FrameCursor([_live_frame(0, distance=0.02)])

    summary = collect_pinch_distance_window(
        cursor,
        label="open",
        repeat_index=1,
        config=PinchThresholdCalibrationConfig(sample_window_seconds=0.001, min_valid_samples=2),
        adapter_config=DeviceAdapterConfig(pinch_position_mode="nodes_world"),
        adapter=ManusViveExperimentAdapter(
            None,
            config=DeviceAdapterConfig(pinch_position_mode="nodes_world"),
        ),
    )

    assert summary["valid_sample_count"] == 1
    assert summary["errors"]


def test_load_pinch_threshold_json_accepts_minimal_payload(tmp_path: Path) -> None:
    path = tmp_path / "threshold.json"
    path.write_text(
        json.dumps(
            {
                "pinch_on_threshold_m": 0.045,
                "pinch_off_threshold_m": 0.055,
            }
        ),
        encoding="utf-8",
    )

    payload = load_pinch_threshold_json(path)

    assert payload["pinch_threshold"]["grab"] == pytest.approx(0.045)
    assert payload["pinch_threshold"]["release"] == pytest.approx(0.055)


def test_load_pinch_threshold_config_accepts_nested_payload(tmp_path: Path) -> None:
    path = tmp_path / "pinch_config.json"
    path.write_text(
        json.dumps(
            {
                "pinch_threshold_calibration": {
                    "on_fraction": 0.25,
                    "off_fraction": 0.35,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_pinch_threshold_config(path)

    assert config.on_fraction == pytest.approx(0.25)
    assert config.off_fraction == pytest.approx(0.35)


def _node_config() -> dict:
    return build_pinch_node_config_payload(
        thumb_node=4,
        index_node=14,
        tracker_index=0,
        skeleton_index=0,
        pinch_position_mode="nodes_world",
    )


def _live_frame(index: int, *, distance: float) -> LiveRawFrame:
    half = distance / 2.0
    return LiveRawFrame(
        frame_index=index,
        raw_frame={
            "timestamp": index * 1000.0,
            "frame": index,
            "skeletons": [
                {
                    "gloveId": "glove-a",
                    "side": "left",
                    "nodes": [
                        {"id": 4, "position": [-half, 0.0, 0.0]},
                        {"id": 9, "position": [half, 0.0, 0.0]},
                    ],
                }
            ],
            "trackers": [
                {
                    "trackerId": "tracker-a",
                    "position": [0.0, 0.0, 0.0],
                    "rotation": [0.0, 0.0, 0.0, 1.0],
                    "valid": False,
                }
            ],
        },
        receive_time_monotonic=float(index),
        receive_wall_time=float(index),
        byte_length=1,
    )


class _FrameCursor:
    def __init__(self, frames: list[LiveRawFrame]) -> None:
        self.frames = list(frames)

    def get_frame(self, timeout: float | None = None) -> LiveRawFrame | None:
        del timeout
        if not self.frames:
            return None
        return self.frames.pop(0)
