"""Configuration for Stage 1 haptic command routing."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DIRECTION_KEYS = ("X_NEG", "X_POS", "Y_NEG", "Y_POS", "Z_NEG", "Z_POS")
AXIS_KEYS = ("X", "Y", "Z")
MATRIX_FEEDBACK_MODES = ("latched_once", "continuous_resend")
MATRIX_DIRECTION_SEMANTICS = ("blocked_surface", "correction_direction")
MATRIX_MISSING_COMBINATION_POLICIES = ("skip", "union_single_directions")
VIBRATION_PROTOCOLS = ("pending",)


@dataclass(frozen=True)
class MatrixHapticConfig:
    """Configuration for the Matrix/electrotactile ESP32 target."""

    enabled: bool = False
    required: bool = True
    host: str = ""
    port: int = 12345
    connect_timeout_s: float = 3.0
    send_timeout_s: float = 0.05
    startup_settle_seconds: float = 7.0
    max_queue_size: int = 8
    latest_only: bool = True
    feedback_mode: str = "latched_once"
    resend_interval_ms: float = 100.0
    direction_semantics: str = "blocked_surface"
    direction_channel_map: dict[str, list[int]] = field(
        default_factory=lambda: {key: [] for key in DIRECTION_KEYS}
    )
    combination_channel_map: dict[str, list[int]] = field(default_factory=dict)
    missing_combination_policy: str = "skip"
    ignore_direction_axes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bool_value(self.enabled, "matrix.enabled")
        _bool_value(self.required, "matrix.required")
        if self.enabled and not str(self.host).strip():
            raise ValueError("matrix.host is required when matrix.enabled=true.")
        object.__setattr__(self, "port", _port_value(self.port, "matrix.port"))
        object.__setattr__(
            self,
            "connect_timeout_s",
            _positive_float(self.connect_timeout_s, "matrix.connect_timeout_s"),
        )
        object.__setattr__(
            self,
            "send_timeout_s",
            _positive_float(self.send_timeout_s, "matrix.send_timeout_s"),
        )
        object.__setattr__(
            self,
            "startup_settle_seconds",
            _non_negative_float(
                self.startup_settle_seconds,
                "matrix.startup_settle_seconds",
            ),
        )
        object.__setattr__(
            self,
            "max_queue_size",
            _positive_int(self.max_queue_size, "matrix.max_queue_size"),
        )
        _bool_value(self.latest_only, "matrix.latest_only")
        if self.feedback_mode not in MATRIX_FEEDBACK_MODES:
            raise ValueError(
                "matrix.feedback_mode must be one of: "
                + ", ".join(MATRIX_FEEDBACK_MODES)
            )
        object.__setattr__(
            self,
            "resend_interval_ms",
            _positive_float(self.resend_interval_ms, "matrix.resend_interval_ms"),
        )
        if self.direction_semantics not in MATRIX_DIRECTION_SEMANTICS:
            raise ValueError(
                "matrix.direction_semantics must be one of: "
                + ", ".join(MATRIX_DIRECTION_SEMANTICS)
            )
        object.__setattr__(
            self,
            "direction_channel_map",
            _direction_channel_map(self.direction_channel_map),
        )
        object.__setattr__(
            self,
            "combination_channel_map",
            _combination_channel_map(self.combination_channel_map),
        )
        if self.missing_combination_policy not in MATRIX_MISSING_COMBINATION_POLICIES:
            raise ValueError(
                "matrix.missing_combination_policy must be one of: "
                + ", ".join(MATRIX_MISSING_COMBINATION_POLICIES)
            )
        object.__setattr__(
            self,
            "ignore_direction_axes",
            _axis_list(self.ignore_direction_axes, "matrix.ignore_direction_axes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VibrationHapticConfig:
    """Configuration for the reserved vibration ESP32 target."""

    enabled: bool = False
    host: str = ""
    port: int = 12345
    protocol: str = "pending"
    enable_contact: bool = True
    enable_release: bool = True
    enable_slip: bool = True
    enable_slip_pinch_insufficient: bool = True
    enable_slip_track_blocked: bool = True
    enable_slip_track_blocked_in_target_region: bool = True

    def __post_init__(self) -> None:
        for name in (
            "enabled",
            "enable_contact",
            "enable_release",
            "enable_slip",
            "enable_slip_pinch_insufficient",
            "enable_slip_track_blocked",
            "enable_slip_track_blocked_in_target_region",
        ):
            _bool_value(getattr(self, name), f"vibration.{name}")
        object.__setattr__(self, "port", _port_value(self.port, "vibration.port"))
        if self.protocol not in VIBRATION_PROTOCOLS:
            raise ValueError(
                "vibration.protocol must be one of: "
                + ", ".join(VIBRATION_PROTOCOLS)
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HapticConfig:
    """Top-level haptic configuration.

    ``enabled`` gates the haptic stage. Target-level ``enabled`` flags select
    which device routes can emit commands.
    """

    enabled: bool = False
    matrix: MatrixHapticConfig = field(default_factory=MatrixHapticConfig)
    vibration: VibrationHapticConfig = field(default_factory=VibrationHapticConfig)

    def __post_init__(self) -> None:
        _bool_value(self.enabled, "enabled")

    @property
    def matrix_enabled(self) -> bool:
        return bool(self.enabled and self.matrix.enabled)

    @property
    def vibration_enabled(self) -> bool:
        return bool(self.enabled and self.vibration.enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "matrix": self.matrix.to_dict(),
            "vibration": self.vibration.to_dict(),
        }


def default_haptic_config() -> HapticConfig:
    """Return disabled-by-default Stage 1 haptic configuration."""

    return HapticConfig()


def load_haptic_config(path: str | Path | None) -> HapticConfig:
    """Load a haptic config from JSON or YAML with strict key validation."""

    if path is None:
        return default_haptic_config()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"haptic config not found: {config_path}")
    suffix = config_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    elif suffix in {".yaml", ".yml"}:
        payload = _load_yaml(config_path)
    else:
        raise ValueError("haptic config must be .json, .yaml, or .yml")
    if not isinstance(payload, dict):
        raise ValueError("haptic config must be an object.")
    return haptic_config_from_dict(payload)


def haptic_config_from_dict(payload: dict[str, Any]) -> HapticConfig:
    """Validate and normalize a plain haptic config payload."""

    allowed = {"enabled", "matrix", "vibration"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown haptic config keys: {', '.join(unknown)}")
    matrix_payload = _object_value(payload.get("matrix", {}), "matrix")
    vibration_payload = _object_value(payload.get("vibration", {}), "vibration")
    return HapticConfig(
        enabled=_bool_value(payload.get("enabled", False), "enabled"),
        matrix=_matrix_config_from_dict(matrix_payload),
        vibration=_vibration_config_from_dict(vibration_payload),
    )


def _matrix_config_from_dict(payload: dict[str, Any]) -> MatrixHapticConfig:
    allowed = {
        "enabled",
        "required",
        "host",
        "port",
        "connect_timeout_s",
        "send_timeout_s",
        "startup_settle_seconds",
        "max_queue_size",
        "latest_only",
        "feedback_mode",
        "resend_interval_ms",
        "direction_semantics",
        "direction_channel_map",
        "combination_channel_map",
        "missing_combination_policy",
        "ignore_direction_axes",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown matrix haptic config keys: {', '.join(unknown)}")
    return MatrixHapticConfig(
        enabled=_bool_value(payload.get("enabled", False), "matrix.enabled"),
        required=_bool_value(payload.get("required", True), "matrix.required"),
        host=str(payload.get("host", "")),
        port=payload.get("port", MatrixHapticConfig.port),
        connect_timeout_s=payload.get(
            "connect_timeout_s",
            MatrixHapticConfig.connect_timeout_s,
        ),
        send_timeout_s=payload.get("send_timeout_s", MatrixHapticConfig.send_timeout_s),
        startup_settle_seconds=payload.get(
            "startup_settle_seconds",
            MatrixHapticConfig.startup_settle_seconds,
        ),
        max_queue_size=payload.get("max_queue_size", MatrixHapticConfig.max_queue_size),
        latest_only=_bool_value(
            payload.get("latest_only", MatrixHapticConfig.latest_only),
            "matrix.latest_only",
        ),
        feedback_mode=str(payload.get("feedback_mode", MatrixHapticConfig.feedback_mode)),
        resend_interval_ms=payload.get(
            "resend_interval_ms",
            MatrixHapticConfig.resend_interval_ms,
        ),
        direction_semantics=str(
            payload.get("direction_semantics", MatrixHapticConfig.direction_semantics)
        ),
        direction_channel_map=payload.get("direction_channel_map", {}),
        combination_channel_map=payload.get("combination_channel_map", {}),
        missing_combination_policy=str(
            payload.get(
                "missing_combination_policy",
                MatrixHapticConfig.missing_combination_policy,
            )
        ),
        ignore_direction_axes=payload.get("ignore_direction_axes", ()),
    )


def _vibration_config_from_dict(payload: dict[str, Any]) -> VibrationHapticConfig:
    allowed = {
        "enabled",
        "host",
        "port",
        "protocol",
        "enable_contact",
        "enable_release",
        "enable_slip",
        "enable_slip_pinch_insufficient",
        "enable_slip_track_blocked",
        "enable_slip_track_blocked_in_target_region",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown vibration haptic config keys: {', '.join(unknown)}")
    return VibrationHapticConfig(
        enabled=_bool_value(payload.get("enabled", False), "vibration.enabled"),
        host=str(payload.get("host", "")),
        port=payload.get("port", VibrationHapticConfig.port),
        protocol=str(payload.get("protocol", VibrationHapticConfig.protocol)),
        enable_contact=_bool_value(
            payload.get("enable_contact", VibrationHapticConfig.enable_contact),
            "vibration.enable_contact",
        ),
        enable_release=_bool_value(
            payload.get("enable_release", VibrationHapticConfig.enable_release),
            "vibration.enable_release",
        ),
        enable_slip=_bool_value(
            payload.get("enable_slip", VibrationHapticConfig.enable_slip),
            "vibration.enable_slip",
        ),
        enable_slip_pinch_insufficient=_bool_value(
            payload.get(
                "enable_slip_pinch_insufficient",
                VibrationHapticConfig.enable_slip_pinch_insufficient,
            ),
            "vibration.enable_slip_pinch_insufficient",
        ),
        enable_slip_track_blocked=_bool_value(
            payload.get(
                "enable_slip_track_blocked",
                VibrationHapticConfig.enable_slip_track_blocked,
            ),
            "vibration.enable_slip_track_blocked",
        ),
        enable_slip_track_blocked_in_target_region=_bool_value(
            payload.get(
                "enable_slip_track_blocked_in_target_region",
                VibrationHapticConfig.enable_slip_track_blocked_in_target_region,
            ),
            "vibration.enable_slip_track_blocked_in_target_region",
        ),
    )


def _direction_channel_map(value: Any) -> dict[str, list[int]]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("matrix.direction_channel_map must be an object.")
    unknown = sorted(set(value) - set(DIRECTION_KEYS))
    if unknown:
        raise ValueError(
            "matrix.direction_channel_map has unknown directions: "
            + ", ".join(unknown)
        )
    normalized: dict[str, list[int]] = {}
    for direction in DIRECTION_KEYS:
        channels = value.get(direction, [])
        if not isinstance(channels, list):
            raise ValueError(f"matrix.direction_channel_map.{direction} must be a list.")
        normalized[direction] = [_channel_value(ch, direction) for ch in channels]
    return normalized


def _combination_channel_map(value: Any) -> dict[str, list[int]]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("matrix.combination_channel_map must be an object.")

    normalized: dict[str, list[int]] = {}
    for raw_key, raw_channels in value.items():
        key = normalize_direction_key(raw_key, name="matrix.combination_channel_map key")
        parts = key.split("+")
        if len(parts) < 2:
            raise ValueError(
                f"matrix.combination_channel_map.{key} must contain at least two directions."
            )
        axes = [part.split("_", 1)[0] for part in parts]
        if len(set(axes)) != len(axes):
            raise ValueError(
                f"matrix.combination_channel_map.{key} must not contain duplicate axes."
            )
        if not isinstance(raw_channels, list):
            raise ValueError(f"matrix.combination_channel_map.{key} must be a list.")
        if key in normalized:
            raise ValueError(f"matrix.combination_channel_map has duplicate key after normalization: {key}")
        normalized[key] = [
            _channel_value(ch, key, field_name="matrix.combination_channel_map")
            for ch in raw_channels
        ]
    return normalized


def _axis_list(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        value = ()
    if isinstance(value, str):
        raise ValueError(f"{name} must be a list of axes.")
    try:
        raw_axes = list(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be a list of axes.") from exc
    axes: list[str] = []
    for raw_axis in raw_axes:
        if not isinstance(raw_axis, str):
            raise ValueError(f"{name} axes must be strings.")
        axis = raw_axis.strip().upper()
        if axis not in AXIS_KEYS:
            raise ValueError(f"{name} axes must be one of: " + ", ".join(AXIS_KEYS))
        if axis not in axes:
            axes.append(axis)
    return tuple(sorted(axes, key=AXIS_KEYS.index))


def normalize_direction_key(value: Any, *, name: str = "direction key") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    parts = [part.strip().upper() for part in value.split("+") if part.strip()]
    if not parts:
        raise ValueError(f"{name} must contain at least one direction.")
    unknown = sorted(set(parts) - set(DIRECTION_KEYS))
    if unknown:
        raise ValueError(f"{name} has unknown directions: " + ", ".join(unknown))
    if len(set(parts)) != len(parts):
        raise ValueError(f"{name} must not contain duplicate directions.")
    return "+".join(sorted(parts, key=_direction_sort_key))


def _direction_sort_key(direction: str) -> tuple[int, int]:
    axis = direction.split("_", 1)[0]
    sign = direction.split("_", 1)[1]
    axis_order = {"X": 0, "Y": 1, "Z": 2}
    sign_order = {"POS": 0, "NEG": 1}
    return axis_order[axis], sign_order[sign]


def _channel_value(
    value: Any,
    direction: str,
    *,
    field_name: str = "matrix.direction_channel_map",
) -> int:
    if not isinstance(value, int):
        raise ValueError(
            f"{field_name}.{direction} channels must be integers."
        )
    if value < 0 or value > 127:
        raise ValueError(
            f"{field_name}.{direction} channel must be in 0..127."
        )
    return value


def _object_value(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return value


def _bool_value(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be true or false.")


def _port_value(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer port.") from exc
    if result <= 0 or result > 65535:
        raise ValueError(f"{name} must be in 1..65535.")
    return result


def _positive_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if result <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return result


def _positive_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be > 0.")
    return result


def _non_negative_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be >= 0.")
    return result


def _finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number.")
    return result


def _load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "YAML haptic config requires PyYAML. Install with: pip install PyYAML"
        ) from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))
