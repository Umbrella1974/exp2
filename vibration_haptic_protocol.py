"""Vibration ESP32 TCP line-integer command encoding."""

from __future__ import annotations


VIBRATION_COMMAND_MIN = 1
VIBRATION_COMMAND_MAX = 255


def encode_vibration_line_command(command: int) -> bytes:
    """Encode one vibration command as an ASCII integer plus newline."""

    if not isinstance(command, int):
        raise ValueError(f"vibration command must be an integer: {command!r}")
    if command < VIBRATION_COMMAND_MIN or command > VIBRATION_COMMAND_MAX:
        raise ValueError(
            f"vibration command must be in 1..255, got {command!r}."
        )
    return f"{command}\n".encode("ascii")


def vibration_payload_to_log_string(payload: bytes) -> str:
    """Return a CSV-safe escaped payload string such as ``3\\n``."""

    return payload.decode("ascii").replace("\n", "\\n")
