"""Bounded, binary-aware framing primitives for raw SCPI sockets."""

from __future__ import annotations

import math
from dataclasses import dataclass


class SocketTransportError(Exception):
    """Base class for raw socket configuration and framing failures."""


class MessageTooLarge(SocketTransportError):
    """Raised before an input message can exceed its configured bound."""


@dataclass(frozen=True)
class SocketTransportConfig:
    """Per-server raw socket limits and termination behavior."""

    read_terminations: tuple[bytes, ...] = (b"\r\n", b"\n", b"\r")
    write_termination: bytes = b"\n"
    receive_chunk_size: int = 64 * 1024
    max_message_size: int = 16 * 1024 * 1024
    idle_frame_timeout: float | None = 0.3
    client_idle_timeout: float | None = None
    send_timeout: float = 10.0
    accept_poll_interval: float = 0.2
    backlog: int = 5

    def __post_init__(self) -> None:
        terms = tuple(bytes(term) for term in self.read_terminations)
        if not terms or any(not term for term in terms):
            raise ValueError("read terminations must contain non-empty byte strings")
        if len(terms) != len(set(terms)):
            raise ValueError("read terminations must be unique")
        object.__setattr__(self, "read_terminations", tuple(sorted(terms, key=len, reverse=True)))
        object.__setattr__(self, "write_termination", bytes(self.write_termination))
        for name in ("receive_chunk_size", "max_message_size", "backlog"):
            if isinstance(getattr(self, name), bool) or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("idle_frame_timeout", "client_idle_timeout"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number or None")
        for name in ("send_timeout", "accept_poll_interval"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")


class SocketMessageFramer:
    """Extract terminated messages without splitting definite binary-block payloads."""

    def __init__(self, config: SocketTransportConfig) -> None:
        self.config = config
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes | bytearray | memoryview) -> tuple[bytes, ...]:
        self._buffer.extend(data)
        messages: list[bytes] = []
        while True:
            boundary = self._find_boundary()
            if boundary is None:
                self._check_bound()
                break
            position, length = boundary
            if position > self.config.max_message_size:
                self._buffer.clear()
                raise MessageTooLarge(
                    f"SCPI message exceeds {self.config.max_message_size} byte input limit"
                )
            messages.append(bytes(self._buffer[:position]))
            del self._buffer[: position + length]
        return tuple(messages)

    def flush_unterminated(self) -> bytes | None:
        """Return a legacy timeout-delimited message while preserving the same size bound."""
        if not self._buffer:
            return None
        self._check_bound()
        message = bytes(self._buffer)
        self._buffer.clear()
        return message

    def clear(self) -> None:
        self._buffer.clear()

    def _check_bound(self) -> None:
        if len(self._buffer) > self.config.max_message_size:
            self._buffer.clear()
            raise MessageTooLarge(
                f"SCPI message exceeds {self.config.max_message_size} byte input limit"
            )

    def _find_boundary(self) -> tuple[int, int] | None:
        data = self._buffer
        index = 0
        quote: int | None = None
        while index < len(data):
            value = data[index]
            if quote is not None:
                if value == quote:
                    if index + 1 < len(data) and data[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if value in (ord('"'), ord("'")):
                quote = value
                index += 1
                continue
            if value == ord("#"):
                block_end = self._definite_block_end(index)
                if block_end is None:
                    return None
                if block_end > index + 1:
                    index = block_end
                    continue
            for terminator in self.config.read_terminations:
                if data[index : index + len(terminator)] == terminator:
                    return index, len(terminator)
            index += 1
        return None

    def _definite_block_end(self, marker: int) -> int | None:
        data = self._buffer
        if marker + 1 >= len(data):
            return None
        digit = data[marker + 1]
        if not ord("0") <= digit <= ord("9"):
            return marker + 1
        digit_count = digit - ord("0")
        if digit_count == 0:
            return marker + 1
        header_end = marker + 2 + digit_count
        if header_end > len(data):
            return None
        length_bytes = bytes(data[marker + 2 : header_end])
        if not length_bytes.isdigit():
            return marker + 1
        payload_length = int(length_bytes)
        block_end = header_end + payload_length
        if block_end > self.config.max_message_size:
            self._buffer.clear()
            raise MessageTooLarge(
                f"declared binary block exceeds {self.config.max_message_size} byte input limit"
            )
        if block_end > len(data):
            return None
        return block_end
