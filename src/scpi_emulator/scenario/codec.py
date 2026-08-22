"""Versioned JSON and compressed-binary scenario codecs."""

from __future__ import annotations

import base64
import gzip
import json
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .model import (
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioFormatError,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)


BINARY_MAGIC = b"SCPI-SCENARIO\x00\x01"


def load_scenario(path: str | Path) -> ScenarioDefinition:
    """Load JSON or the versioned compressed binary container from a file."""
    return load_scenario_bytes(Path(path).read_bytes())


def load_scenario_bytes(data: bytes) -> ScenarioDefinition:
    if data.startswith(BINARY_MAGIC):
        try:
            data = gzip.decompress(data[len(BINARY_MAGIC) :])
        except (gzip.BadGzipFile, EOFError, OSError) as error:
            raise ScenarioFormatError(f"invalid binary scenario container: {error}") from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScenarioFormatError("scenario is neither UTF-8 JSON nor a binary container") from error
    return loads_scenario(text)


def loads_scenario(text: str) -> ScenarioDefinition:
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise ScenarioFormatError(f"invalid scenario JSON: {error}") from error
    return _parse_definition(raw)


def dumps_scenario(definition: ScenarioDefinition, *, indent: int | None = 2) -> str:
    return json.dumps(_encode_definition(definition), indent=indent) + (
        "\n" if indent is not None else ""
    )


def dump_scenario_binary(definition: ScenarioDefinition) -> bytes:
    """Create a deterministic gzip-compressed scenario container."""
    payload = dumps_scenario(definition, indent=None).encode("utf-8")
    return BINARY_MAGIC + gzip.compress(payload, mtime=0)


def _parse_definition(raw: Any) -> ScenarioDefinition:
    if not isinstance(raw, dict):
        raise ScenarioFormatError("scenario root must be an object")
    if raw.get("schema_version") != 1:
        raise ScenarioFormatError("scenario schema_version must be 1")
    name = _text(raw, "name", "scenario")
    seed = raw.get("seed", 0)
    streams_raw = raw.get("streams")
    if not isinstance(streams_raw, dict) or not streams_raw:
        raise ScenarioFormatError("scenario streams must be a non-empty object")
    streams = tuple(_parse_stream(stream_name, value) for stream_name, value in streams_raw.items())
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ScenarioFormatError("scenario metadata must be an object")
    return ScenarioDefinition(
        name=name,
        description=raw.get("description", ""),
        seed=seed,
        metadata=_decode_value(metadata),
        streams=streams,
    )


def _parse_stream(name: str, raw: Any) -> ScenarioStream:
    if not isinstance(raw, dict):
        raise ScenarioFormatError(f"stream {name!r} must be an object")
    try:
        kind = StreamKind(raw.get("kind"))
        advance = AdvancePolicy(raw.get("advance", AdvancePolicy.READ.value))
        end = EndPolicy(raw.get("end", EndPolicy.ERROR.value))
    except ValueError as error:
        raise ScenarioFormatError(f"stream {name!r} has an unknown policy or kind: {error}") from error
    samples_raw = raw.get("samples")
    if not isinstance(samples_raw, list) or not samples_raw:
        raise ScenarioFormatError(f"stream {name!r} samples must be a non-empty array")
    samples: list[ScenarioSample] = []
    for index, sample in enumerate(samples_raw):
        if not isinstance(sample, dict) or "value" not in sample:
            raise ScenarioFormatError(f"stream {name!r} sample {index} requires a value")
        samples.append(
            ScenarioSample(
                _decode_value(sample["value"]),
                at_seconds=sample.get("at", 0.0),
                label=sample.get("label"),
            )
        )
    return ScenarioStream(
        name=name,
        kind=kind,
        samples=tuple(samples),
        advance=advance,
        end=end,
        description=raw.get("description", ""),
    )


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_decode_value(item) for item in value)
    if not isinstance(value, dict):
        return value
    if set(value) == {"$complex"}:
        pair = value["$complex"]
        if not isinstance(pair, list) or len(pair) != 2:
            raise ScenarioFormatError("$complex requires [real, imaginary]")
        return complex(*pair)
    if set(value) == {"$bytes"}:
        return _decode_base64(value["$bytes"], "$bytes")
    if set(value) == {"$binary"}:
        return _decode_numeric_binary(value["$binary"])
    return {str(key): _decode_value(item) for key, item in value.items()}


def _decode_numeric_binary(raw: Any) -> tuple[int | float | complex, ...]:
    if not isinstance(raw, dict):
        raise ScenarioFormatError("$binary must be an object")
    dtype = raw.get("dtype")
    byte_order = raw.get("byte_order", "big")
    formats = {
        "int16": "h",
        "int32": "i",
        "float32": "f",
        "float64": "d",
        "complex64": "f",
        "complex128": "d",
    }
    if dtype not in formats:
        raise ScenarioFormatError(f"unsupported $binary dtype {dtype!r}")
    if byte_order not in {"big", "little"}:
        raise ScenarioFormatError("$binary byte_order must be 'big' or 'little'")
    payload = _decode_base64(raw.get("data"), "$binary data")
    code = formats[dtype]
    width = struct.calcsize(code)
    if len(payload) % width:
        raise ScenarioFormatError(f"$binary payload length is not aligned for {dtype}")
    prefix = ">" if byte_order == "big" else "<"
    values = struct.unpack(f"{prefix}{len(payload) // width}{code}", payload)
    if dtype.startswith("complex"):
        if len(values) % 2:
            raise ScenarioFormatError(f"$binary {dtype} requires real/imaginary pairs")
        return tuple(complex(values[index], values[index + 1]) for index in range(0, len(values), 2))
    return tuple(values)


def _decode_base64(value: Any, field_name: str) -> bytes:
    if not isinstance(value, str):
        raise ScenarioFormatError(f"{field_name} must be a base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ScenarioFormatError(f"{field_name} is invalid base64") from error


def _encode_definition(definition: ScenarioDefinition) -> dict[str, Any]:
    return {
        "schema_version": definition.schema_version,
        "name": definition.name,
        "description": definition.description,
        "seed": definition.seed,
        "metadata": _encode_value(definition.metadata),
        "streams": {
            stream.name: {
                "kind": stream.kind.value,
                "advance": stream.advance.value,
                "end": stream.end.value,
                "description": stream.description,
                "samples": [
                    {
                        "value": _encode_value(sample.value),
                        "at": sample.at_seconds,
                        **({"label": sample.label} if sample.label is not None else {}),
                    }
                    for sample in stream.samples
                ],
            }
            for stream in definition.streams
        },
    }


def _encode_value(value: Any) -> Any:
    if isinstance(value, complex):
        return {"$complex": [value.real, value.imag]}
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        return {str(key): _encode_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    return value


def _text(raw: dict[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioFormatError(f"{context} {key} must be a non-empty string")
    return value
