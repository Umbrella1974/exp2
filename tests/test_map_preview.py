"""Tests for direct MapConfig preview rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import map_preview


def test_map_preview_renders_example_map(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    out = tmp_path / "preview.png"

    summary = map_preview.render_map_preview(
        map_config_path=Path("maps/examples/xoy_turn.json"),
        out_path=out,
    )

    assert out.exists()
    assert summary["map_id"] == "xoy_turn"
    assert summary["track_box_count"] == 2
    assert summary["target_region_present"] is True
    assert summary["validation_errors"] == []


def test_map_preview_validation_error_fails_clearly(tmp_path: Path) -> None:
    bad_map = tmp_path / "bad_map.json"
    bad_map.write_text(
        json.dumps(
            {
                "map_id": "bad",
                "coordinate_space": "task",
                "unit": "m",
                "block_initial_center_task": [0.0, 0.0, 0.0],
                "block_size": [0.2, 0.2, 0.2],
                "track_boxes": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="map validation failed"):
        map_preview.render_map_preview(
            map_config_path=bad_map,
            out_path=tmp_path / "preview.png",
        )


def test_map_preview_summary_out_is_optional(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    summary_out = tmp_path / "preview_summary.json"

    map_preview.render_map_preview(
        map_config_path=Path("maps/examples/xoy_straight.json"),
        out_path=tmp_path / "preview.png",
        show_target_region=True,
        show_configured_block=True,
        summary_out=summary_out,
    )

    summary = json.loads(summary_out.read_text(encoding="utf-8"))
    assert summary["map_id"] == "xoy_straight"
    assert summary["target_region_present"] is True
    assert summary["validation_errors"] == []


def test_map_preview_cli_generates_output(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    out = tmp_path / "preview.png"
    summary_out = tmp_path / "summary.json"

    exit_code = map_preview.main(
        [
            "--map-config",
            "maps/examples/xoy_straight.json",
            "--out",
            str(out),
            "--summary-out",
            str(summary_out),
            "--no-show-box-labels",
        ]
    )

    assert exit_code == 0
    assert out.exists()
    assert summary_out.exists()
