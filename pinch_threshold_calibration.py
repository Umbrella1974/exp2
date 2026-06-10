"""Pinch-distance threshold calibration helpers."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Callable

from device_frame_models import DeviceAdapterConfig
from live_raw_stream import LiveRawFrame
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_manus_vive_parser import parse_raw_manus_vive_frame


PINCH_THRESHOLD_METHOD = "three_repeats_window_median"
DEFAULT_PINCH_GRAB_THRESHOLD = 0.1
DEFAULT_PINCH_RELEASE_THRESHOLD = 0.12


class PinchThresholdCalibrationAborted(RuntimeError):
    """Raised when the operator aborts pinch threshold calibration."""


@dataclass(frozen=True)
class PinchThresholdCalibrationConfig:
    """Tunable parameters for interactive pinch threshold calibration."""

    repeat_count: int = 3
    sample_window_seconds: float = 1.0
    min_valid_samples: int = 10
    on_fraction: float = 0.40
    off_fraction: float = 0.50
    min_required_range_m: float = 0.015
    max_repeat_spread_m: float = 0.03
    require_tracker_valid: bool = False

    def __post_init__(self) -> None:
        if self.repeat_count <= 0:
            raise ValueError("repeat_count must be > 0.")
        if self.sample_window_seconds <= 0.0:
            raise ValueError("sample_window_seconds must be > 0.")
        if self.min_valid_samples <= 0:
            raise ValueError("min_valid_samples must be > 0.")
        for name in ("on_fraction", "off_fraction"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0 or value >= 1.0:
                raise ValueError(f"{name} must be between 0 and 1.")
            object.__setattr__(self, name, value)
        if self.on_fraction >= self.off_fraction:
            raise ValueError("on_fraction must be smaller than off_fraction.")
        object.__setattr__(
            self,
            "min_required_range_m",
            _positive_float(self.min_required_range_m, "min_required_range_m"),
        )
        object.__setattr__(
            self,
            "max_repeat_spread_m",
            _positive_float(self.max_repeat_spread_m, "max_repeat_spread_m"),
        )
        object.__setattr__(self, "sample_window_seconds", float(self.sample_window_seconds))
        object.__setattr__(self, "repeat_count", int(self.repeat_count))
        object.__setattr__(self, "min_valid_samples", int(self.min_valid_samples))
        if not isinstance(self.require_tracker_valid, bool):
            raise ValueError("require_tracker_valid must be true or false.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_pinch_threshold_calibration_config() -> PinchThresholdCalibrationConfig:
    """Return default pinch threshold calibration parameters."""

    return PinchThresholdCalibrationConfig()


def load_pinch_threshold_config(path: str | Path | None) -> PinchThresholdCalibrationConfig:
    """Load calibration parameters from JSON or YAML."""

    if path is None:
        return default_pinch_threshold_calibration_config()
    payload = _load_object(Path(path), "pinch threshold config")
    return pinch_threshold_config_from_dict(payload)


def pinch_threshold_config_from_dict(payload: dict[str, Any]) -> PinchThresholdCalibrationConfig:
    """Validate a plain calibration config payload."""

    allowed = {
        "repeat_count",
        "sample_window_seconds",
        "min_valid_samples",
        "on_fraction",
        "off_fraction",
        "min_required_range_m",
        "max_repeat_spread_m",
        "require_tracker_valid",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown pinch threshold config keys: {', '.join(unknown)}")
    return PinchThresholdCalibrationConfig(
        repeat_count=_positive_int(payload.get("repeat_count", PinchThresholdCalibrationConfig.repeat_count), "repeat_count"),
        sample_window_seconds=payload.get(
            "sample_window_seconds",
            PinchThresholdCalibrationConfig.sample_window_seconds,
        ),
        min_valid_samples=_positive_int(
            payload.get("min_valid_samples", PinchThresholdCalibrationConfig.min_valid_samples),
            "min_valid_samples",
        ),
        on_fraction=payload.get("on_fraction", PinchThresholdCalibrationConfig.on_fraction),
        off_fraction=payload.get("off_fraction", PinchThresholdCalibrationConfig.off_fraction),
        min_required_range_m=payload.get(
            "min_required_range_m",
            PinchThresholdCalibrationConfig.min_required_range_m,
        ),
        max_repeat_spread_m=payload.get(
            "max_repeat_spread_m",
            PinchThresholdCalibrationConfig.max_repeat_spread_m,
        ),
        require_tracker_valid=_bool_value(
            payload.get("require_tracker_valid", PinchThresholdCalibrationConfig.require_tracker_valid),
            "require_tracker_valid",
        ),
    )


def load_pinch_threshold_json(path: str | Path) -> dict[str, Any]:
    """Load calibrated thresholds from a full or minimal threshold JSON/YAML file."""

    payload = _load_object(Path(path), "pinch threshold json")
    on, off = threshold_values_from_payload(payload)
    return effective_pinch_threshold_payload(
        pinch_on_threshold_m=on,
        pinch_off_threshold_m=off,
        source="json",
        source_path=Path(path),
    )


def threshold_values_from_payload(payload: dict[str, Any]) -> tuple[float, float]:
    """Extract and validate on/off thresholds from known payload shapes."""

    if "pinch_on_threshold_m" in payload or "pinch_off_threshold_m" in payload:
        on = payload.get("pinch_on_threshold_m")
        off = payload.get("pinch_off_threshold_m")
        return validate_pinch_threshold_values(on, off)

    effective = payload.get("effective_pinch_threshold")
    if isinstance(effective, dict):
        on = effective.get("pinch_on_threshold_m", effective.get("grab"))
        off = effective.get("pinch_off_threshold_m", effective.get("release"))
        return validate_pinch_threshold_values(on, off)

    threshold = payload.get("pinch_threshold")
    if isinstance(threshold, dict):
        return validate_pinch_threshold_values(threshold.get("grab"), threshold.get("release"))

    raise ValueError(
        "pinch threshold payload must contain pinch_on_threshold_m/pinch_off_threshold_m, "
        "effective_pinch_threshold, or pinch_threshold."
    )


def validate_pinch_threshold_values(on: Any, off: Any) -> tuple[float, float]:
    """Validate finite positive hysteresis thresholds."""

    on_value = _positive_float(on, "pinch_on_threshold_m")
    off_value = _positive_float(off, "pinch_off_threshold_m")
    if on_value >= off_value:
        raise ValueError("pinch_on_threshold_m must be smaller than pinch_off_threshold_m.")
    return on_value, off_value


def effective_pinch_threshold_payload(
    *,
    pinch_on_threshold_m: float,
    pinch_off_threshold_m: float,
    source: str,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return normalized threshold fields for config/summary outputs."""

    on, off = validate_pinch_threshold_values(pinch_on_threshold_m, pinch_off_threshold_m)
    return {
        "pinch_threshold": {
            "grab": on,
            "release": off,
        },
        "effective_pinch_threshold": {
            "grab": on,
            "release": off,
            "pinch_on_threshold_m": on,
            "pinch_off_threshold_m": off,
            "source": str(source),
            "source_path": str(source_path) if source_path is not None else None,
        },
        "pinch_on_threshold_m": on,
        "pinch_off_threshold_m": off,
        "pinch_threshold_source": str(source),
        "pinch_threshold_source_path": str(source_path) if source_path is not None else None,
    }


