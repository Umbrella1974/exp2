"""Live raw stream smoke test for MANUS/Vive newline JSON input.

Stage 5B-0 intentionally stops at parser/adapter health metrics. It does not
start TrialController, does not run BlockController, and does not send haptics.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from device_frame_models import DeviceAdapterConfig
from live_raw_stream import LiveRawFrame, LiveRawStreamServer, QUEUE_DROP_POLICY
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from session_recorder import SessionRecorder


LIVE_METRICS_HEADER = [
    "frame_index",
    "raw_timestamp",
    "receive_time_monotonic",
    "receive_wall_time",
    "parse_ok",
    "adapter_ok",
    "tracker_valid",
    "hand_valid",
    "pinch_valid",
    "pinch_distance",
    "skeleton_count",
    "tracker_count",
    "sync_delta_ms",
    "inter_receive_interval_ms",
    "processing_latency_ms",
    "queue_size",
    "dropped_frame_count",
    "error_message",
]


@dataclass(frozen=True)
class LiveRawPreviewConfig:
    """Configuration for live raw preview."""

    host: str = "127.0.0.1"
    port: int = 8888
    duration_seconds: float | None = None
    max_frames: int | None = None
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    timestamp_scale: float = 0.001
    out_dir: Path = Path("data/live_raw_preview")
    write_session: bool = False
    session_dir: Path | None = None
    print_every: int = 30
    save_raw_jsonl: bool = True
    socket_timeout: float | None = None
    max_queue_size: int = 300


@dataclass(frozen=True)
class LiveRawPreviewResult:
    """Outputs produced by one live raw preview run."""

    metrics: list[dict[str, Any]]
    summary: dict[str, Any]


def run_live_raw_preview(
    config: LiveRawPreviewConfig,
    *,
    source: Any | None = None,
) -> LiveRawPreviewResult:
    """Run live raw preview using a real server or injected source."""

    out_dir = Path(config.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "live_metrics.csv"
    summary_path = out_dir / "live_summary.json"
    raw_path = out_dir / "raw_frames.jsonl"

    adapter_config = DeviceAdapterConfig(
        skeleton_index=config.skeleton_index,
        tracker_index=config.tracker_index,
        thumb_tip_node_id=config.thumb_node,
        index_tip_node_id=config.index_node,
        timestamp_scale=config.timestamp_scale,
    )
    adapter = ManusViveExperimentAdapter(None, config=adapter_config)
    own_source = source is None
    live_source = source or LiveRawStreamServer(
        host=config.host,
        port=config.port,
        max_queue_size=config.max_queue_size,
        socket_timeout=config.socket_timeout,
    )

    raw_handle = None
    session_recorder = None
    metrics: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not config.save_raw_jsonl:
        warnings.append("raw JSONL saving disabled by --no-save-raw-jsonl.")

    try:
        if config.save_raw_jsonl:
            raw_handle = raw_path.open("w", encoding="utf-8")
        _write_metrics_header(metrics_path)

        if config.write_session:
            session_dir = config.session_dir or (out_dir / "session")
            session_recorder = SessionRecorder(session_dir)
            session_recorder.start_session(
                session_meta={
                    "mode": "live_raw_preview",
                    "is_formal_calibration": False,
                    "is_live_trial": False,
                    "trial_controller_started": False,
                    "processed_frames_are_trial_outputs": False,
                    "warnings": [
                        "Live raw preview records parser/adapter health only; it does not start TrialController."
                    ],
                },
                calibration={
                    "calibration_type": "none",
                    "is_formal_calibration": False,
                },
                trial_config={
                    "mode": "live_raw_preview",
                    "trial_controller_started": False,
                    "processed_frames_are_trial_outputs": False,
                },
            )

        if hasattr(live_source, "start"):
            live_source.start()

        run_started = time.monotonic()
        previous_receive_time: float | None = None
        stop_reason = ""
        raw_parser_error_count = 0
        adapter_error_count = 0
        tracker_valid_count = 0
        hand_valid_count = 0
        pinch_valid_count = 0
        processing_latencies: list[float] = []
        sync_deltas: list[float] = []
        processed_count = 0

        while True:
            if config.duration_seconds is not None and time.monotonic() - run_started >= config.duration_seconds:
                stop_reason = "duration_reached"
                _stop_source(live_source, stop_reason)
                break
            if config.max_frames is not None and processed_count >= config.max_frames:
                stop_reason = "max_frames"
                _stop_source(live_source, stop_reason)
                break

            live_frame = live_source.get_frame(timeout=0.1)
            if live_frame is None:
                source_stop_reason = _source_stop_reason(live_source)
                if source_stop_reason in {"client_disconnected", "server_stopped", "socket_error"}:
                    stop_reason = source_stop_reason
                    break
                if _source_is_stopped(live_source):
                    stop_reason = source_stop_reason or "source_stopped"
                    break
                continue

            row, parse_ok, adapter_ok, device_frame = _process_live_frame(
                live_frame,
                adapter_config,
                adapter,
                previous_receive_time,
                live_source,
            )
            previous_receive_time = live_frame.receive_time_monotonic
            processed_count += 1
            metrics.append(row)
            _append_metric_row(metrics_path, row)

            _write_raw_frame(raw_handle, live_frame.raw_frame)
            if session_recorder is not None:
                session_recorder.record_raw_frame(live_frame.frame_index, live_frame.raw_frame)
                if device_frame is not None:
                    session_recorder.record_device_frame(live_frame.frame_index, device_frame)

            if not parse_ok:
                raw_parser_error_count += 1
            if parse_ok and not adapter_ok:
                adapter_error_count += 1
            if row["tracker_valid"] is True:
                tracker_valid_count += 1
            if row["hand_valid"] is True:
                hand_valid_count += 1
            if row["pinch_valid"] is True:
                pinch_valid_count += 1
            if row["processing_latency_ms"] not in ("", None):
                processing_latencies.append(float(row["processing_latency_ms"]))
            if row["sync_delta_ms"] not in ("", None):
                sync_deltas.append(float(row["sync_delta_ms"]))

            if config.print_every > 0 and processed_count % config.print_every == 0:
                print(
                    f"[LIVE] processed={processed_count} "
                    f"tracker_valid={tracker_valid_count} pinch_valid={pinch_valid_count} "
                    f"dropped={_source_dropped_count(live_source)}"
                )

        stats = _source_stats(live_source)
        if not stop_reason:
            stop_reason = stats.get("stop_reason") or "completed"
        total_received = int(stats.get("total_received_frames", processed_count))
        parse_error_count = int(stats.get("parse_error_count", 0)) + raw_parser_error_count
        summary = {
            "mode": "live_raw_preview",
            "is_formal_calibration": False,
            "is_live_trial": False,
            "trial_controller_started": False,
            "processed_frames_are_trial_outputs": False,
            "total_received_frames": total_received,
            "total_processed_frames": int(processed_count),
            "parse_error_count": int(parse_error_count),
            "bad_json_line_count": int(stats.get("bad_json_line_count", 0)),
            "raw_parser_error_count": int(raw_parser_error_count),
            "adapter_error_count": int(adapter_error_count),
            "tracker_valid_frame_count": int(tracker_valid_count),
            "hand_valid_frame_count": int(hand_valid_count),
            "pinch_valid_frame_count": int(pinch_valid_count),
            "dropped_frame_count": int(stats.get("dropped_frame_count", 0)),
            "queue_drop_policy": QUEUE_DROP_POLICY,
            "stop_reason": stop_reason,
            "mean_receive_fps": _mean_receive_fps(metrics),
            "mean_processing_latency_ms": mean(processing_latencies) if processing_latencies else None,
            "max_processing_latency_ms": max(processing_latencies) if processing_latencies else None,
            "mean_sync_delta_ms": mean(sync_deltas) if sync_deltas else None,
            "max_sync_delta_ms": max(sync_deltas) if sync_deltas else None,
            "last_parse_error_message": stats.get("last_parse_error_message", ""),
            "last_bad_json_preview": stats.get("last_bad_json_preview", ""),
            "save_raw_jsonl": bool(config.save_raw_jsonl),
            "warnings": warnings,
        }
        _write_json(summary_path, summary)
        if session_recorder is not None:
            session_recorder.finalize(summary)
            _write_json(Path(session_recorder.session_dir) / "live_summary.json", summary)
        return LiveRawPreviewResult(metrics=metrics, summary=summary)
    except KeyboardInterrupt:
        _stop_source(live_source, "keyboard_interrupt")
        raise
    finally:
        if raw_handle is not None:
            raw_handle.close()
        if own_source:
            _stop_source(live_source, _source_stop_reason(live_source) or "server_stopped")
            if hasattr(live_source, "join"):
                live_source.join(timeout=1.0)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = _parse_args(argv)
    config = LiveRawPreviewConfig(
        host=args.host,
        port=args.port,
        duration_seconds=args.duration_seconds,
        max_frames=args.max_frames,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        timestamp_scale=args.timestamp_scale,
        out_dir=Path(args.out_dir),
        write_session=args.write_session,
        session_dir=Path(args.session_dir) if args.session_dir is not None else None,
        print_every=args.print_every,
        save_raw_jsonl=args.save_raw_jsonl,
        socket_timeout=args.socket_timeout,
        max_queue_size=args.max_queue_size,
    )
    try:
        result = run_live_raw_preview(config)
    except KeyboardInterrupt:
        print("[LIVE] interrupted by user")
        return 130
    print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def _process_live_frame(
    live_frame: LiveRawFrame,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
    previous_receive_time: float | None,
    source: Any,
) -> tuple[dict[str, Any], bool, bool, Any | None]:
    start = time.monotonic()
    raw = live_frame.raw_frame
    device_frame = None
    sample = None
    parse_ok = False
    adapter_ok = False
    error_message = ""
    try:
        device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
        parse_ok = True
        sample = adapter.to_experiment_input_sample(device_frame)
        adapter_ok = True
    except Exception as exc:
        error_message = str(exc)

    processing_latency_ms = (time.monotonic() - live_frame.receive_time_monotonic) * 1000.0
    inter_receive_interval_ms = (
        (live_frame.receive_time_monotonic - previous_receive_time) * 1000.0
        if previous_receive_time is not None
        else ""
    )
    tracker = getattr(device_frame, "tracker", None) if device_frame is not None else None
    hand = getattr(device_frame, "hand", None) if device_frame is not None else None
    row = {
        "frame_index": live_frame.frame_index,
        "raw_timestamp": raw.get("timestamp", ""),
        "receive_time_monotonic": live_frame.receive_time_monotonic,
        "receive_wall_time": live_frame.receive_wall_time,
        "parse_ok": parse_ok,
        "adapter_ok": adapter_ok,
        "tracker_valid": getattr(sample, "tracker_valid", "") if sample is not None else "",
        "hand_valid": getattr(hand, "valid", "") if hand is not None else "",
        "pinch_valid": _metadata_value(sample, "pinch_valid"),
        "pinch_distance": getattr(sample, "pinch_distance", "") if sample is not None else "",
        "skeleton_count": len(raw.get("skeletons", [])) if isinstance(raw.get("skeletons", []), list) else "",
        "tracker_count": len(raw.get("trackers", [])) if isinstance(raw.get("trackers", []), list) else "",
        "sync_delta_ms": getattr(device_frame, "sync_delta_ms", "") if device_frame is not None else "",
        "inter_receive_interval_ms": inter_receive_interval_ms,
        "processing_latency_ms": processing_latency_ms,
        "queue_size": _source_queue_size(source),
        "dropped_frame_count": _source_dropped_count(source),
        "error_message": error_message,
    }
    del start, tracker
    return row, parse_ok, adapter_ok, device_frame


def _metadata_value(sample: Any, key: str) -> Any:
    if sample is None:
        return ""
    metadata = getattr(sample, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return ""
    return metadata.get(key, "")


def _write_metrics_header(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIVE_METRICS_HEADER)
        writer.writeheader()


def _append_metric_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIVE_METRICS_HEADER)
        writer.writerow({key: _csv_value(row.get(key, "")) for key in LIVE_METRICS_HEADER})


def _write_raw_frame(handle: Any, raw_frame: dict[str, Any]) -> None:
    if handle is None:
        return
    handle.write(json.dumps(raw_frame, ensure_ascii=False))
    handle.write("\n")
    handle.flush()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _mean_receive_fps(metrics: list[dict[str, Any]]) -> float | None:
    if len(metrics) < 2:
        return None
    start = float(metrics[0]["receive_time_monotonic"])
    end = float(metrics[-1]["receive_time_monotonic"])
    elapsed = end - start
    if elapsed <= 0.0:
        return None
    return float((len(metrics) - 1) / elapsed)


def _source_stats(source: Any) -> dict[str, Any]:
    if hasattr(source, "stats_snapshot"):
        snapshot = source.stats_snapshot()
        if isinstance(snapshot, dict):
            return dict(snapshot)
        if hasattr(snapshot, "__dict__"):
            return dict(snapshot.__dict__)
    return {
        "total_received_frames": getattr(source, "total_received_frames", None),
        "parse_error_count": getattr(source, "parse_error_count", 0),
        "bad_json_line_count": getattr(source, "bad_json_line_count", 0),
        "dropped_frame_count": getattr(source, "dropped_frame_count", 0),
        "stop_reason": getattr(source, "stop_reason", None),
    }


def _source_queue_size(source: Any) -> int | str:
    if hasattr(source, "queue_size"):
        return source.queue_size()
    return ""


def _source_dropped_count(source: Any) -> int:
    return int(getattr(source, "dropped_frame_count", 0))


def _source_stop_reason(source: Any) -> str | None:
    value = getattr(source, "stop_reason", None)
    if callable(value):
        return value()
    return value


def _source_is_stopped(source: Any) -> bool:
    if hasattr(source, "stop_event"):
        return bool(source.stop_event.is_set())
    return bool(getattr(source, "stopped", False))


def _stop_source(source: Any, reason: str) -> None:
    if hasattr(source, "stop"):
        source.stop(reason)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live raw MANUS/Vive stream preview.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--thumb-node", type=int, default=4)
    parser.add_argument("--index-node", type=int, default=9)
    parser.add_argument("--tracker-index", type=int, default=0)
    parser.add_argument("--skeleton-index", type=int, default=0)
    parser.add_argument("--timestamp-scale", type=float, default=0.001)
    parser.add_argument("--out-dir", default="data/live_raw_preview")
    parser.add_argument("--write-session", action="store_true")
    parser.add_argument("--session-dir", default=None)
    parser.add_argument("--print-every", type=int, default=30)
    parser.add_argument("--socket-timeout", type=float, default=None)
    parser.add_argument("--max-queue-size", type=int, default=300)
    parser.set_defaults(save_raw_jsonl=True)
    parser.add_argument("--save-raw-jsonl", dest="save_raw_jsonl", action="store_true")
    parser.add_argument("--no-save-raw-jsonl", dest="save_raw_jsonl", action="store_false")
    args = parser.parse_args(argv)
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        parser.error("--duration-seconds must be > 0.")
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be > 0.")
    if args.max_queue_size <= 0:
        parser.error("--max-queue-size must be > 0.")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
