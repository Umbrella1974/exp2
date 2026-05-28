"""Command-line live table-line calibration tool."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from calibration_io import calibration_to_dict
from calibration_live_runner import (
    CalibrationLiveConfig,
    CalibrationSegmentSpec,
    RAW_JSONL_SIMULATED_LIVE_WARNING,
    run_live_table_calibration,
    save_live_calibration_result,
)
from live_raw_stream import LiveRawStreamServer
from simulated_live_source import RawJsonlSimulatedLiveSource


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = _parse_args(argv)
    source = _build_source(args)
    config = _build_config(args)

    print("Live table-line calibration")
    print("Segments: origin, long_axis_line, width_axis_line, diagonal_line")
    if config.collection_mode == "raw_jsonl_simulated_live":
        print(RAW_JSONL_SIMULATED_LIVE_WARNING)
    else:
        print(f"Listening on {args.live_host}:{args.live_port}; start the sender before sampling.")

    try:
        result = run_live_table_calibration(
            source,
            config,
            before_segment_callback=_before_segment_callback(config, source),
            progress_callback=_progress_callback(config),
        )
    except KeyboardInterrupt:
        _stop_source(source, "keyboard_interrupt")
        print("calibration interrupted by user", file=sys.stderr)
        return 130
    except Exception as exc:
        _stop_source(source, "calibration_error")
        print(f"calibration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if config.collection_mode == "live_stream":
            _stop_source(source, "calibration_completed")

    _print_result_summary(result)
    if result.errors:
        print("calibration failed; errors are present, so no file was saved.", file=sys.stderr)
        return 1
    if result.calibration is None:
        print("calibration failed; no calibration object was created.", file=sys.stderr)
        return 1
    if result.warnings and not args.no_confirm_save:
        answer = input("Warnings are present. Save calibration anyway? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("calibration not saved.")
            return 1

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    save_live_calibration_result(result, args.out)
    print(f"saved calibration: {args.out}")
    return 0


def _build_config(args: argparse.Namespace) -> CalibrationLiveConfig:
    collection_mode = "live_stream" if args.use_live_stream else "raw_jsonl_simulated_live"
    return CalibrationLiveConfig(
        calibration_id=args.calibration_id or "",
        point_source=args.point_source,
        sample_duration_seconds=args.sample_duration_seconds,
        min_samples=args.min_samples,
        min_line_length=args.min_line_length,
        up_hint=list(args.up_hint),
        timestamp_scale=args.timestamp_scale,
        output_path=Path(args.out),
        notes=args.notes,
        require_enter_between_segments=not args.auto_advance,
        auto_advance=args.auto_advance,
        collection_mode=collection_mode,
        source_path=str(args.raw_jsonl) if args.raw_jsonl is not None else None,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        print_every=args.print_every,
    )


def _build_source(args: argparse.Namespace) -> Any:
    if args.use_live_stream:
        return LiveRawStreamServer(
            host=args.live_host,
            port=args.live_port,
            max_queue_size=args.max_queue_size,
            socket_timeout=args.socket_timeout,
        )
    return RawJsonlSimulatedLiveSource(
        args.raw_jsonl,
        timestamp_scale=args.timestamp_scale,
        real_time=args.replay_real_time,
        speed=args.speed,
        max_frames=args.max_frames,
    )


def _before_segment_callback(config: CalibrationLiveConfig, source: Any):
    def before(segment: CalibrationSegmentSpec) -> None:
        print("")
        print(f"[{segment.label}] {segment.prompt}")
        print(f"Collecting {segment.duration_seconds:.3f} seconds; min samples={segment.min_samples}.")
        if config.collection_mode == "live_stream":
            _wait_for_live_stream_ready(source, segment.label)
        if config.require_enter_between_segments and not config.auto_advance:
            input("Press Enter to start this segment...")
    return before


def _progress_callback(config: CalibrationLiveConfig):
    def progress(summary: dict[str, Any]) -> None:
        last_error = str(summary.get("last_error_message", ""))
        if len(last_error) > 120:
            last_error = last_error[:117] + "..."
        print(
            f"[{summary['label']}] elapsed={float(summary['duration_seconds']):.2f}s "
            f"valid={summary['valid_sample_count']} "
            f"tracker={summary['tracker_valid_count']} hand={summary['hand_valid_count']} "
            f"parse_error={summary.get('parse_error_count', 0)} "
            f"raw_skeletons={summary.get('skeleton_count', 0)} "
            f"raw_trackers={summary.get('tracker_count', 0)} "
            f"last_error={last_error}"
        )
    return progress if config.print_every > 0 else None


def _print_result_summary(result: Any) -> None:
    payload = {
        "errors": result.errors,
        "warnings": result.warnings,
        "live_metrics_summary": result.live_metrics_summary,
        "segment_summaries": result.segment_summaries,
    }
    if result.calibration is not None:
        calibration_payload = calibration_to_dict(result.calibration)
        payload["calibration_id"] = result.calibration.calibration_id
        payload["calibration_type"] = result.calibration.calibration_type
        payload["is_formal_calibration"] = result.calibration.is_formal_calibration
        payload["quality"] = calibration_payload["quality"]
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _stop_source(source: Any, reason: str) -> None:
    if hasattr(source, "stop"):
        source.stop(reason)
    if hasattr(source, "join"):
        source.join(timeout=1.0)


def _wait_for_live_stream_ready(
    source: Any,
    segment_label: str,
    *,
    timeout: float | None = None,
    poll_interval: float = 0.1,
    status_interval: float = 2.0,
) -> None:
    """Wait until a live client has connected and at least one frame arrived."""

    started = time.monotonic()
    next_status_at = started
    baseline_received = _stats_int(_source_stats(source), "total_received_frames")
    print(f"[LIVE] waiting for sender before {segment_label}...")
    while True:
        stats = _source_stats(source)
        connected = bool(stats.get("client_connected", False))
        queued = _stats_int(stats, "queue_size")
        received = _stats_int(stats, "total_received_frames")
        stop_reason = str(stats.get("stop_reason") or "")
        if connected and (queued > 0 or received > baseline_received):
            print(
                f"[LIVE] stream ready: client_connected=1 queued={queued} "
                f"received={received}"
            )
            return
        if stop_reason in {"client_disconnected", "server_stopped", "socket_error"}:
            raise RuntimeError(f"live stream stopped before calibration data arrived: {stop_reason}")
        now = time.monotonic()
        if timeout is not None and now - started >= timeout:
            raise TimeoutError(
                "timed out waiting for live stream data; check manus_vive_com connection "
                "and ensure it is sending newline-delimited JSON frames."
            )
        if now >= next_status_at:
            print(
                f"[LIVE] waiting... client_connected={int(connected)} "
                f"queued={queued} received={received}"
            )
            next_status_at = now + status_interval
        time.sleep(poll_interval)


def _source_stats(source: Any) -> dict[str, Any]:
    if hasattr(source, "stats_snapshot"):
        snapshot = source.stats_snapshot()
        if isinstance(snapshot, dict):
            return dict(snapshot)
        if hasattr(snapshot, "__dict__"):
            return dict(snapshot.__dict__)
    return {
        "client_connected": False,
        "running": True,
        "queue_size": source.queue_size() if hasattr(source, "queue_size") else 0,
        "total_received_frames": getattr(source, "total_received_frames", 0),
        "stop_reason": getattr(source, "stop_reason", None),
    }


def _stats_int(stats: dict[str, Any], key: str) -> int:
    try:
        return int(stats.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_vec3(value: str) -> tuple[float, float, float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected three comma-separated floats")
    vector = np.asarray(parts, dtype=float)
    if not np.all(np.isfinite(vector)):
        raise argparse.ArgumentTypeError("vector components must be finite")
    return (float(vector[0]), float(vector[1]), float(vector[2]))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect live table-line calibration.")
    parser.add_argument("--out", default="data/calibration/live_table_calibration.json", type=Path)
    parser.add_argument("--calibration-id", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--point-source",
        choices=("tracker_position_world", "pinch_center_world"),
        default="tracker_position_world",
    )
    parser.add_argument("--sample-duration-seconds", default=5.0, type=float)
    parser.add_argument("--min-samples", default=10, type=int)
    parser.add_argument("--min-line-length", default=0.10, type=float)
    parser.add_argument("--up-hint", default=(0.0, 0.0, 1.0), type=_parse_vec3)
    parser.add_argument("--timestamp-scale", default=0.001, type=float)
    parser.add_argument("--thumb-node", default=4, type=int)
    parser.add_argument("--index-node", default=9, type=int)
    parser.add_argument("--tracker-index", default=0, type=int)
    parser.add_argument("--skeleton-index", default=0, type=int)

    parser.add_argument("--raw-jsonl", default=None, type=Path)
    parser.add_argument("--simulate-live", action="store_true")
    parser.add_argument("--replay-real-time", action="store_true")
    parser.add_argument("--speed", default=1.0, type=float)
    parser.add_argument("--max-frames", default=None, type=int)

    parser.add_argument("--use-live-stream", action="store_true")
    parser.add_argument("--live-host", default="127.0.0.1")
    parser.add_argument("--live-port", default=8888, type=int)
    parser.add_argument("--socket-timeout", default=None, type=float)
    parser.add_argument("--max-queue-size", default=300, type=int)

    parser.add_argument("--auto-advance", action="store_true")
    parser.add_argument("--no-confirm-save", action="store_true")
    parser.add_argument("--print-every", default=30, type=int)
    args = parser.parse_args(argv)

    if args.raw_jsonl is None and not args.use_live_stream:
        parser.error("choose an input source: --raw-jsonl --simulate-live or --use-live-stream.")
    if args.raw_jsonl is not None and args.use_live_stream:
        parser.error("--raw-jsonl and --use-live-stream are mutually exclusive.")
    if args.raw_jsonl is not None and not args.simulate_live:
        parser.error("--raw-jsonl requires --simulate-live.")
    if args.simulate_live and args.raw_jsonl is None:
        parser.error("--simulate-live requires --raw-jsonl.")
    if args.sample_duration_seconds <= 0.0:
        parser.error("--sample-duration-seconds must be > 0.")
    if args.min_samples <= 0:
        parser.error("--min-samples must be > 0.")
    if args.min_line_length <= 0.0:
        parser.error("--min-line-length must be > 0.")
    if args.speed <= 0.0:
        parser.error("--speed must be > 0.")
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be > 0.")
    if args.max_queue_size <= 0:
        parser.error("--max-queue-size must be > 0.")
    if args.print_every < 0:
        parser.error("--print-every must be >= 0.")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
