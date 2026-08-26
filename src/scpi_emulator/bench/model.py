"""Immutable virtual-bench definitions and resource addresses."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


class BenchError(Exception):
    """Base class for bench schema, composition, and runtime failures."""


class BenchFormatError(BenchError, ValueError):
    """Raised when a bench definition is malformed or internally contradictory."""


class BenchCompositionError(BenchError):
    """Raised when a valid bench cannot be composed from the available drivers."""


class BenchStartError(BenchError):
    """Raised when a composed bench cannot be started transactionally."""


@dataclass(frozen=True)
class ResourceAddress:
    """A transport, host, and port assigned to one instrument instance."""

    transport: str
    host: str
    port: int

    def __post_init__(self) -> None:
        _identifier(self.transport, "resource transport")
        _text(self.host, "resource host")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise BenchFormatError("resource port must be an integer")
        if not 1 <= self.port <= 65535:
            raise BenchFormatError("resource port must be between 1 and 65535")

    @property
    def endpoint(self) -> tuple[str, str, int]:
        return self.transport.casefold(), self.host.casefold(), self.port

    def render(self, template: str, *, host: str | None = None) -> str:
        selected_host = self.host if host is None else host
        _text(selected_host, "resource host override")
        try:
            return template.format(host=selected_host, port=self.port)
        except (KeyError, ValueError) as error:
            raise BenchFormatError(f"invalid resource template {template!r}: {error}") from error


@dataclass(frozen=True)
class BenchInstrument:
    """One named driver/model instance in a reusable bench file."""

    id: str
    driver: str
    model: str
    resource: ResourceAddress
    name: str | None = None
    firmware: str | None = None
    serial_number: str | None = None
    reported_model: str | None = None
    configuration: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.id, "instrument id")
        _identifier(self.driver, "instrument driver")
        _identifier(self.model, "instrument model")
        if not isinstance(self.resource, ResourceAddress):
            raise BenchFormatError(f"instrument {self.id!r} resource is invalid")
        if self.name is not None:
            _text(self.name, "instrument name")
        if self.firmware is not None:
            _text(self.firmware, "instrument firmware")
        if self.serial_number is not None:
            _text(self.serial_number, "instrument serial number")
        if self.reported_model is not None:
            _identity_field(self.reported_model, "instrument reported_model")
        if not isinstance(self.configuration, Mapping):
            raise BenchFormatError(f"instrument {self.id!r} configuration must be an object")
        object.__setattr__(self, "configuration", _freeze(self.configuration))


@dataclass(frozen=True)
class BenchDefinition:
    """A versioned, fully validated multi-instrument composition."""

    name: str
    instruments: tuple[BenchInstrument, ...]
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 2

    def __post_init__(self) -> None:
        _text(self.name, "bench name")
        if self.schema_version != 2:
            raise BenchFormatError(f"unsupported bench schema version {self.schema_version!r}")
        try:
            instruments = tuple(self.instruments)
        except TypeError as error:
            raise BenchFormatError("bench instruments must be iterable") from error
        object.__setattr__(self, "instruments", instruments)
        if not instruments:
            raise BenchFormatError("bench requires at least one instrument")
        if self.description and not isinstance(self.description, str):
            raise BenchFormatError("bench description must be a string")
        if not isinstance(self.metadata, Mapping):
            raise BenchFormatError("bench metadata must be an object")
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        ids = tuple(instrument.id.casefold() for instrument in instruments)
        if len(ids) != len(set(ids)):
            raise BenchFormatError("bench instrument ids must be unique")
        endpoints = tuple(instrument.resource.endpoint for instrument in instruments)
        if len(endpoints) != len(set(endpoints)):
            raise BenchFormatError("bench resource addresses must be unique")

    def instrument(self, instrument_id: str) -> BenchInstrument:
        for instrument in self.instruments:
            if instrument.id.casefold() == instrument_id.casefold():
                return instrument
        raise BenchFormatError(f"bench has no instrument {instrument_id!r}")


def thaw(value: Any) -> Any:
    """Convert immutable bench configuration values back to ordinary JSON/Python values."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BenchFormatError(f"{field_name} must be a non-empty string")


def _identifier(value: str, field_name: str) -> None:
    _text(value, field_name)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is None:
        raise BenchFormatError(f"{field_name} contains unsupported characters: {value!r}")


def _identity_field(value: str, field_name: str) -> None:
    _text(value, field_name)
    if any(character in value for character in ",\r\n"):
        raise BenchFormatError(f"{field_name} cannot contain commas or line breaks")
