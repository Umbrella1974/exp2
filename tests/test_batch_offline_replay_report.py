"""Tests for the batch offline replay report wrapper."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from batch_offline_replay_report import BatchCase, run_batch


def fake_replay_runner(case: BatchCase, output_dir: Path, python_executable: str) -> None:
    """Write deterministic fake replay outputs for runner behavior tests."""

    del python_executable
    if case.case_id == "error_case":
        raise RuntimeError("fake replay failed")

    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "total_raw_frames": 3,
        "replayed_raw_frames": 3,
        "valid_input_frames": 3,
        "valid_pinch_frames": 3,
        "tracker_fallback_frame_count": 0,
        "invalid_input_frame_count": 0,
        "generated_contact_enter_count": 1,
        "generated_contact_exit_count": 1,
        "slip_active_frame_count": 0,
        "blocked_frame_count": 0,
        "large_delta_frame_count": 0,
        "tracker_invalid_frame_count": 0,
        "pinch_distance_min": 0.01,
        "pinch_distance_mean": 0.02,
        "pinch_distance_max": 0.03,
        "scene_mode": case.args.get("scene_mode", "wide-track"),
        "calibration_mode": case.args.get("calibration_mode", "initial-window"),
        "calibration_frames": case.args.get("calibration_frames", 100),
        "warnings": [],
    }
    if case.case_id == "blocked_case":
        summary.pop("blocked_frame_count")

    _write_json(output_dir / "summary.json", summary)
    motion_rows = [
        {"block_motion_state": "FREE_VISIBLE", "contact_state": "OUTSIDE_BLOCK", "stop_reason": "NONE"},
        {"block_motion_state": "GRABBED_MOVING", "contact_state": "INSIDE_BLOCK", "stop_reason": "NONE"},
        {"block_motion_state": "GRABBED_MOVING", "contact_state": "INSIDE_BLOCK", "stop_reason": "NONE"},
    ]
    if case.case_id == "blocked_case":
        motion_rows[1]["block_motion_state"] = "GRABBED_BLOCKED"
        motion_rows[1]["stop_reason"] = "TRACK_BLOCKED"
    _write_frames(output_dir / "frames.csv", motion_rows)


def test_run_batch_writes_csv_json_and_statuses(tmp_path: Path) -> None:
    cases_path = _write_cases(
        tmp_path,
        [
            {
                "id": "pass_case",
                "raw_jsonl": "fake-pass.jsonl",
                "args": {"scene_mode": "wide-track"},
                "expectations": [
                    {"metric": "moving_frame_count", "op": ">=", "value": 1},
                    {"metric": "large_delta_frame_count", "op": "==", "value": 0},
                ],
            },
            {
                "id": "fail_case",
                "raw_jsonl": "fake-fail.jsonl",
                "expectations": [
                    {"metric": "large_delta_frame_count", "op": ">", "value": 999},
                ],
            },
            {
                "id": "error_case",
                "raw_jsonl": "missing.jsonl",
                "expectations": [
                    {"metric": "large_delta_frame_count", "op": "==", "value": 0},
                ],
            },
        ],
    )

    out_dir = tmp_path / "batch"
    result = run_batch(cases_path=cases_path, out_dir=out_dir, runner=fake_replay_runner)

    assert result.exit_code == 0
    assert (out_dir / "batch_summary.csv").exists()
    assert (out_dir / "batch_summary.json").exists()

    payload = json.loads((out_dir / "batch_summary.json").read_text(encoding="utf-8"))
    statuses = {case["case_id"]: case["status"] for case in payload["cases"]}
    assert statuses == {
        "pass_case": "PASS",
        "fail_case": "FAIL",
        "error_case": "ERROR",
    }
    assert payload["passed_cases"] == 1
    assert payload["failed_cases"] == 1
    assert payload["error_cases"] == 1

    pass_case = _case(payload, "pass_case")
    assert pass_case["summary"]["moving_frame_count"] == 2

    with (out_dir / "batch_summary.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["case_id"] for row in rows] == ["pass_case", "fail_case", "error_case"]
    assert rows[0]["moving_frame_count"] == "2"
    assert rows[2]["error_message"] == "fake replay failed"


def test_missing_metric_expectation_fails_without_interrupting_batch(tmp_path: Path) -> None:
    cases_path = _write_cases(
        tmp_path,
        [
            {
                "id": "missing_metric_case",
                "raw_jsonl": "fake.jsonl",
                "expectations": [
                    {"metric": "metric_that_does_not_exist", "op": "==", "value": 1},
                ],
            }
        ],
    )

    result = run_batch(
        cases_path=cases_path,
        out_dir=tmp_path / "batch",
        runner=fake_replay_runner,
    )

    case = result.payload["cases"][0]
    expectation = case["expectation_results"][0]
    assert case["status"] == "FAIL"
    assert expectation["passed"] is False
    assert expectation["missing_metric"] is True
    assert "missing metric" in case["warnings"][0]


def test_stop_on_fail_stops_early_and_sets_nonzero_exit_code(tmp_path: Path) -> None:
    cases_path = _write_cases(
        tmp_path,
        [
            {
                "id": "pass_case",
                "raw_jsonl": "fake-pass.jsonl",
                "expectations": [{"metric": "large_delta_frame_count", "op": "==", "value": 0}],
            },
            {
                "id": "fail_case",
                "raw_jsonl": "fake-fail.jsonl",
                "expectations": [{"metric": "large_delta_frame_count", "op": ">", "value": 999}],
            },
            {
                "id": "after_fail_case",
                "raw_jsonl": "fake-after.jsonl",
                "expectations": [{"metric": "large_delta_frame_count", "op": "==", "value": 0}],
            },
        ],
    )

    out_dir = tmp_path / "batch"
    result = run_batch(
        cases_path=cases_path,
        out_dir=out_dir,
        stop_on_fail=True,
        runner=fake_replay_runner,
    )

    assert result.exit_code == 1
    assert [case["case_id"] for case in result.payload["cases"]] == ["pass_case", "fail_case"]
    assert not (out_dir / "after_fail_case").exists()


def test_overwrite_clears_only_current_case_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "batch"
    case_dir = out_dir / "pass_case"
    other_dir = out_dir / "other_case"
    case_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    (case_dir / "old.txt").write_text("old", encoding="utf-8")
    (other_dir / "keep.txt").write_text("keep", encoding="utf-8")
    cases_path = _write_cases(
        tmp_path,
        [
            {
                "id": "pass_case",
                "raw_jsonl": "fake-pass.jsonl",
                "expectations": [{"metric": "large_delta_frame_count", "op": "==", "value": 0}],
            }
        ],
    )

    run_batch(
        cases_path=cases_path,
        out_dir=out_dir,
        overwrite=True,
        runner=fake_replay_runner,
    )

    assert not (case_dir / "old.txt").exists()
    assert (other_dir / "keep.txt").exists()


def test_case_id_must_be_safe_filename(tmp_path: Path) -> None:
    cases_path = _write_cases(
        tmp_path,
        [
            {
                "id": "../bad",
                "raw_jsonl": "fake.jsonl",
            }
        ],
    )

    with pytest.raises(ValueError, match="case id"):
        run_batch(cases_path=cases_path, out_dir=tmp_path / "batch", runner=fake_replay_runner)


def test_default_subprocess_runner_light_integration(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw_frames.jsonl"
    _write_jsonl(
        raw_path,
        [
            _raw_frame(0.0, 0.0),
            _raw_frame(100.0, 0.1),
            _raw_frame(200.0, 0.2),
        ],
    )
    cases_path = _write_cases(
        tmp_path,
        [
            {
                "id": "integration_case",
                "raw_jsonl": str(raw_path),
                "args": {
                    "max_frames": 3,
                    "calibration_frames": 2,
                    "scene_mode": "wide-track",
                    "block_size": 1.0,
                },
                "expectations": [
                    {"metric": "total_raw_frames", "op": "==", "value": 3},
                    {"metric": "large_delta_frame_count", "op": "==", "value": 0},
                ],
            }
        ],
    )

    result = run_batch(
        cases_path=cases_path,
        out_dir=tmp_path / "batch",
        overwrite=True,
        python_executable=sys.executable,
    )

    assert result.exit_code == 0
    assert result.payload["cases"][0]["status"] == "PASS"
    assert (tmp_path / "batch" / "integration_case" / "summary.json").exists()


def _write_cases(tmp_path: Path, cases: list[dict]) -> Path:
    cases_path = tmp_path / "cases.json"
    _write_json(cases_path, {"cases": cases})
    return cases_path


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_frames(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _raw_frame(timestamp: float, pinch_x: float) -> dict:
    return {
        "timestamp": timestamp,
        "frame": int(timestamp),
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
                "valid": True,
            }
        ],
    }


def _case(payload: dict, case_id: str) -> dict:
    for case in payload["cases"]:
        if case["case_id"] == case_id:
            return case
    raise AssertionError(f"case not found: {case_id}")
