"""Versioned JSON codec for reusable virtual-bench definitions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import (
    BenchDefinition,
    BenchFormatError,
    BenchInstrument,
    ResourceAddress,
    thaw,
)


def load_bench(path: str | Path) -> BenchDefinition:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BenchFormatError(f"could not read bench file {str(path)!r}: {error}") from error
    return loads_bench(text)


def loads_bench(text: str) -> BenchDefinition:
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise BenchFormatError(f"invalid bench JSON: {error}") from error
    return _parse_bench(raw)


def dumps_bench(definition: BenchDefinition, *, indent: int | None = 2) -> str:
    rendered = json.dumps(_encode_bench(definition), indent=indent)
    return rendered + ("\n" if indent is not None else "")


def save_bench(definition: BenchDefinition, path: str | Path) -> None:
    """Write one complete definition; callers choose their own atomic replacement policy."""
    try:
        Path(path).write_text(dumps_bench(definition), encoding="utf-8")
    except OSError as error:
        raise BenchFormatError(f"could not write bench file {str(path)!r}: {error}") from error


def _parse_bench(raw: Any) -> BenchDefinition:
    if not isinstance(raw, dict):
        raise BenchFormatError("bench root must be an object")
    if raw.get("schema_version") != 2:
        raise BenchFormatError("bench schema_version must be 2")
    name = _required_text(raw, "name", "bench")
    instruments_raw = raw.get("instruments")
    if not isinstance(instruments_raw, list) or not instruments_raw:
        raise BenchFormatError("bench instruments must be a non-empty array")
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise BenchFormatError("bench metadata must be an object")
    return BenchDefinition(
        name=name,
        description=raw.get("description", ""),
        metadata=metadata,
        instruments=tuple(
            _parse_instrument(item, index) for index, item in enumerate(instruments_raw)
        ),
    )


def _parse_instrument(raw: Any, index: int) -> BenchInstrument:
    context = f"instrument {index}"
    if not isinstance(raw, dict):
        raise BenchFormatError(f"{context} must be an object")
    resource = raw.get("resource")
    if not isinstance(resource, dict):
        raise BenchFormatError(f"{context} resource must be an object")
    configuration = raw.get("configuration", {})
    if not isinstance(configuration, dict):
        raise BenchFormatError(f"{context} configuration must be an object")
    return BenchInstrument(
        id=_required_text(raw, "id", context),
        driver=_required_text(raw, "driver", context),
        model=_required_text(raw, "model", context),
        name=raw.get("name"),
        firmware=raw.get("firmware"),
        serial_number=raw.get("serial_number"),
        reported_model=raw.get("reported_model"),
        configuration=configuration,
        resource=ResourceAddress(
            transport=_required_text(resource, "transport", f"{context} resource"),
            host=_required_text(resource, "host", f"{context} resource"),
            port=resource.get("port"),
        ),
    )


def _encode_bench(definition: BenchDefinition) -> dict[str, Any]:
    return {
        "schema_version": definition.schema_version,
        "name": definition.name,
        "description": definition.description,
        "metadata": thaw(definition.metadata),
        "instruments": [
            {
                "id": instrument.id,
                "name": instrument.name,
                "driver": instrument.driver,
                "model": instrument.model,
                "firmware": instrument.firmware,
                "serial_number": instrument.serial_number,
                "reported_model": instrument.reported_model,
                "configuration": thaw(instrument.configuration),
                "resource": {
                    "transport": instrument.resource.transport,
                    "host": instrument.resource.host,
                    "port": instrument.resource.port,
                },
            }
            for instrument in definition.instruments
        ],
    }


def _required_text(raw: dict[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BenchFormatError(f"{context} {key} must be a non-empty string")
    return value
