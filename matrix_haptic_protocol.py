"""Matrix electrotactile TCP packet encoding."""

from __future__ import annotations

from typing import Iterable


MATRIX_MAGIC = b"\xAA\x55\xAA\x55"
MATRIX_MAX_PAYLOAD_BYTES = 128
MATRIX_CHANNEL_MIN = 0
MATRIX_CHANNEL_MAX = 127


def encode_matrix_packet(payload: bytes) -> bytes:
    """Encode one Matrix ESP32 packet.

    Packet format:
    MAGIC(4B) + payload_length(1B) + payload(N<=128B) + checksum(1B)
    where checksum is ``sum(payload) & 0xFF``.
    """

    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes.")
    payload_bytes = bytes(payload)
    if len(payload_bytes) > MATRIX_MAX_PAYLOAD_BYTES:
        raise ValueError("matrix payload length must be <= 128 bytes.")
    checksum = sum(payload_bytes) & 0xFF
    return MATRIX_MAGIC + bytes([len(payload_bytes)]) + payload_bytes + bytes([checksum])


def channel_list_to_payload(channels: Iterable[int]) -> bytes:
    """Validate and encode an HV507 channel list as payload bytes."""

    payload = bytearray()
    for channel in channels:
        if not isinstance(channel, int):
            raise ValueError(f"matrix channel must be an integer: {channel!r}")
        if channel < MATRIX_CHANNEL_MIN or channel > MATRIX_CHANNEL_MAX:
            raise ValueError(
                f"matrix channel must be in 0..127, got {channel!r}."
            )
        payload.append(channel)
    return bytes(payload)


def encode_matrix_channel_packet(channels: Iterable[int]) -> bytes:
    """Validate a channel list and encode it as a Matrix ESP32 packet."""

    return encode_matrix_packet(channel_list_to_payload(channels))
