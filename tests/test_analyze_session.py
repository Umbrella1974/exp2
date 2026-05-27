"""Tests for post-hoc session analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import analyze_session


def test_analyze_session_writes_summary_and_derives_state_edges(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )

    assert (session_dir / "analysis_summary.json").exists()
    assert summary["status"] == "OK"
    assert summary["mode"] == "offline_autocalibrated"
    assert summary["is_formal_calibration"] is False
    assert summary["total_processed_frames"] == 5
    assert summary["total_events"] == 3
    assert summary["contact_enter_count"] == 1
    assert summary["contact_exit_count"] == 1
    assert summary["slip_active_frame_count"] == 2
    assert summary["slip_start_count"] == 1
    assert summary["slip_end_count"] == 1
    assert summary["blocked_frame_count"] == 1
    assert summary["blocked_start_count"] == 1
    assert summary["blocked_end_count"] == 1
    assert summary["haptic_active_frame_count"] == 3
    assert summary["haptic_event_count"] == 2
    assert summary["pinch_distance_min"] == 0.01
    assert summary["pinch_distance_max"] == 0.05
    assert summary["block_displacement_task"]["dx"] == 0.4
    assert summary["time_column_used"] == "sample_time"
    assert summary["time_axis_mode"] == "relative"
    assert summary["time_zero"] == 10.0
    assert summary["time_axis_label"] == "time since session start (s)"
    assert "time column used: sample_time" in summary["warnings"]
    assert "post-hoc auto calibration" in " ".join(summary["warnings"])
    assert summary["derived_event_counts"]["slip_start"] == 1
    assert summary["derived_event_counts"]["blocked_start"] == 1
    assert summary["logical_slip_feedback_frame_count"] == summary["slip_active_frame_count"]
    assert summary["slip_reason_counts"] == {"PINCH_INSUFFICIENT": 2}
    assert summary["logical_slip_due_to_pinch_insufficient_count"] == 2
    assert summary["logical_slip_due_to_track_blocked_count"] == 0
    assert summary["logical_blocked_feedback_frame_count"] == 1
    assert summary["blocked_force_active_count"] == 1
    assert summary["hardware_haptic_active_frame_count"] == summary["haptic_active_frame_count"]
    assert summary["hardware_haptic_event_count"] == summary["haptic_event_count"]
    assert summary["valid_track_box_count"] == 0
    assert summary["skipped_track_box_count"] == 0
    assert summary["track_region_semantics"] == "block_center_feasible_region"
    assert summary["slip_frame_count"] == 2
    assert summary["slip_frames_with_geometry_check"] == 2
    assert summary["slip_frames_pinch_inside_block_count"] == 0
    assert summary["slip_frames_pinch_outside_block_count"] == 2


def test_absolute_time_keeps_selected_time_axis(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
        relative_time=False,
    )

    assert summary["time_axis_mode"] == "absolute"
    assert summary["time_zero"] is None
    assert summary["time_axis_label"] == "sample_time"


def test_time_column_fallback_uses_sample_time_when_requested_column_empty(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path, empty_trial_time=True)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
        time_column="trial_time",
    )

    assert summary["time_column_used"] == "sample_time"
    assert any("trial_time" in warning and "sample_time" in warning for warning in summary["warnings"])


def test_time_column_fallback_can_use_frame_index(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path, empty_all_time=True)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )

    assert summary["time_column_used"] == "frame_index"
    assert summary["time_axis_mode"] == "relative"
    assert summary["time_zero"] == 0.0
    assert any("using frame_index" in warning for warning in summary["warnings"])


def test_matplotlib_unavailable_does_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_dir = _fake_session(tmp_path)

    def fail_import() -> object:
        raise ImportError("no matplotlib")

    monkeypatch.setattr(analyze_session, "_load_pyplot", fail_import)
    summary = analyze_session.analyze_session(session_dir=session_dir, overwrite=True)

    assert summary["generated_plots"] == []
    assert "matplotlib not available; plots were skipped" in summary["warnings"]


def test_event_label_limit_counts_skipped_key_events(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path, many_events=True)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        event_label_limit=2,
        overwrite=True,
    )

    assert summary["skipped_event_label_count"] > 0


def test_missing_haptic_csv_falls_back_to_processed_frames(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path)
    (session_dir / "haptic.csv").unlink()

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )

    assert summary["haptic_active_frame_count"] == 2
    assert any("haptic.csv" in warning and "fallback" in warning for warning in summary["warnings"])


def test_existing_analysis_summary_requires_overwrite(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path)
    (session_dir / "analysis_summary.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        analyze_session.analyze_session(session_dir=session_dir, no_plots=True)


def test_cli_generates_summary_without_plots(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path)

    exit_code = analyze_session.main(
        [
            "--session-dir",
            str(session_dir),
            "--no-plots",
            "--overwrite",
        ]
    )

    assert exit_code == 0
    assert (session_dir / "analysis_summary.json").exists()


def test_plot_generation_if_matplotlib_is_available(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    session_dir = _fake_session(tmp_path)

    summary = analyze_session.analyze_session(session_dir=session_dir, overwrite=True)

    assert summary["generated_plots"]
    assert any(Path(path).name == "trajectory_track_map.png" for path in summary["generated_plots"])
    assert any(
        Path(path).name == "trajectory_track_map_with_block_footprint.png"
        for path in summary["generated_plots"]
    )


def test_footprint_summary_counts_overlays(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path, with_track_boxes=True)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
        max_footprint_overlays=1,
    )

    assert summary["block_footprint_overlay_count"] == 5
    assert summary["slip_footprint_overlay_count"] == 1
    assert summary["blocked_footprint_overlay_count"] == 1
    assert summary["track_region_semantics"] == "block_center_feasible_region"
    assert any("pinch_center_task outside" in warning for warning in summary["warnings"])


def test_missing_block_size_warns_without_failing(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path, missing_block_size=True)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )

    assert summary["status"] == "OK"
    assert summary["block_footprint_overlay_count"] == 0
    assert summary["slip_frames_with_geometry_check"] == 0
    assert any("block_size missing" in warning for warning in summary["warnings"])


def test_analysis_summary_reports_map_track_boxes(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path, with_track_boxes=True)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )

    assert summary["map_id"] == "fake_map"
    assert summary["map_config_version"] == 1
    assert summary["map_source_type"] == "manual"
    assert summary["track_box_count"] == 2
    assert summary["valid_track_box_count"] == 2
    assert summary["skipped_track_box_count"] == 0
    assert summary["target_region_present"] is True
    assert summary["trajectory_map_used_track_boxes"] is True


def test_trajectory_map_uses_track_boxes_when_available(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    session_dir = _fake_session(tmp_path, with_track_boxes=True)

    summary = analyze_session.analyze_session(session_dir=session_dir, overwrite=True)

    assert any(Path(path).name == "trajectory_track_map.png" for path in summary["generated_plots"])
    assert summary["trajectory_map_used_track_boxes"] is True
    assert not any("track_boxes missing or unusable" in warning for warning in summary["warnings"])


def test_trajectory_map_falls_back_without_track_boxes(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    session_dir = _fake_session(tmp_path)

    summary = analyze_session.analyze_session(session_dir=session_dir, overwrite=True)

    assert any(Path(path).name == "trajectory_track_map.png" for path in summary["generated_plots"])
    assert summary["trajectory_map_used_track_boxes"] is False
    assert any("track_boxes missing or unusable" in warning for warning in summary["warnings"])


def test_bad_track_box_warns_without_failing(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    session_dir = _fake_session(tmp_path, with_track_boxes=True, bad_track_box=True)

    summary = analyze_session.analyze_session(session_dir=session_dir, overwrite=True)

    assert summary["status"] == "OK"
    assert summary["track_box_count"] == 3
    assert summary["valid_track_box_count"] == 2
    assert summary["skipped_track_box_count"] == 1
    assert summary["trajectory_map_used_track_boxes"] is True
    assert any("track_boxes[2] could not be parsed" in warning for warning in summary["warnings"])


def test_bad_track_box_stats_are_available_without_plots(tmp_path: Path) -> None:
    session_dir = _fake_session(tmp_path, with_track_boxes=True, bad_track_box=True)

    summary = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )

    assert summary["status"] == "OK"
    assert summary["track_box_count"] == 3
    assert summary["valid_track_box_count"] == 2
    assert summary["skipped_track_box_count"] == 1
    assert summary["trajectory_map_used_track_boxes"] is True
    assert any("track_boxes[2] could not be parsed" in warning for warning in summary["warnings"])


def _fake_session(
    tmp_path: Path,
    *,
    empty_trial_time: bool = False,
    empty_all_time: bool = False,
    many_events: bool = False,
    with_track_boxes: bool = False,
    bad_track_box: bool = False,
    missing_block_size: bool = False,
) -> Path:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_json(
        session_dir / "session_meta.json",
        {
            "session_id": "s1",
            "mode": "offline_autocalibrated",
            "is_formal_calibration": False,
            "is_formal_scene": False,
            "warnings": [
                "This session was generated from post-hoc auto calibration and must not be treated as a formal experimental trial."
            ],
        },
    )
    _write_json(
        session_dir / "calibration.json",
        {
            "calibration_type": "post_hoc_auto",
            "is_formal_calibration": False,
        },
    )
    _write_json(
        session_dir / "trial_config.json",
        _trial_config(with_track_boxes, bad_track_box, missing_block_size),
    )
    _write_json(session_dir / "trial_summary.json", {"warnings": []})
    rows = _processed_rows(empty_trial_time=empty_trial_time, empty_all_time=empty_all_time)
    _write_csv(session_dir / "processed_frames.csv", rows)
    _write_csv(session_dir / "events.csv", _event_rows(many_events=many_events))
    _write_csv(session_dir / "haptic.csv", _haptic_rows())
    return session_dir


def _processed_rows(*, empty_trial_time: bool, empty_all_time: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(5):
        trial_time = "" if empty_trial_time else f"{index * 0.1:.1f}"
        sample_time = "" if empty_all_time else f"{10.0 + index * 0.1:.1f}"
        raw_timestamp = "" if empty_all_time else str(1000 + index)
        if empty_all_time:
            trial_time = ""
        rows.append(
            {
                "frame_index": str(index),
                "sample_time": sample_time,
                "trial_time": trial_time,
                "raw_timestamp": raw_timestamp,
                "tracker_valid": "False" if index == 0 else "True",
                "pinch_valid": "True",
                "pinch_distance": f"{0.01 + index * 0.01:.2f}",
                "pinch_center_task_x": f"{index * 0.1:.1f}",
                "pinch_center_task_y": f"{index * 0.2:.1f}",
                "pinch_center_task_z": "0.0",
                "block_center_task_x": f"{index * 0.1:.1f}",
                "block_center_task_y": "0.0",
                "block_center_task_z": "0.0",
                "contact_state": "INSIDE_BLOCK" if index in (1, 2, 3) else "OUTSIDE_BLOCK",
                "block_motion_state": "GRABBED_BLOCKED" if index == 3 else "GRABBED_MOVING",
                "stop_reason": "TRACK_BLOCKED" if index == 3 else "NONE",
                "slip_active": "True" if index in (1, 2) else "False",
                "slip_reason": "PINCH_INSUFFICIENT" if index in (1, 2) else "",
                "blocked_force_active": "True" if index == 3 else "False",
                "large_delta": "True" if index == 4 else "False",
                "haptic_state": "ON" if index in (1, 2) else "",
                "haptic_reason": "",
            }
        )
    return rows


def _event_rows(*, many_events: bool) -> list[dict[str, str]]:
    rows = [
        {"event_index": "0", "frame_index": "1", "time": "10.1", "event_type": "contact_enter", "details_json": "{}"},
        {"event_index": "1", "frame_index": "3", "time": "10.3", "event_type": "contact_exit", "details_json": "{}"},
        {"event_index": "2", "frame_index": "4", "time": "10.4", "event_type": "subject_end", "details_json": "{}"},
    ]
    if many_events:
        for index in range(20):
            rows.append(
                {
                    "event_index": str(3 + index),
                    "frame_index": str(index % 5),
                    "time": f"{10.0 + (index % 5) * 0.1:.1f}",
                    "event_type": "contact_enter" if index % 2 == 0 else "contact_exit",
                    "details_json": "{}",
                }
            )
    return rows


def _haptic_rows() -> list[dict[str, str]]:
    return [
        {"frame_index": "0", "time": "10.0", "haptic_state": "OFF", "command_type": "", "slip_active": "False", "blocked_force_active": "False"},
        {"frame_index": "1", "time": "10.1", "haptic_state": "ON", "command_type": "", "slip_active": "True", "blocked_force_active": "False"},
        {"frame_index": "2", "time": "10.2", "haptic_state": "ON", "command_type": "", "slip_active": "True", "blocked_force_active": "False"},
        {"frame_index": "3", "time": "10.3", "haptic_state": "OFF", "command_type": "", "slip_active": "False", "blocked_force_active": "True"},
        {"frame_index": "4", "time": "10.4", "haptic_state": "", "command_type": "pulse", "slip_active": "False", "blocked_force_active": "False"},
    ]


def _trial_config(with_track_boxes: bool, bad_track_box: bool, missing_block_size: bool) -> dict:
    config = {
        "scene_type": "post_hoc_auto",
        "is_formal_scene": False,
        "block_size": [0.2, 0.2, 0.2],
        "pinch_threshold": {"grab": 0.025, "release": 0.035},
        "scene_auto": {
            "track_bounds": {
                "min": [-1.0, -1.0, -1.0],
                "max": [1.0, 1.0, 1.0],
            }
        },
    }
    if missing_block_size:
        config.pop("block_size")
    if not with_track_boxes:
        return config

    config.update(
        {
            "map_config_version": 1,
            "map_id": "fake_map",
            "map_source_type": "manual",
            "block_initial_center_task": [0.0, 0.0, 0.0],
            "track_boxes": [
                {
                    "id": "segment_00",
                    "order": 0,
                    "label": "Segment 1",
                    "min": [0.0, -0.2, -0.1],
                    "max": [0.5, 0.2, 0.1],
                    "metadata": {"direction": "x+"},
                },
                {
                    "id": "segment_01",
                    "order": 1,
                    "label": "Segment 2",
                    "min": [0.4, -0.2, -0.1],
                    "max": [0.8, 0.2, 0.1],
                    "metadata": {"direction": "x+"},
                },
            ],
            "target_region": {
                "id": "target",
                "label": "Target region",
                "min": [0.7, -0.2, -0.1],
                "max": [0.8, 0.2, 0.1],
                "metadata": {"type": "target_region"},
            },
        }
    )
    if bad_track_box:
        config["track_boxes"].append({"id": "bad_box", "min": [0.0], "max": [1.0]})
    return config


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
