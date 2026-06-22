"""Tests for interaction-layer configuration."""

from __future__ import annotations

import pytest

from interaction_config import (
    default_interaction_config,
    interaction_config_from_dict,
)


def test_interaction_config_defaults_keep_boundary_lock_disabled() -> None:
    config = default_interaction_config()

    assert config.boundary_lock.enabled is False
    assert config.boundary_lock.unlock_delta_m == pytest.approx(0.005)
    assert config.boundary_lock.contact_tolerance_m == pytest.approx(0.0)
    assert config.boundary_lock.surface_mode == "primary"


def test_interaction_config_accepts_boundary_lock_values() -> None:
    config = interaction_config_from_dict(
        {
            "boundary_lock": {
                "enabled": True,
                "unlock_delta_m": 0.01,
                "contact_tolerance_m": 0.002,
                "surface_mode": "primary",
            }
        }
    )

    assert config.boundary_lock.enabled is True
    assert config.boundary_lock.unlock_delta_m == pytest.approx(0.01)
    assert config.boundary_lock.contact_tolerance_m == pytest.approx(0.002)


def test_interaction_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown boundary_lock config keys"):
        interaction_config_from_dict({"boundary_lock": {"mystery": True}})


def test_interaction_config_requires_positive_unlock_delta() -> None:
    with pytest.raises(ValueError, match="unlock_delta_m must be > 0"):
        interaction_config_from_dict({"boundary_lock": {"unlock_delta_m": 0.0}})