def build_pinch_node_config_payload(
    *,
    thumb_node: int,
    index_node: int,
    tracker_index: int,
    skeleton_index: int,
    pinch_position_mode: str,
) -> dict[str, Any]:
    """Return legacy and neutral pinch-node config fields."""

    return {
        "thumb_node": int(thumb_node),
        "index_node": int(index_node),
        "tracker_index": int(tracker_index),
        "skeleton_index": int(skeleton_index),
        "pinch_position_mode": str(pinch_position_mode),
        "pinch_node_config": {
            "thumb_node": int(thumb_node),
            "secondary_node": int(index_node),
            "secondary_node_role": "index_node_cli_arg",
            "tracker_index": int(tracker_index),
            "skeleton_index": int(skeleton_index),
            "pinch_position_mode": str(pinch_position_mode),
        },
    }


def build_pinch_threshold_calibration_payload(
    *,
    open_repeat_values_m: list[float],
    closed_repeat_values_m: list[float],
    config: PinchThresholdCalibrationConfig,
    node_config: dict[str, Any],
    tracker_valid_sample_fraction: float,
    repeat_summaries: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Compute calibrated hysteresis thresholds from repeat medians."""

    if len(open_repeat_values_m) < config.repeat_count:
        raise ValueError("not enough open repeat values.")
    if len(closed_repeat_values_m) < config.repeat_count:
        raise ValueError("not enough closed repeat values.")
    open_values = [_positive_float(value, "open repeat value") for value in open_repeat_values_m]
    closed_values = [_positive_float(value, "closed repeat value") for value in closed_repeat_values_m]

    open_distance = float(median(open_values))
    closed_distance = float(median(closed_values))
    range_m = open_distance - closed_distance
    on = closed_distance + float(config.on_fraction) * range_m
    off = closed_distance + float(config.off_fraction) * range_m
    open_spread = max(open_values) - min(open_values)
    closed_spread = max(closed_values) - min(closed_values)

    errors: list[str] = []
    if open_distance <= closed_distance:
        errors.append("open_distance_m must be greater than closed_distance_m.")
    if range_m < float(config.min_required_range_m):
        errors.append(
            f"range_m must be >= {float(config.min_required_range_m):.6f}."
        )
    if open_spread > float(config.max_repeat_spread_m):
        errors.append(
            f"open repeat spread {open_spread:.6f} exceeds max_repeat_spread_m "
            f"{float(config.max_repeat_spread_m):.6f}."
        )
    if closed_spread > float(config.max_repeat_spread_m):
        errors.append(
            f"closed repeat spread {closed_spread:.6f} exceeds max_repeat_spread_m "
            f"{float(config.max_repeat_spread_m):.6f}."
        )
    try:
        validate_pinch_threshold_values(on, off)
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors))

    payload = {
        "enabled": True,
        "method": PINCH_THRESHOLD_METHOD,
        **node_config,
        "repeat_count": int(config.repeat_count),
        "sample_window_seconds": float(config.sample_window_seconds),
        "min_valid_samples": int(config.min_valid_samples),
        "closed_distance_m": closed_distance,
        "open_distance_m": open_distance,
        "range_m": range_m,
        "on_fraction": float(config.on_fraction),
        "off_fraction": float(config.off_fraction),
        "pinch_on_threshold_m": on,
        "pinch_off_threshold_m": off,
        "required_tracker_valid": bool(config.require_tracker_valid),
        "tracker_valid_sample_fraction": float(tracker_valid_sample_fraction),
        "warnings": list(warnings or []),
        "repeat_summaries": list(repeat_summaries or []),
        "quality": {
            "valid": True,
            "min_required_range_m": float(config.min_required_range_m),
            "max_repeat_spread_m": float(config.max_repeat_spread_m),
            "open_repeat_values_m": open_values,
            "closed_repeat_values_m": closed_values,
            "open_repeat_spread_m": open_spread,
            "closed_repeat_spread_m": closed_spread,
        },
    }
    payload.update(
        effective_pinch_threshold_payload(
            pinch_on_threshold_m=on,
            pinch_off_threshold_m=off,
            source="calibrated",
        )
    )
    return payload


def run_interactive_pinch_threshold_calibration(
    frame_iter: Any,
    *,
    config: PinchThresholdCalibrationConfig,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
    input_fn: Callable[[str], str],
    node_config: dict[str, Any],
    display_enabled: bool = True,
) -> dict[str, Any]:
    """Run CLI pinch threshold calibration against a live latest-frame source."""

    while True:
        _display(
            display_enabled,
            "Pinch distance calibration\n"
            f"Using thumb_node={node_config['thumb_node']}, "
            f"secondary_node={node_config['pinch_node_config']['secondary_node']}",
        )
        open_values, open_summaries = _collect_repeats(
            frame_iter,
            label="open",
            instruction="keep thumb and target finger naturally open",
            config=config,
            adapter_config=adapter_config,
            adapter=adapter,
            input_fn=input_fn,
            display_enabled=display_enabled,
        )
        closed_values, closed_summaries = _collect_repeats(
            frame_iter,
            label="closed",
            instruction="pinch thumb and target finger tightly",
            config=config,
            adapter_config=adapter_config,
            adapter=adapter,
            input_fn=input_fn,
            display_enabled=display_enabled,
        )
        repeat_summaries = [*open_summaries, *closed_summaries]
        tracker_valid_fraction = _tracker_valid_fraction(repeat_summaries)
        try:
            payload = build_pinch_threshold_calibration_payload(
                open_repeat_values_m=open_values,
                closed_repeat_values_m=closed_values,
                config=config,
                node_config=node_config,
                tracker_valid_sample_fraction=tracker_valid_fraction,
                repeat_summaries=repeat_summaries,
            )
        except ValueError as exc:
            response = input_fn(
                f"Pinch threshold calibration failed: {exc}\n"
                "Press Enter to redo, or q to abort... "
            ).strip().lower()
            if response == "q":
                raise PinchThresholdCalibrationAborted("pinch threshold calibration aborted")
            continue

        _display(
            display_enabled,
            "Computed pinch thresholds:\n"
            f"  open_distance_m = {payload['open_distance_m']:.6f}\n"
            f"  closed_distance_m = {payload['closed_distance_m']:.6f}\n"
            f"  pinch_on_threshold_m = {payload['pinch_on_threshold_m']:.6f}\n"
            f"  pinch_off_threshold_m = {payload['pinch_off_threshold_m']:.6f}",
        )
        response = input_fn("Accept? [Enter=yes / r=redo / q=abort] ").strip().lower()
        if response == "q":
            raise PinchThresholdCalibrationAborted("pinch threshold calibration aborted")
        if response == "r":
            continue
        return payload


def collect_pinch_distance_window(
    frame_iter: Any,
    *,
    label: str,
    repeat_index: int,
    config: PinchThresholdCalibrationConfig,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
) -> dict[str, Any]:
    """Collect one time window of valid pinch distances."""

    _drain_live_queue(frame_iter)
    live_start = time.monotonic()
    live_end = live_start + float(config.sample_window_seconds)
    distances: list[float] = []
    summary: dict[str, Any] = {
        "label": label,
        "repeat_index": int(repeat_index),
        "sample_window_seconds": float(config.sample_window_seconds),
        "received_frame_count": 0,
        "valid_sample_count": 0,
        "invalid_sample_count": 0,
        "tracker_valid_count": 0,
        "tracker_valid_valid_sample_count": 0,
        "hand_valid_count": 0,
        "parse_error_count": 0,
        "adapter_error_count": 0,
        "frame_start": None,
        "frame_end": None,
        "median_distance_m": None,
        "errors": [],
    }

    while True:
        remaining = live_end - time.monotonic()
        if remaining <= 0.0:
            break
        frame = frame_iter.get_frame(timeout=min(0.1, max(0.0, remaining)))
        if frame is None:
            continue
        summary["received_frame_count"] += 1
        frame_index = getattr(frame, "frame_index", None)
        if summary["frame_start"] is None:
            summary["frame_start"] = frame_index
        summary["frame_end"] = frame_index
        processed = _process_frame(frame, adapter_config, adapter)
        if processed["parse_ok"]:
            if processed["tracker_valid"]:
                summary["tracker_valid_count"] += 1
            if processed["hand_valid"]:
                summary["hand_valid_count"] += 1
        else:
            summary["parse_error_count"] += 1
        if processed["parse_ok"] and not processed["adapter_ok"]:
            summary["adapter_error_count"] += 1
        distance = processed["pinch_distance"]
        is_valid = (
            processed["parse_ok"]
            and processed["adapter_ok"]
            and processed["hand_valid"]
            and processed["pinch_valid"]
            and distance is not None
            and math.isfinite(float(distance))
            and float(distance) > 0.0
            and (processed["tracker_valid"] or not config.require_tracker_valid)
        )
        if is_valid:
            distances.append(float(distance))
            summary["valid_sample_count"] += 1
            if processed["tracker_valid"]:
                summary["tracker_valid_valid_sample_count"] += 1
        else:
            summary["invalid_sample_count"] += 1

    if len(distances) < int(config.min_valid_samples):
        summary["errors"].append(
            f"{label} repeat {repeat_index}: only {len(distances)} valid pinch distance "
            f"samples; need at least {int(config.min_valid_samples)}."
        )
        return summary
    summary["median_distance_m"] = float(median(distances))
    return summary


def _collect_repeats(
    frame_iter: Any,
    *,
    label: str,
    instruction: str,
    config: PinchThresholdCalibrationConfig,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
    input_fn: Callable[[str], str],
    display_enabled: bool,
) -> tuple[list[float], list[dict[str, Any]]]:
    values: list[float] = []
    summaries: list[dict[str, Any]] = []
    repeat_index = 1
    while repeat_index <= int(config.repeat_count):
        response = input_fn(
            f"[PINCH {label.upper()}] Repeat {repeat_index}/{config.repeat_count}: "
            f"{instruction}. Press Enter to collect {config.sample_window_seconds:.1f}s, "
            "or q to abort... "
        ).strip().lower()
        if response == "q":
            raise PinchThresholdCalibrationAborted("pinch threshold calibration aborted")
        _display(display_enabled, f"Collecting {label} repeat {repeat_index}...")
        summary = collect_pinch_distance_window(
            frame_iter,
            label=label,
            repeat_index=repeat_index,
            config=config,
            adapter_config=adapter_config,
            adapter=adapter,
        )
        if summary["errors"]:
            response = input_fn(
                f"{summary['errors'][0]}\nPress Enter to redo this repeat, or q to abort... "
            ).strip().lower()
            if response == "q":
                raise PinchThresholdCalibrationAborted("pinch threshold calibration aborted")
            continue
        value = float(summary["median_distance_m"])
        _display(display_enabled, f"{label} repeat {repeat_index} median distance = {value:.6f} m")
        values.append(value)
        summaries.append(summary)
        repeat_index += 1
    return values, summaries


def _process_frame(
    live_frame: Any,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
) -> dict[str, Any]:
    raw_frame = live_frame.raw_frame if isinstance(live_frame, LiveRawFrame) else live_frame
    device_frame = None
    sample = None
    parse_ok = False
    adapter_ok = False
    try:
        device_frame = parse_raw_manus_vive_frame(raw_frame, adapter_config)
        parse_ok = True
        sample = adapter.to_experiment_input_sample(device_frame)
        adapter_ok = True
    except Exception:
        pass
    hand = getattr(device_frame, "hand", None) if device_frame is not None else None
    metadata = getattr(sample, "metadata", {}) or {}
    return {
        "parse_ok": parse_ok,
        "adapter_ok": adapter_ok,
        "tracker_valid": bool(metadata.get("tracker_valid", getattr(sample, "tracker_valid", False))),
        "hand_valid": bool(getattr(hand, "valid", False)),
        "pinch_valid": bool(metadata.get("pinch_valid", False)),
        "pinch_distance": getattr(sample, "pinch_distance", None),
    }


def _tracker_valid_fraction(repeat_summaries: list[dict[str, Any]]) -> float:
    valid_count = sum(int(summary.get("valid_sample_count", 0) or 0) for summary in repeat_summaries)
    if valid_count <= 0:
        return 0.0
    tracker_valid = sum(
        int(summary.get("tracker_valid_valid_sample_count", 0) or 0)
        for summary in repeat_summaries
    )
    return float(tracker_valid) / float(valid_count)


def _drain_live_queue(frame_iter: Any) -> None:
    if not hasattr(frame_iter, "get_frame") or not hasattr(frame_iter, "stats_snapshot"):
        return
    while frame_iter.get_frame(timeout=0.0) is not None:
        pass


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif suffix in {".yaml", ".yml"}:
        payload = _load_yaml(path)
    else:
        raise ValueError(f"{label} must be .json, .yaml, or .yml")
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object.")
    return payload


def _load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("YAML pinch threshold config requires PyYAML. Install with: pip install PyYAML") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _positive_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number.") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive number.")
    return result


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be a positive integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if result <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return result


def _bool_value(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be true or false.")


def _display(enabled: bool, message: str) -> None:
    if enabled:
        print(message)
