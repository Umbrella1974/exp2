"""Configuration loading and validation for non-hardware cue generation."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CueConfig:
    """Effective cue generation behavior.

    The runtime sink is intentionally not part of this configuration. A replay
    can reuse cue generation semantics without reusing the original display
    modality.
    """

    enable_contact_cue: bool = True
    enable_contact_exit_cue: bool = True
    enable_slip_cue: bool = True
    enable_blocked_directional_cue: bool = True
    min_cue_interval_ms: float = 0.0
    repeat_policy: str = "edge_only"
    message_language: str = "en"

    def __post_init__(self) -> None:
        for name in (
            "enable_contact_cue",
            "enable_contact_exit_cue",
            "enable_slip_cue",
            "enable_blocked_directional_cue",
        ):
            _bool_value(getattr(self, name), name)
        object.__setattr__(
            self,
            "min_cue_interval_ms",
            _non_negative_float(self.min_cue_interval_ms, "min_cue_interval_ms"),
        )
        if self.repeat_policy != "edge_only":
            raise ValueError('repeat_policy must be "edge_only".')
        if self.message_language != "en":
            raise ValueError('message_language must be "en".')

    def to_dict(self) -> dict[str, Any]:
        """Return the normalized JSON-safe effective config."""

        return asdict(self)


def default_cue_config() -> CueConfig:
    """Return default cue generation settings."""

    return CueConfig()


def load_cue_config(path: str | Path | None) -> CueConfig:
    """Load a cue config from JSON or YAML without silent fallback."""

    if path is None:
        return default_cue_config()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"cue config not found: {config_path}")
    suffix = config_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    elif suffix in {".yaml", ".yml"}:
        payload = _load_yaml(config_path)
    else:
        raise ValueError("cue config must be .json, .yaml, or .yml")
    if not isinstance(payload, dict):
        raise ValueError("cue config must be an object.")
    return cue_config_from_dict(payload)


def cue_config_from_dict(payload: dict[str, Any]) -> CueConfig:
    """Validate and normalize a plain cue config payload."""

    normalized = dict(payload)
    legacy_language = normalized.pop("console_message_language", None)
    if legacy_language is not None:
        if "message_language" in normalized:
            raise ValueError(
                "cue config cannot contain both message_language and console_message_language."
            )
        normalized["message_language"] = legacy_language

    allowed = {
        "enable_contact_cue",
        "enable_contact_exit_cue",
        "enable_slip_cue",
        "enable_blocked_directional_cue",
        "min_cue_interval_ms",
        "repeat_policy",
        "message_language",
    }
    unknown = sorted(set(normalized) - allowed)
    if unknown:
        raise ValueError(f"unknown cue config keys: {', '.join(unknown)}")

    config = CueConfig(
        enable_contact_cue=_bool_value(
            normalized.get("enable_contact_cue", CueConfig.enable_contact_cue),
            "enable_contact_cue",
        ),
        enable_contact_exit_cue=_bool_value(
            normalized.get("enable_contact_exit_cue", CueConfig.enable_contact_exit_cue),
            "enable_contact_exit_cue",
        ),
        enable_slip_cue=_bool_value(
            normalized.get("enable_slip_cue", CueConfig.enable_slip_cue),
            "enable_slip_cue",
        ),
        enable_blocked_directional_cue=_bool_value(
            normalized.get(
                "enable_blocked_directional_cue",
                CueConfig.enable_blocked_directional_cue,
            ),
            "enable_blocked_directional_cue",
        ),
        min_cue_interval_ms=_non_negative_float(
            normalized.get("min_cue_interval_ms", CueConfig.min_cue_interval_ms),
            "min_cue_interval_ms",
        ),
        repeat_policy=str(normalized.get("repeat_policy", CueConfig.repeat_policy)),
        message_language=str(normalized.get("message_language", CueConfig.message_language)),
    )
    if config.repeat_policy != "edge_only":
        raise ValueError('repeat_policy must be "edge_only".')
    if config.message_language != "en":
        raise ValueError('message_language must be "en".')
    return config


def _load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("YAML cue config requires PyYAML. Install with: pip install PyYAML") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _bool_value(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be true or false.")


def _non_negative_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number.") from exc
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number.")
    return result
