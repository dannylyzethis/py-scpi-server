"""Immutable definitions for deterministic instrument scenarios."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any


class ScenarioError(Exception):
    """Base class for scenario definition and playback errors."""


class ScenarioFormatError(ScenarioError, ValueError):
    """Raised when serialized scenario data violates the schema."""


class StreamNotFound(ScenarioError, KeyError):
    """Raised when a scenario does not contain the requested stream."""


class StreamNotReady(ScenarioError):
    """Raised when a timed sample is read before its scheduled time."""


class StreamExhausted(ScenarioError):
    """Raised when a stream with the explicit error end policy is exhausted."""


class StreamKind(str, Enum):
    SCALAR = "scalar"
    TRACE = "trace"
    TABLE = "table"
    EVENT = "event"
    ERROR = "error"


class AdvancePolicy(str, Enum):
    READ = "read"
    TRIGGER = "trigger"
    OPERATION = "operation"
    MANUAL = "manual"


class EndPolicy(str, Enum):
    HOLD_LAST = "hold-last"
    LOOP = "loop"
    ERROR = "error"


@dataclass(frozen=True)
class ScenarioSample:
    """One immutable value scheduled relative to scenario reset."""

    value: Any
    at_seconds: float = 0.0
    label: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.at_seconds, bool) or not isinstance(self.at_seconds, (int, float)):
            raise ScenarioFormatError("sample at_seconds must be numeric")
        at_seconds = float(self.at_seconds)
        if not math.isfinite(at_seconds) or at_seconds < 0:
            raise ScenarioFormatError("sample at_seconds must be finite and non-negative")
        if self.label is not None and (not isinstance(self.label, str) or not self.label.strip()):
            raise ScenarioFormatError("sample label must be a non-empty string")
        object.__setattr__(self, "at_seconds", at_seconds)
        object.__setattr__(self, "value", freeze_value(self.value))


@dataclass(frozen=True)
class ScenarioStream:
    """An ordered stream and its consumption/end policies."""

    name: str
    kind: StreamKind
    samples: tuple[ScenarioSample, ...]
    advance: AdvancePolicy = AdvancePolicy.READ
    end: EndPolicy = EndPolicy.ERROR
    description: str = ""

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "samples", tuple(self.samples))
        except TypeError as error:
            raise ScenarioFormatError(f"stream {self.name!r} samples must be iterable") from error
        _require_name(self.name, "stream name")
        if not isinstance(self.kind, StreamKind):
            raise ScenarioFormatError(f"stream {self.name!r} kind must be a StreamKind")
        if not isinstance(self.advance, AdvancePolicy):
            raise ScenarioFormatError(f"stream {self.name!r} advance must be an AdvancePolicy")
        if not isinstance(self.end, EndPolicy):
            raise ScenarioFormatError(f"stream {self.name!r} end must be an EndPolicy")
        if not self.samples:
            raise ScenarioFormatError(f"stream {self.name!r} requires at least one sample")
        if self.description and not isinstance(self.description, str):
            raise ScenarioFormatError(f"stream {self.name!r} description must be a string")
        previous = -1.0
        for sample in self.samples:
            if not isinstance(sample, ScenarioSample):
                raise ScenarioFormatError(f"stream {self.name!r} samples must be ScenarioSample values")
            if sample.at_seconds < previous:
                raise ScenarioFormatError(
                    f"stream {self.name!r} sample times must be non-decreasing"
                )
            previous = sample.at_seconds
            _validate_value(self.name, self.kind, sample.value)


@dataclass(frozen=True)
class ScenarioDefinition:
    """A versioned collection of named streams sharing one seed and clock."""

    name: str
    streams: tuple[ScenarioStream, ...]
    seed: int = 0
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "streams", tuple(self.streams))
        except TypeError as error:
            raise ScenarioFormatError("scenario streams must be iterable") from error
        _require_name(self.name, "scenario name")
        if self.schema_version != 1:
            raise ScenarioFormatError(f"unsupported scenario schema version {self.schema_version!r}")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ScenarioFormatError("scenario seed must be an integer")
        if not self.streams:
            raise ScenarioFormatError("scenario requires at least one stream")
        names = tuple(stream.name for stream in self.streams)
        if len(names) != len(set(names)):
            raise ScenarioFormatError("scenario stream names must be unique")
        if self.description and not isinstance(self.description, str):
            raise ScenarioFormatError("scenario description must be a string")
        if not isinstance(self.metadata, Mapping):
            raise ScenarioFormatError("scenario metadata must be an object")
        object.__setattr__(self, "metadata", freeze_value(self.metadata))

    def stream(self, name: str) -> ScenarioStream:
        for stream in self.streams:
            if stream.name == name:
                return stream
        raise StreamNotFound(name)


def freeze_value(value: Any) -> Any:
    """Recursively freeze mutable scenario values for safe shared playback."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return value


def _validate_value(name: str, kind: StreamKind, value: Any) -> None:
    if kind is StreamKind.SCALAR:
        if isinstance(value, (tuple, Mapping)):
            raise ScenarioFormatError(f"scalar stream {name!r} requires scalar sample values")
        return
    if kind is StreamKind.TRACE:
        if not isinstance(value, tuple) or not all(_is_numeric(item) for item in value):
            raise ScenarioFormatError(f"trace stream {name!r} requires a numeric vector")
        return
    if kind is StreamKind.TABLE:
        if not isinstance(value, tuple) or not value or not all(
            isinstance(row, tuple) for row in value
        ):
            raise ScenarioFormatError(f"table stream {name!r} requires rows")
        widths = {len(row) for row in value}
        if len(widths) != 1:
            raise ScenarioFormatError(f"table stream {name!r} rows must have equal width")
        return
    if not isinstance(value, Mapping):
        raise ScenarioFormatError(f"{kind.value} stream {name!r} requires object sample values")


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float, complex)) and not isinstance(value, bool)


def _require_name(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioFormatError(f"{field_name} must be a non-empty string")
