"""Tests for offline timing summary analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from analyze_timing import analyze_timing
from timing_diagnostics import TIMING_CSV_FIELDS


def test_analyze_timing_outputs_median_p95_max_and_phase_stats(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_timing_csv(
        session_dir / "timing_diagnostics.csv",
        [
            _row(
                frame_index=0,
                phase="WAITING_FOR_STREAM",
                raw_to_frame_publish_latency_ms=1.0,
                frame_wait_age_ms=2.0,
            ),
            _row(
                frame_index=1,
                phase="TRIAL_RUNNING",
                raw_to_frame_publish_latency_ms=3.0,
                frame_wait_age_ms=4.0,
                raw_to_trial_update_latency_ms=10.0,
                trial_update_duration_ms=5.0,
                snapshot_publish_to_gui_render_latency_ms=20.0,
            ),
            _row(
                frame_index=2,
                phase="TRIAL_RUNNING",
                raw_to_frame_publish_latency_ms=5.0,
                frame_wait_age_ms=6.0,
                raw_to_trial_update_latency_ms=30.0,
                trial_update_duration_ms=15.0,
                snapshot_publish_to_gui_render_latency_ms=40.0,
                operator_command_to_trial_stop_latency_ms=8.0,
            ),
        ],
    )
    (session_dir / "trial_summary.json").write_text(
        json.dumps({"max_no_new_frame_gap_seconds": 0.25}),
        encoding="utf-8",
    )

    summary = analyze_timing(session_dir)

    assert summary["frame_count"] == 3
    assert summary["median_trial_update_duration_ms"] == pytest.approx(10.0)
    assert summary["p95_trial_update_duration_ms"] == pytest.approx(14.5)
    assert summary["median_raw_to_update_latency_ms"] == pytest.approx(20.0)
    assert summary["median_snapshot_to_gui_latency_ms"] == pytest.approx(30.0)
    assert summary["max_no_frame_gap_ms"] == pytest.approx(250.0)
    assert summary["operator_command_to_stop_latency_ms"] == pytest.approx(8.0)
    assert summary["phase_summaries"]["TRIAL_RUNNING"]["frame_count"] == 2
    assert (session_dir / "timing_analysis_summary.json").exists()


def test_analyze_timing_refuses_overwrite_by_default(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_timing_csv(session_dir / "timing_diagnostics.csv", [_row(frame_index=0)])
    output = session_dir / "timing_analysis_summary.json"
    output.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="overwrite"):
        analyze_timing(session_dir)

    summary = analyze_timing(session_dir, overwrite=True)
    assert summary["frame_count"] == 1


def test_replay_timing_is_marked_as_not_live_latency(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_timing_csv(
        session_dir / "timing_diagnostics.csv",
        [_row(frame_index=0, mode="replay", is_live_latency=False)],
    )

    summary = analyze_timing(session_dir)

    assert summary["mode"] == "replay"
    assert summary["is_live_latency"] is False
    assert any("not a measurement of real live" in warning for warning in summary["warnings"])


def _write_timing_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIMING_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _row(
    *,
    frame_index: int,
    phase: str = "TRIAL_RUNNING",
    mode: str = "live",
    is_live_latency: bool = True,
    **values: object,
) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in TIMING_CSV_FIELDS}
    row.update(
        {
            "sequence_index": frame_index,
            "event_type": "frame",
            "mode": mode,
            "is_live_latency": "true" if is_live_latency else "false",
            "frame_index": frame_index,
            "phase": phase,
            "frame_published": "true",
            "frame_consumed": "true",
            "frame_processed": "true",
            "overwritten_before_consume": "false",
        }
    )
    row.update(values)
    return row
