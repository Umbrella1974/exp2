"""Tests for protective termination config loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termination_config import default_termination_config, load_termination_config


def test_default_termination_config_values() -> None:
    config = default_termination_config()

    assert config.max_trial_duration_seconds == pytest.approx(600.0)
    assert config.max_detach_count == 20
    assert config.manual_completion_enabled is True
    assert config.timeout_enabled is True
    assert config.detach_limit_enabled is True


def test_load_termination_config_from_json(tmp_path: Path) -> None:
    path = tmp_path / "termination.json"
    path.write_text(
        json.dumps(
            {
                "max_trial_duration_seconds": 12.5,
                "max_detach_count": 3,
                "manual_completion_enabled": False,
                "timeout_enabled": True,
                "detach_limit_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    config = load_termination_config(path)

    assert config.max_trial_duration_seconds == pytest.approx(12.5)
    assert config.max_detach_count == 3
    assert config.manual_completion_enabled is False
    assert config.timeout_enabled is True
    assert config.detach_limit_enabled is False


def test_invalid_termination_config_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "termination.json"
    path.write_text(json.dumps({"max_detach_count": -1}), encoding="utf-8")

    with pytest.raises(ValueError, match="max_detach_count"):
        load_termination_config(path)


def test_fractional_detach_limit_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "termination.json"
    path.write_text(json.dumps({"max_detach_count": 1.5}), encoding="utf-8")

    with pytest.raises(ValueError, match="max_detach_count"):
        load_termination_config(path)
