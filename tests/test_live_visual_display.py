"""Tests for best-effort live visual display helpers."""

from __future__ import annotations

import builtins
from types import SimpleNamespace

from dashboard_snapshot import DashboardSnapshot
from live_visual_display import (
    TextLiveVisualDisplay,
    build_status_text,
    create_live_visual_display,
)


def test_text_mode_outputs_english_labels(capsys) -> None:
    display = TextLiveVisualDisplay(print_every=1)
    display.update(_snapshot())

    output = capsys.readouterr().out
    assert "MAIN=MOVING" in output
    assert "CONTACT=CONTACT" in output
    assert "PINCH=PINCH_VALID" in output
    assert "FEEDBACK=NONE" in output


def test_status_text_contains_required_sections() -> None:
    text = build_status_text(_snapshot())

    assert "MAIN STATE:" in text
    assert "CONTACT:" in text
    assert "PINCH:" in text
    assert "MOTION:" in text
    assert "STOP:" in text
    assert "FEEDBACK:" in text


def test_matplotlib_unavailable_falls_back_to_text(monkeypatch) -> None:
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("no matplotlib")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    display = create_live_visual_display(
        show_visual=True,
        visual_mode="matplotlib",
        map_config=SimpleNamespace(track_boxes=[], target_region=None),
        print_every=1,
    )

    assert getattr(display, "mode", "") == "text"
    display.update(_snapshot())


def _snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        frame_index=0,
        time=1.0,
        tracker_valid=True,
        hand_valid=True,
        pinch_valid=True,
        pinch_distance=0.02,
        pinch_center_task=[0.0, 0.0, 0.0],
        block_center_task=[0.0, 0.0, 0.0],
        block_size=[0.2, 0.2, 0.2],
        contact_state="INSIDE_BLOCK",
        block_motion_state="GRABBED_MOVING",
        stop_reason="NONE",
        track_state="INSIDE_TRACK",
        pinch_state="PINCH_VALID",
        detach_state="NONE",
        large_delta=False,
        slip_active=False,
        slip_reason="",
        blocked_force_active=False,
        logical_haptic_active=False,
        logical_haptic_label="NONE",
        hardware_haptic_active=False,
        map_id="map",
        calibration_id="cal",
        processing_latency_ms=1.0,
        contact_label="CONTACT (INSIDE_BLOCK)",
        release_label="NO RELEASE",
        interaction_label="GRABBED / MOVING (GRABBED_MOVING)",
        feedback_label="FEEDBACK: NONE",
        status_line="MAIN=MOVING",
        main_state_label="MOVING",
        pinch_label="PINCH VALID, distance=0.020 m",
    )
