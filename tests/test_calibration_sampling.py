"""Tests for calibration sampling helpers."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from calibration_sampling import (
    build_calibration_line_record,
    extract_calibration_point_from_sample,
    summarize_static_points,
)


def test_summarize_static_points_mean_std_and_max_deviation() -> None:
    summary = summarize_static_points([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    assert summary["mean_world"] == [1.0, 0.0, 0.0]
    assert summary["std_world"] == [1.0, 0.0, 0.0]
    assert summary["max_deviation_m"] == pytest.approx(1.0)
    assert summary["sample_count"] == 2


def test_build_calibration_line_record_generates_fit() -> None:
    record = build_calibration_line_record(
        "long_axis_line",
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        source="tracker_position_world",
        time_start=0.0,
        time_end=5.0,
    )
    assert np.allclose(record.direction_world, [1.0, 0.0, 0.0])
    assert record.line_length_m == pytest.approx(1.0)
    assert record.line_fit_rmse_m == pytest.approx(0.0)
    assert record.duration_seconds == pytest.approx(5.0)


def test_extract_calibration_point_from_tracker_position() -> None:
    device_frame = SimpleNamespace(
        tracker=SimpleNamespace(
            valid=True,
            pose_world=SimpleNamespace(position=np.asarray([1.0, 2.0, 3.0])),
        )
    )
    sample = SimpleNamespace(pinch_center_world=None)
    point = extract_calibration_point_from_sample(
        sample,
        device_frame,
        source="tracker_position_world",
    )
    assert point == [1.0, 2.0, 3.0]


def test_extract_calibration_point_from_missing_pinch_returns_none() -> None:
    point = extract_calibration_point_from_sample(
        SimpleNamespace(pinch_center_world=None),
        None,
        source="pinch_center_world",
    )
    assert point is None


def test_extract_calibration_point_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="source"):
        extract_calibration_point_from_sample(
            SimpleNamespace(pinch_center_world=None),
            None,
            source="bad_source",
        )
