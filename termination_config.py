"""Trial termination configuration for live integrated sessions."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TerminationConfig:
    """Protective trial termination settings."""

    max_trial_duration_seconds: float = 600.0
    max_detach_count: int = 20
    manual_completion_enabled: bool = True
    timeout_enabled: bool = True
    detach_limit_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_termination_config() -> TerminationConfig:
    """Return the default protective termination settings."""

    return TerminationConfig()


def load_termination_config(path: str | Path | None) -> TerminationConfig:
    """Load a termination config from JSON or YAML."""

    if path is None:
        return default_termination_config()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"termination config not found: {config_path}")
    suffix = config_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    elif suffix in {".yaml", ".yml"}:
        payload = _load_yaml(config_path)
    else:
        raise ValueError("termination config must be .json, .yaml, or .yml")
    if not isinstance(payload, dict):
        raise ValueError("termination config must be an object.")
    return termination_config_from_dict(payload)


def termination_config_from_dict(payload: dict[str, Any]) -> TerminationConfig:
    """Validate and normalize a plain termination config payload."""

    allowed = {
        "max_trial_duration_seconds",
        "max_detach_count",
        "manual_completion_enabled",
        "timeout_enabled",
        "detach_limit_enabled",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown termination config keys: {', '.join(unknown)}")
    config = TerminationConfig(
        max_trial_duration_seconds=_positive_float(
            payload.get("max_trial_duration_seconds", TerminationConfig.max_trial_duration_seconds),
            "max_trial_duration_seconds",
        ),
        max_detach_count=_non_negative_int(
            payload.get("max_detach_count", TerminationConfig.max_detach_count),
            "max_detach_count",
        ),
        manual_completion_enabled=_bool_value(
            payload.get("manual_completion_enabled", TerminationConfig.manual_completion_enabled),
            "manual_completion_enabled",
        ),
        timeout_enabled=_bool_value(
            payload.get("timeout_enabled", TerminationConfig.timeout_enabled),
            "timeout_enabled",
        ),
        detach_limit_enabled=_bool_value(
            payload.get("detach_limit_enabled", TerminationConfig.detach_limit_enabled),
            "detach_limit_enabled",
        ),
    )
    return config


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("YAML termination config requires PyYAML. Install with: pip install PyYAML") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _positive_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number.") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive number.")
    return result


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be a non-negative integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer.") from exc
    if result < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return result


def _bool_value(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be true or false.")
