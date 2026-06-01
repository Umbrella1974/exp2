"""Replay Debug GUI entrypoint.

This runner owns replay input handling and feeds DashboardSnapshot to the GUI.
The GUI itself only polls LatestSnapshotStore.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from typing import Any

from debug_gui import (
    GuiDependencyError,
    INSTALL_GUI_DEPS_MESSAGE,
    preflight_gui_dependencies,
    run_debug_gui,
)
from latest_snapshot_store import LatestSnapshotStore
from replay_debug_runner import ReplayDebugConfig, load_replay_debug_inputs, run_replay_debug


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = _config_from_args(args)
    try:
        inputs = load_replay_debug_inputs(config)
    except Exception as exc:
        print(f"Replay input error: {exc}", file=sys.stderr)
        return 2

    store = LatestSnapshotStore()
    if args.headless:
        try:
            result = run_replay_debug(config, snapshot_store=store)
        except Exception as exc:
            print(f"Replay failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    try:
        preflight_gui_dependencies()
    except GuiDependencyError:
        print(INSTALL_GUI_DEPS_MESSAGE, file=sys.stderr)
        return 1

    result_holder: dict[str, Any] = {}

    def replay_worker() -> None:
        try:
            result_holder["result"] = run_replay_debug(config, snapshot_store=store)
        except Exception as exc:
            result_holder["error"] = exc

    thread = threading.Thread(target=replay_worker, name="ReplayDebugRunner", daemon=False)
    thread.start()
    try:
        exit_code = run_debug_gui(
            snapshot_store=store,
            scene=inputs.scene,
            mode="replay",
            gui_fps=args.gui_fps,
            title="Exp2 Replay Debug GUI",
            log_path=(Path(args.out_dir) / "gui_diagnostics.csv") if args.out_dir else None,
            runtime_stats_getter=lambda: {
                "mode": "replay",
                "total_received_frames": store.stats_snapshot().update_count,
                "overwritten_snapshot_count": store.stats_snapshot().overwritten_snapshot_count,
            },
        )
    except GuiDependencyError:
        print(INSTALL_GUI_DEPS_MESSAGE, file=sys.stderr)
        thread.join(timeout=1.0)
        return 1
    except KeyboardInterrupt:
        store.mark_gui_closed()
        thread.join()
        return 130

    thread.join()
    if "error" in result_holder:
        print(f"Replay failed: {result_holder['error']}", file=sys.stderr)
        return 1
    result = result_holder.get("result")
    if result is not None:
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
    parser.add_argument("--headless", action="store_true", help="Run replay without opening the GUI.")
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
        timestamp_scale=args.timestamp_scale,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
