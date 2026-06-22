"""Configuration for interaction-layer behavior switches."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


BOUNDARY_LOCK_SURFACE_MODES = ("primary",)


@dataclass(frozen=True)
class BoundaryLockConfig:
    """Boundary lock behavior for track-blocked block movement."""

    enabled: bool = False
    unlock_delta_m: float = 0.005
    contact_tolerance_m: float = 0.0
    surface_mode: str = "primary"

    def __post_init__(self) -> None:
        _bool_value(self.enabled, "boundary_lock.enabled")
        object.__setattr__(
            self,
            "unlock_delta_m",
            _positive_float(self.unlock_delta_m, "boundary_lock.unlock_delta_m"),
        )
        object.__setattr__(
            self,
            "contact_tolerance_m",
            _non_negative_float(
                self.contact_tolerance_m,
                "boundary_lock.contact_tolerance_m",
            ),
        )
        if self.surface_mode not in BOUNDARY_LOCK_SURFACE_MODES:
            raise ValueError(
                "boundary_lock.surface_mode must be one of: "
                + ", ".join(BOUNDARY_LOCK_SURFACE_MODES)
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InteractionConfig:
    """Top-level interaction config for experiment state-machine options."""

    boundary_lock: BoundaryLockConfig = field(default_factory=BoundaryLockConfig)

    def to_dict(self) -> dict[str, Any]:
        return {"boundary_lock": self.boundary_lock.to_dict()}


def default_interaction_config() -> InteractionConfig:
    """Return disabled-by-default interaction behavior."""

    return InteractionConfig()


def load_interaction_config(path: str | Path | None) -> InteractionConfig:
    """Load interaction config from JSON/YAML with strict key validation."""

    if path is None:
        return default_interaction_config()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"interaction config not found: {config_path}")
    suffix = config_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    elif suffix in {".yaml", ".yml"}:
        payload = _load_yaml(config_path)
    else:
        raise ValueError("interaction config must be .json, .yaml, or .yml")
    if not isinstance(payload, dict):
        raise ValueError("interaction config must be an object.")
    return interaction_config_from_dict(payload)


def interaction_config_from_dict(payload: dict[str, Any]) -> InteractionConfig:
    """Validate and normalize a plain interaction config payload."""

    allowed = {"boundary_lock"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown interaction config keys: {', '.join(unknown)}")
    return InteractionConfig(
        boundary_lock=_boundary_lock_from_dict(
            _object_value(payload.get("boundary_lock", {}), "boundary_lock")
        )
    )


def _boundary_lock_from_dict(payload: dict[str, Any]) -> BoundaryLockConfig:
    allowed = {
        "enabled",
        "unlock_delta_m",
        "contact_tolerance_m",
        "surface_mode",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown boundary_lock config keys: {', '.join(unknown)}")
    return BoundaryLockConfig(
        enabled=_bool_value(payload.get("enabled", False), "boundary_lock.enabled"),
        unlock_delta_m=payload.get(
            "unlock_delta_m",
            BoundaryLockConfig.unlock_delta_m,
        ),
        contact_tolerance_m=payload.get(
            "contact_tolerance_m",
            BoundaryLockConfig.contact_tolerance_m,
        ),
        surface_mode=str(payload.get("surface_mode", BoundaryLockConfig.surface_mode)),
    )


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
            "YAML interaction config requires PyYAML. Install with: pip install PyYAML"
        ) from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))
