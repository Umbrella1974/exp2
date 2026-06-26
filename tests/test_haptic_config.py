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
    assert config.matrix.transport == "tcp_magic_v1"
    assert config.matrix.combination_channel_map == {}
    assert config.matrix.missing_combination_policy == "skip"
    assert config.matrix.ignore_direction_axes == ()
    assert config.matrix.contact_valid_feedback.enabled is False
    assert config.matrix.contact_valid_feedback.channel_list == ()
    assert config.matrix.pinch_insufficient_feedback.enabled is False
    assert config.matrix.reset_before_output_change.enabled is False
    assert config.matrix.reset_before_output_change.missing_reset_policy == "skip_reset"
    assert config.matrix.reset_before_output_change.reset_map == {}
    assert config.vibration.enabled is False
    assert config.vibration.transport == "tcp_line_int_v1"
    assert config.vibration.required is True
    assert config.vibration.port == 12346
    assert config.vibration.command_map == {
        "contact_enter": 1,
        "contact_exit": 2,
        "slip_start": 3,
        "slip_end": 4,
    }
    assert config.vibration.pinch_insufficient_slip_policy == "allow_loose_touch"


def test_haptic_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown haptic config keys"):
        haptic_config_from_dict({"mystery": True})

    with pytest.raises(ValueError, match="unknown matrix haptic config keys"):
        haptic_config_from_dict({"matrix": {"mystery": True}})

    with pytest.raises(ValueError, match="unknown vibration haptic config keys"):
        haptic_config_from_dict({"vibration": {"mystery": True}})


def test_matrix_enabled_requires_host() -> None:
    with pytest.raises(ValueError, match="matrix.host is required"):
        haptic_config_from_dict({"enabled": True, "matrix": {"enabled": True}})


def test_vibration_enabled_requires_host_and_validates_command_map() -> None:
    with pytest.raises(ValueError, match="vibration.host is required"):
        haptic_config_from_dict({"enabled": True, "vibration": {"enabled": True}})

    config = haptic_config_from_dict(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "192.168.1.30",
                "port": 12346,
                "command_map": {"contact_enter": 10, "slip_end": 40},
            },
        }
    )

    assert config.vibration_enabled is True
    assert config.vibration.command_map["contact_enter"] == 10
    assert config.vibration.command_map["contact_exit"] == 2
    assert config.vibration.command_map["slip_end"] == 40

    with pytest.raises(ValueError, match="1..255"):
        haptic_config_from_dict(
            {"vibration": {"command_map": {"contact_enter": 0}}}
        )

    with pytest.raises(ValueError, match="unknown labels"):
        haptic_config_from_dict(
            {"vibration": {"command_map": {"bad": 1}}}
        )


def test_legacy_vibration_protocol_key_is_transport_alias() -> None:
    config = haptic_config_from_dict({"vibration": {"protocol": "tcp_line_int_v1"}})

    assert config.vibration.transport == "tcp_line_int_v1"


def test_vibration_pinch_insufficient_slip_policy_is_validated() -> None:
    config = haptic_config_from_dict(
        {"vibration": {"pinch_insufficient_slip_policy": "requires_prior_grab"}}
    )

    assert config.vibration.pinch_insufficient_slip_policy == "requires_prior_grab"

    with pytest.raises(ValueError, match="pinch_insufficient_slip_policy"):
        haptic_config_from_dict(
            {"vibration": {"pinch_insufficient_slip_policy": "sometimes"}}
        )


def test_matrix_state_feedback_and_reset_config_are_normalized() -> None:
    config = haptic_config_from_dict(
        {
            "matrix": {
                "contact_valid_feedback": {
                    "enabled": True,
                    "channel_list": [1, 2],
                },
                "pinch_insufficient_feedback": {
                    "enabled": True,
                    "channel_list": [3],
                },
                "reset_before_output_change": {
                    "enabled": True,
                    "missing_reset_policy": "error",
                    "apply_on_transition_to_none": True,
                    "reset_map": {
                        "contact_valid": {"channel_list": [10], "hold_ms": 0},
                        "blocked:Y_NEG+X_POS": {"channel_list": [11]},
                    },
                },
            }
        }
    )

    assert config.matrix.contact_valid_feedback.channel_list == (1, 2)
    assert config.matrix.pinch_insufficient_feedback.channel_list == (3,)
    reset = config.matrix.reset_before_output_change
    assert reset.missing_reset_policy == "error"
    assert reset.apply_on_transition_to_none is True
    assert set(reset.reset_map) == {"contact_valid", "blocked:X_POS+Y_NEG"}
    assert reset.reset_map["blocked:X_POS+Y_NEG"].channel_list == (11,)


def test_matrix_reset_config_rejects_invalid_keys_and_nonzero_hold() -> None:
    with pytest.raises(ValueError, match="matrix reset_map key"):
        haptic_config_from_dict(
            {
                "matrix": {
                    "reset_before_output_change": {
                        "reset_map": {"mystery": {"channel_list": [1]}}
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="hold_ms must be 0"):
        haptic_config_from_dict(
            {
                "matrix": {
                    "reset_before_output_change": {
                        "reset_map": {
                            "contact_valid": {"channel_list": [1], "hold_ms": 5}
                        }
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="missing_reset_policy"):
        haptic_config_from_dict(
            {
                "matrix": {
                    "reset_before_output_change": {
                        "missing_reset_policy": "clear_all"
                    }
                }
            }
        )


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


def test_combination_channel_map_is_validated_and_normalized() -> None:
    config = haptic_config_from_dict(
        {
            "matrix": {
                "combination_channel_map": {
                    "Y_POS+X_POS": [20, 21],
                    "X_NEG+Y_POS+Z_NEG": [30],
                },
                "missing_combination_policy": "union_single_directions",
            }
        }
    )

    assert config.matrix.combination_channel_map == {
        "X_POS+Y_POS": [20, 21],
        "X_NEG+Y_POS+Z_NEG": [30],
    }
    assert config.matrix.missing_combination_policy == "union_single_directions"


def test_combination_channel_map_rejects_invalid_keys() -> None:
    with pytest.raises(ValueError, match="unknown directions"):
        haptic_config_from_dict(
            {"matrix": {"combination_channel_map": {"X_POS+BAD": [1]}}}
        )

    with pytest.raises(ValueError, match="at least two directions"):
        haptic_config_from_dict(
            {"matrix": {"combination_channel_map": {"X_POS": [1]}}}
        )

    with pytest.raises(ValueError, match="duplicate axes"):
        haptic_config_from_dict(
            {"matrix": {"combination_channel_map": {"X_POS+X_NEG": [1]}}}
        )


def test_ignore_direction_axes_are_validated_and_normalized() -> None:
    config = haptic_config_from_dict(
        {"matrix": {"ignore_direction_axes": ["z", "X", "Z"]}}
    )

    assert config.matrix.ignore_direction_axes == ("X", "Z")

    with pytest.raises(ValueError, match="one of"):
        haptic_config_from_dict({"matrix": {"ignore_direction_axes": ["A"]}})

    with pytest.raises(ValueError, match="list of axes"):
        haptic_config_from_dict({"matrix": {"ignore_direction_axes": "Z"}})


def test_channel_range_validation() -> None:
    with pytest.raises(ValueError, match="channel must be in 0..127"):
        haptic_config_from_dict(
            {
                "matrix": {
                    "direction_channel_map": {"X_NEG": [128]},
                }
            }
        )
