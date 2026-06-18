from __future__ import annotations

import pytest

from vibration_haptic_protocol import (
    encode_vibration_line_command,
    vibration_payload_to_log_string,
)


def test_encode_vibration_line_command_writes_ascii_integer_newline() -> None:
    payload = encode_vibration_line_command(3)

    assert payload == b"3\n"
    assert vibration_payload_to_log_string(payload) == "3\\n"
    assert payload.hex() == "330a"


def test_encode_vibration_line_command_validates_range() -> None:
    with pytest.raises(ValueError, match="1..255"):
        encode_vibration_line_command(0)

    with pytest.raises(ValueError, match="integer"):
        encode_vibration_line_command("3")  # type: ignore[arg-type]
