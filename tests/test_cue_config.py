"""Tests for cue config loading and normalization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cue_config import cue_config_from_dict, load_cue_config


def test_cue_config_normalizes_legacy_message_language_alias() -> None:
    config = cue_config_from_dict({"console_message_language": "en"})

    assert config.message_language == "en"
    assert "console_message_language" not in config.to_dict()


def test_cue_config_rejects_unknown_keys_and_invalid_repeat_policy() -> None:
    with pytest.raises(ValueError, match="unknown cue config keys"):
        cue_config_from_dict({"enable_slpi_cue": True})

    with pytest.raises(ValueError, match="edge_only"):
        cue_config_from_dict({"repeat_policy": "periodic"})


def test_load_cue_config_json(tmp_path: Path) -> None:
    path = tmp_path / "cue.json"
    path.write_text(json.dumps({"min_cue_interval_ms": 25}), encoding="utf-8")

    config = load_cue_config(path)

    assert config.min_cue_interval_ms == pytest.approx(25.0)
