"""Tests for Stage 1 haptic configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from haptic_config import haptic_config_from_dict, load_haptic_config


def test_default_haptic_config_is_disabled() -> None:
    config = haptic_config_from_dict({})

    assert config.enabled is False
    assert config.matrix.enabled is False
    assert config.matrix.required is True
    assert config.matrix.direction_semantics == "blocked_surface"
    assert config.vibration.enabled is False


def test_haptic_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown haptic config keys"):
        haptic_config_from_dict({"mystery": True})

    with pytest.raises(ValueError, match="unknown matrix haptic config keys"):
        haptic_config_from_dict({"matrix": {"mystery": True}})


def test_matrix_enabled_requires_host() -> None:
    with pytest.raises(ValueError, match="matrix.host is required"):
        haptic_config_from_dict({"enabled": True, "matrix": {"enabled": True}})


def test_direction_semantics_and_channel_map_are_validated(tmp_path: Path) -> None:
    path = tmp_path / "haptic.json"
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "matrix": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "direction_semantics": "correction_direction",
                    "direction_channel_map": {"X_POS": [1, 2, 3]},
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_haptic_config(path)

    assert config.matrix.direction_semantics == "correction_direction"
    assert config.matrix.direction_channel_map["X_POS"] == [1, 2, 3]
    assert config.matrix.direction_channel_map["X_NEG"] == []


def test_channel_range_validation() -> None:
    with pytest.raises(ValueError, match="channel must be in 0..127"):
        haptic_config_from_dict(
            {
                "matrix": {
                    "direction_channel_map": {"X_NEG": [128]},
                }
            }
        )
