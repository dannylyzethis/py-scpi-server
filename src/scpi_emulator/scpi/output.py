"""SCPI output queue, response framing, and binary data formatting."""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass
from enum import Enum
from threading import RLock

from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
)
from .status import StatusSystem


class OutputQueueFull(BufferError):
    """A response cannot fit in the configured output queue."""


@dataclass(frozen=True)
class BinaryResponse:
    data: bytes
    definite: bool = True

    def encode(self) -> bytes:
        if not self.definite:
            return b"#0" + self.data
        length = str(len(self.data)).encode("ascii")
        if len(length) > 9:
            raise ValueError("IEEE definite block payload is too large")
        return b"#" + str(len(length)).encode("ascii") + length + self.data


class ByteOrder(Enum):
    NORMAL = "NORM"
    SWAPPED = "SWAP"


class DataEncoding(Enum):
    ASCII = "ASC"
    REAL32 = "REAL,32"
    REAL64 = "REAL,64"
    INTEGER16 = "INT,16"
    INTEGER32 = "INT,32"


@dataclass
class DataFormat:
    encoding: DataEncoding = DataEncoding.REAL64
    byte_order: ByteOrder = ByteOrder.NORMAL

    def configure(self, kind: str, bits: int | None = None) -> None:
        kind = kind.upper()
        if kind in ("ASC", "ASCII"):
            if bits is not None:
                raise ValueError("ASCII data format does not accept a width")
            self.encoding = DataEncoding.ASCII
            return
        if kind == "REAL" and bits in (32, 64):
            self.encoding = DataEncoding.REAL32 if bits == 32 else DataEncoding.REAL64
            return
        if kind in ("INT", "INTEGER") and bits in (16, 32):
            self.encoding = DataEncoding.INTEGER16 if bits == 16 else DataEncoding.INTEGER32
            return
        raise ValueError(f"unsupported data format {kind},{bits}")

    def encode_values(self, values) -> str | BinaryResponse:
        values = tuple(values)
        if self.encoding is DataEncoding.ASCII:
            return ",".join(str(value) for value in values)
        format_character = {
            DataEncoding.REAL32: "f",
            DataEncoding.REAL64: "d",
            DataEncoding.INTEGER16: "h",
            DataEncoding.INTEGER32: "i",
        }[self.encoding]
        prefix = ">" if self.byte_order is ByteOrder.NORMAL else "<"
        payload = struct.pack(f"{prefix}{len(values)}{format_character}", *values)
        return BinaryResponse(payload)


class OutputQueue:
    """Thread-safe byte queue whose occupancy drives the MAV status bit."""

    def __init__(self, status: StatusSystem, capacity: int = 64 * 1024 * 1024) -> None:
        if capacity < 1:
            raise ValueError("output queue capacity must be at least one byte")
        self.status = status
        self.capacity = capacity
        self._chunks: deque[memoryview] = deque()
        self._byte_count = 0
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return self._byte_count

    @property
    def message_available(self) -> bool:
        return bool(len(self))

    def enqueue(
        self,
        response: str | bytes | bytearray | memoryview | BinaryResponse,
        *,
        terminate: bool = True,
    ) -> int:
        data = _response_bytes(response)
        if terminate:
            data += b"\n"
        with self._lock:
            if self._byte_count + len(data) > self.capacity:
                raise OutputQueueFull(
                    f"response requires {len(data)} bytes with "
                    f"{self.capacity - self._byte_count} available"
                )
            if data:
                self._chunks.append(memoryview(data))
                self._byte_count += len(data)
                self.status.set_output_queue_count(self._byte_count)
            return len(data)

    def read(self, maximum: int | None = None) -> bytes:
        if maximum is not None and maximum < 0:
            raise ValueError("maximum read size cannot be negative")
        with self._lock:
            remaining = self._byte_count if maximum is None else min(maximum, self._byte_count)
            output = bytearray()
            while remaining and self._chunks:
                chunk = self._chunks[0]
                take = min(remaining, len(chunk))
                output.extend(chunk[:take])
                remaining -= take
                self._byte_count -= take
                if take == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[take:]
            self.status.set_output_queue_count(self._byte_count)
            return bytes(output)

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._byte_count = 0
            self.status.set_output_queue_count(0)


def register_format_commands(
    registry: CommandRegistry, data_format: DataFormat
) -> None:
    format_path = (HeaderNode("FORMat"), HeaderNode("DATA"))
    registry.register(
        CommandSpec(
            path=format_path,
            parameters=(
                ParameterSpec(
                    ParameterType.ENUM,
                    "data format",
                    choices=("ASC", "ASCII", "REAL", "INT", "INTEGER"),
                ),
                ParameterSpec(ParameterType.INTEGER, "width", required=False, default=None),
            ),
            handler=lambda invocation, kind, bits: _configure_response(
                data_format, kind, bits
            ),
        )
    )
    registry.register(
        CommandSpec(
            path=format_path,
            handler=lambda invocation: data_format.encoding.value,
            query=True,
        )
    )
    border_path = (HeaderNode("FORMat"), HeaderNode("BORDer"))
    registry.register(
        CommandSpec(
            path=border_path,
            parameters=(
                ParameterSpec(
                    ParameterType.ENUM,
                    "byte order",
                    choices=("NORM", "NORMAL", "SWAP", "SWAPPED"),
                ),
            ),
            handler=lambda invocation, value: _border_response(data_format, value),
        )
    )
    registry.register(
        CommandSpec(
            path=border_path,
            handler=lambda invocation: data_format.byte_order.value,
            query=True,
        )
    )


def _response_bytes(response) -> bytes:
    if isinstance(response, BinaryResponse):
        return response.encode()
    if isinstance(response, str):
        return response.encode("utf-8")
    if isinstance(response, (bytes, bytearray, memoryview)):
        return bytes(response)
    raise TypeError(f"unsupported response type: {type(response).__name__}")


def _configure_response(data_format: DataFormat, kind: str, bits: int | None) -> str:
    data_format.configure(kind, bits)
    return ""


def _border_response(data_format: DataFormat, value: str) -> str:
    data_format.byte_order = (
        ByteOrder.NORMAL if value in ("NORM", "NORMAL") else ByteOrder.SWAPPED
    )
    return ""
