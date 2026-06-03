"""Tests for thread-safe timing diagnostics records."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from timing_diagnostics import TimingDiagnostics


@dataclass(frozen=True)
class _Frame:
    frame_index: int
    receive_time_monotonic: float | None
    raw_frame: dict[str, Any]


def test_timing_record_calculates_only_same_clock_domain_latencies() -> None:
    diagnostics = TimingDiagnostics(mode="live", is_live_latency=True)
    frame = _Frame(
        frame_index=7,
        receive_time_monotonic=10.0,
        raw_frame={
            "combined_monotonic_ms": 7000.0,
            "skeleton_receive_monotonic_ms": 7010.0,
            "tracker_receive_monotonic_ms": 7035.0,
        },
    )

    diagnostics.record_frame_published(frame, phase="TRIAL_RUNNING", monotonic_time=10.1)
    diagnostics.record_frame_consumed(frame, phase="TRIAL_RUNNING", monotonic_time=10.2)
    diagnostics.record_parse(7, start_monotonic=10.21, end_monotonic=10.22)
    diagnostics.record_adapter(7, start_monotonic=10.23, end_monotonic=10.25)
    diagnostics.record_trial_update(7, start_monotonic=10.3, end_monotonic=10.34)
    diagnostics.record_snapshot_created(7, monotonic_time=10.36)
    diagnostics.record_snapshot_published(7, monotonic_time=10.4)
    diagnostics.record_gui_render(frame, monotonic_time=10.5)
    diagnostics.record_frame_processed(7)

    row = diagnostics.rows_snapshot()[0]
    assert row["skeleton_tracker_sync_delta_ms"] == pytest.approx(25.0)
    assert row["raw_to_frame_publish_latency_ms"] == pytest.approx(100.0)
    assert row["frame_wait_age_ms"] == pytest.approx(100.0)
    assert row["frame_to_trial_update_latency_ms"] == pytest.approx(200.0)
    assert row["parse_duration_ms"] == pytest.approx(10.0)
    assert row["adapter_duration_ms"] == pytest.approx(20.0)
    assert row["trial_update_duration_ms"] == pytest.approx(40.0)
    assert row["trial_update_to_snapshot_latency_ms"] == pytest.approx(20.0)
    assert row["snapshot_publish_to_gui_render_latency_ms"] == pytest.approx(100.0)


def test_missing_fields_and_repeated_gui_render_are_safe() -> None:
    diagnostics = TimingDiagnostics(mode="live", is_live_latency=True)
    frame = _Frame(frame_index=1, receive_time_monotonic=None, raw_frame={})

    diagnostics.record_frame_published(frame, phase="WAITING_FOR_STREAM", monotonic_time=1.0)
    diagnostics.record_gui_render(frame, monotonic_time=2.0)
    diagnostics.record_gui_render(frame, monotonic_time=3.0)

    row = diagnostics.rows_snapshot()[0]
    assert row["raw_to_frame_publish_latency_ms"] is None
    assert row["trial_update_duration_ms"] is None
    assert row["gui_render_monotonic_ms"] == pytest.approx(2000.0)
    assert row["snapshot_publish_to_gui_render_latency_ms"] is None


def test_overwritten_frame_and_session_level_operator_command_are_recorded() -> None:
    diagnostics = TimingDiagnostics(mode="live", is_live_latency=True)
    first = _Frame(frame_index=1, receive_time_monotonic=1.0, raw_frame={})
    second = _Frame(frame_index=2, receive_time_monotonic=2.0, raw_frame={})

    diagnostics.record_frame_published(first, phase="CALIBRATING_ORIGIN", monotonic_time=1.1)
    diagnostics.record_frame_published(
        second,
        phase="CALIBRATING_ORIGIN",
        monotonic_time=2.1,
        overwritten_frame=first,
    )
    diagnostics.record_operator_command(
        "q",
        frame_index=None,
        phase="TRIAL_RUNNING",
        monotonic_time=3.0,
    )
    diagnostics.record_trial_end(frame_index=None, phase="TRIAL_RUNNING", monotonic_time=3.01)

    rows = diagnostics.rows_snapshot()
    assert rows[0]["overwritten_before_consume"] is True
    assert rows[2]["event_type"] == "operator_command"
    assert rows[2]["frame_index"] is None
    assert rows[2]["operator_command_to_trial_stop_latency_ms"] == pytest.approx(10.0)


def test_timing_csv_write_preserves_replay_marker(tmp_path: Path) -> None:
    diagnostics = TimingDiagnostics(mode="replay", is_live_latency=False)
    frame = _Frame(frame_index=0, receive_time_monotonic=1.0, raw_frame={})
    diagnostics.record_frame_published(frame, phase="REPLAY", monotonic_time=1.1)
    diagnostics.record_frame_consumed(frame, phase="REPLAY", monotonic_time=1.2)
    diagnostics.record_frame_processed(0)

    path = diagnostics.write_csv(tmp_path / "timing_diagnostics.csv")
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["mode"] == "replay"
    assert rows[0]["is_live_latency"] == "false"
    assert diagnostics.summary()["published_frame_count"] == 1
    assert diagnostics.summary()["consumed_frame_count"] == 1
    assert diagnostics.summary()["processed_frame_count"] == 1
