"""Replay Debug GUI entrypoint.

This runner owns replay input handling and feeds DashboardSnapshot to the GUI.
The GUI itself only polls LatestSnapshotStore.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from cue_config import load_cue_config
from cue_feedback import CUE_SINK_CHOICES, CueRuntime, CueSinkConfig
from debug_gui import (
    GuiDependencyError,
    INSTALL_GUI_DEPS_MESSAGE,
    preflight_gui_dependencies,
    run_debug_gui,
)
from latest_snapshot_store import LatestSnapshotStore
from replay_debug_runner import (
    ReplayDebugConfig,
    finalize_replay_debug_outputs,
    load_replay_debug_inputs,
    run_replay_debug,
)
from timing_diagnostics import TimingDiagnostics
from visual_profile import DISPLAY_CONTROL_CHOICES, VISUAL_PROFILE_CHOICES, resolve_visual_profile


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = _config_from_args(args)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    try:
        inputs = load_replay_debug_inputs(config)
    except Exception as exc:
        print(f"Replay input error: {exc}", file=sys.stderr)
        return 2
    if args.headless and config.cue_sink == "gui_text":
        print("Config error: --cue-sink gui_text cannot be used with --headless.", file=sys.stderr)
        return 2

    store = LatestSnapshotStore()
    timing_diagnostics = TimingDiagnostics(mode="replay", is_live_latency=False)
    visual_settings = resolve_visual_profile(
        config.visual_profile,
        status_panel=config.status_panel,
        show_axes=config.show_axes,
        show_grid=config.show_grid,
    )
    cue_runtime = CueRuntime(
        trial_id=str(inputs.trial_config.get("trial_id", inputs.session_meta.get("trial_id", "replay_debug"))),
        cue_config=inputs.cue_config,
        sink_config=CueSinkConfig(
            cue_sink=config.cue_sink,
            mode="replay",
            is_live_cue_timing=False,
        ),
    )
    if args.headless:
        try:
            result = run_replay_debug(
                config,
                snapshot_store=store,
                timing_diagnostics=timing_diagnostics,
                cue_runtime=cue_runtime,
            )
        except Exception as exc:
            cue_runtime.end_session()
            print(f"Replay failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    try:
        preflight_gui_dependencies()
    except GuiDependencyError:
        cue_runtime.end_session()
        print(INSTALL_GUI_DEPS_MESSAGE, file=sys.stderr)
        return 1

    result_holder: dict[str, Any] = {}
    stop_event = threading.Event()

    def replay_worker() -> None:
        try:
            result_holder["result"] = run_replay_debug(
                config,
                snapshot_store=store,
                timing_diagnostics=timing_diagnostics,
                cue_runtime=cue_runtime,
                stop_event=stop_event,
            )
        except Exception as exc:
            result_holder["error"] = exc
            stop_event.set()

    def handle_replay_gui_closed() -> None:
        cue_runtime.handle_gui_closed()
        stop_event.set()

    thread = threading.Thread(target=replay_worker, name="ReplayDebugRunner", daemon=False)
    thread.start()
    previous_sigint_handler: Any | None = None
    sigint_handler_installed = False
    try:
        if threading.current_thread() is threading.main_thread():
            previous_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
            sigint_handler_installed = True
        exit_code = run_debug_gui(
            snapshot_store=store,
            scene=inputs.scene,
            mode="replay",
            gui_fps=config.gui_fps,
            title="Exp2 Replay Debug GUI",
            log_path=(Path(args.out_dir) / "gui_diagnostics.csv") if args.out_dir else None,
            runtime_stats_getter=lambda: {
                "mode": "replay",
                "total_received_frames": store.stats_snapshot().update_count,
                "overwritten_snapshot_count": store.stats_snapshot().overwritten_snapshot_count,
            },
            render_callback=timing_diagnostics.record_gui_render,
            cue_store=cue_runtime.gui_cue_store,
            close_callback=handle_replay_gui_closed,
            close_when=stop_event.is_set,
            visual_profile=visual_settings.visual_profile,
            status_panel=visual_settings.status_panel,
            show_axes=visual_settings.show_axes,
            show_grid=visual_settings.show_grid,
        )
    except GuiDependencyError:
        stop_event.set()
        cue_runtime.end_session()
        print(INSTALL_GUI_DEPS_MESSAGE, file=sys.stderr)
        thread.join(timeout=1.0)
        return 1
    except KeyboardInterrupt:
        stop_event.set()
        store.mark_gui_closed()
        thread.join()
        return 130
    except Exception as exc:
        stop_event.set()
        store.mark_gui_closed()
        thread.join()
        cue_runtime.end_session()
        print(f"GUI failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if sigint_handler_installed:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    thread.join()
    if "error" in result_holder:
        cue_runtime.end_session()
        print(f"Replay failed: {result_holder['error']}", file=sys.stderr)
        return 1
    result = result_holder.get("result")
    if result is not None:
        if config.out_dir is not None:
            timing_diagnostics.write_csv(config.out_dir / "timing_diagnostics.csv")
            finalize_replay_debug_outputs(result, config.out_dir)
        print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
    return int(exit_code)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay raw/session data into the debug GUI.")
    parser.add_argument("--session-dir", default=None)
    parser.add_argument("--raw-jsonl", default=None)
    parser.add_argument("--calibration-json", default=None)
    parser.add_argument("--trial-config-json", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--max-frames", default=None, type=int)
    parser.add_argument("--replay-timing", choices=("raw", "fixed", "fast"), default="raw")
    parser.add_argument("--replay-fps", default=60.0, type=float)
    parser.add_argument("--replay-speed", default=1.0, type=float)
    parser.add_argument("--gui-fps", default=30.0, type=float)
    parser.add_argument("--timestamp-scale", default=0.001, type=float)
    parser.add_argument("--thumb-node", default=4, type=int)
    parser.add_argument("--index-node", default=9, type=int)
    parser.add_argument("--tracker-index", default=0, type=int)
    parser.add_argument("--skeleton-index", default=0, type=int)
    display_group = parser.add_mutually_exclusive_group()
    display_group.add_argument(
        "--gui",
        action="store_true",
        help="Open the GUI. This is already the default and is accepted for live CLI symmetry.",
    )
    display_group.add_argument("--headless", action="store_true", help="Run replay without opening the GUI.")
    parser.add_argument("--cue-sink", choices=CUE_SINK_CHOICES, default="logging")
    parser.add_argument("--cue-config", default=None, help="JSON/YAML cue generation config.")
    parser.add_argument("--visual-profile", choices=VISUAL_PROFILE_CHOICES, default="debug_all")
    parser.add_argument("--status-panel", choices=DISPLAY_CONTROL_CHOICES, default="auto")
    parser.add_argument("--show-axes", choices=DISPLAY_CONTROL_CHOICES, default="auto")
    parser.add_argument("--show-grid", choices=DISPLAY_CONTROL_CHOICES, default="auto")
    args = parser.parse_args(argv)
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be > 0.")
    if args.replay_fps <= 0.0:
        parser.error("--replay-fps must be > 0.")
    if args.replay_speed <= 0.0:
        parser.error("--replay-speed must be > 0.")
    if args.gui_fps <= 0.0:
        parser.error("--gui-fps must be > 0.")
    if args.timestamp_scale <= 0.0:
        parser.error("--timestamp-scale must be > 0.")
    return args


def _config_from_args(args: argparse.Namespace) -> ReplayDebugConfig:
    cue_config_path = Path(args.cue_config) if args.cue_config is not None else None
    cue_config = load_cue_config(cue_config_path) if cue_config_path is not None else None
    return ReplayDebugConfig(
        session_dir=Path(args.session_dir) if args.session_dir is not None else None,
        raw_jsonl=Path(args.raw_jsonl) if args.raw_jsonl is not None else None,
        calibration_json=Path(args.calibration_json) if args.calibration_json is not None else None,
        trial_config_json=Path(args.trial_config_json) if args.trial_config_json is not None else None,
        out_dir=Path(args.out_dir) if args.out_dir is not None else None,
        max_frames=args.max_frames,
        replay_timing=args.replay_timing,
        replay_fps=args.replay_fps,
        replay_speed=args.replay_speed,
        gui_fps=args.gui_fps,
        timestamp_scale=args.timestamp_scale,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        cue_sink=args.cue_sink,
        cue_config_path=cue_config_path,
        cue_config=cue_config,
        visual_profile=args.visual_profile,
        status_panel=args.status_panel,
        show_axes=args.show_axes,
        show_grid=args.show_grid,
    )


if __name__ == "__main__":
    raise SystemExit(main())
