"""Versioned VNA command manifests and implementation coverage reporting."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .registry import CommandRegistry, CommandSpec


class ManifestError(ValueError):
    """Raised when a command manifest is structurally or semantically invalid."""


@dataclass(frozen=True)
class ManifestCommand:
    id: str
    syntax: str
    implementation_key: str
    models: frozenset[str]
    firmware_pattern: str
    parameters: tuple[Mapping[str, Any], ...]
    response: Mapping[str, Any]
    defaults: Mapping[str, Any]
    supersedes: tuple[str, ...]
    source_id: str


@dataclass(frozen=True)
class CommandManifest:
    schema_version: int
    snapshot: Mapping[str, str]
    sources: Mapping[str, Mapping[str, str]]
    commands: tuple[ManifestCommand, ...]

    def commands_for(self, model: str, firmware: str) -> tuple[ManifestCommand, ...]:
        return tuple(
            command
            for command in self.commands
            if model in command.models and re.fullmatch(command.firmware_pattern, firmware)
        )


def load_command_manifest(path: str | Path | None = None) -> CommandManifest:
    """Load and validate the packaged manifest or an explicitly supplied file."""
    if path is None:
        resource = files("scpi_emulator").joinpath("profiles/vna_commands.v1.json")
        raw = json.loads(resource.read_text(encoding="utf-8"))
    else:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _validate_manifest(raw)


def command_spec_key(specification: CommandSpec) -> str:
    """Render a stable implementation key from a typed command specification."""
    nodes = []
    for node in specification.path:
        rendered = node.mnemonic.upper()
        if node.index is not None:
            rendered += f"<{node.index.lower()}>"
        nodes.append(rendered)
    key = ":".join(nodes)
    return key + ("?" if specification.query else "")


def registry_implementation_keys(registry: CommandRegistry) -> frozenset[str]:
    return frozenset(command_spec_key(specification) for specification in registry.specifications)


def coverage_report(
    manifest: CommandManifest,
    implemented_keys: Iterable[str],
    *,
    model: str,
    firmware: str,
) -> dict[str, Any]:
    """Compare applicable documented commands with normalized implementation keys."""
    implemented = frozenset(key.upper() for key in implemented_keys)
    applicable = manifest.commands_for(model, firmware)
    if not applicable:
        raise ManifestError(f"manifest has no commands for {model} firmware {firmware}")

    documented = {command.implementation_key.upper(): command for command in applicable}
    present = sorted(command.id for key, command in documented.items() if key in implemented)
    missing = sorted(command.id for key, command in documented.items() if key not in implemented)
    undocumented = sorted(implemented - set(documented))
    total = len(documented)
    return {
        "schema_version": 1,
        "manifest_schema_version": manifest.schema_version,
        "snapshot": dict(manifest.snapshot),
        "target": {"model": model, "firmware": firmware},
        "summary": {
            "documented": total,
            "implemented": len(present),
            "missing": len(missing),
            "coverage_percent": round(100 * len(present) / total, 2),
        },
        "implemented_command_ids": present,
        "missing_command_ids": missing,
        "undocumented_implementation_keys": undocumented,
    }


def _validate_manifest(raw: Any) -> CommandManifest:
    if not isinstance(raw, dict):
        raise ManifestError("manifest root must be an object")
    if raw.get("schema_version") != 1:
        raise ManifestError("unsupported manifest schema_version")

    snapshot = _mapping(raw, "snapshot")
    for field in ("date", "contract_revision", "firmware_pattern"):
        _nonempty_string(snapshot, field, "snapshot")
    try:
        re.compile(snapshot["firmware_pattern"])
    except re.error as error:
        raise ManifestError(f"invalid snapshot firmware_pattern: {error}") from error

    sources = _mapping(raw, "sources")
    if not sources:
        raise ManifestError("manifest requires at least one source")
    for source_id, source in sources.items():
        if not isinstance(source, dict):
            raise ManifestError(f"source {source_id!r} must be an object")
        _nonempty_string(source, "title", f"source {source_id!r}")
        if set(source) != {"title"}:
            raise ManifestError(
                f"source {source_id!r} must contain only an internal contract title"
            )

    entries = raw.get("commands")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("manifest commands must be a non-empty array")
    commands = tuple(_validate_command(entry, snapshot, sources) for entry in entries)
    ids = [command.id for command in commands]
    keys = [command.implementation_key.upper() for command in commands]
    if len(ids) != len(set(ids)):
        raise ManifestError("manifest command ids must be unique")
    if len(keys) != len(set(keys)):
        raise ManifestError("manifest implementation keys must be unique")

    known_ids = set(ids)
    for command in commands:
        unknown = set(command.supersedes) - known_ids
        if unknown:
            raise ManifestError(f"command {command.id!r} supersedes unknown ids: {sorted(unknown)}")
    return CommandManifest(1, snapshot, sources, commands)


def _validate_command(
    entry: Any,
    snapshot: Mapping[str, str],
    sources: Mapping[str, Mapping[str, str]],
) -> ManifestCommand:
    if not isinstance(entry, dict):
        raise ManifestError("each command must be an object")
    command_id = _nonempty_string(entry, "id", "command")
    syntax = _nonempty_string(entry, "syntax", f"command {command_id!r}")
    key = _nonempty_string(entry, "implementation_key", f"command {command_id!r}")
    source_id = _nonempty_string(entry, "source_id", f"command {command_id!r}")
    if source_id not in sources:
        raise ManifestError(f"command {command_id!r} references unknown source {source_id!r}")

    models = entry.get("models")
    if not isinstance(models, list) or not models or not all(isinstance(v, str) for v in models):
        raise ManifestError(f"command {command_id!r} requires a non-empty models array")
    firmware_pattern = entry.get("firmware_pattern", snapshot["firmware_pattern"])
    if not isinstance(firmware_pattern, str):
        raise ManifestError(f"command {command_id!r} firmware_pattern must be a string")
    try:
        re.compile(firmware_pattern)
    except re.error as error:
        raise ManifestError(
            f"command {command_id!r} has invalid firmware_pattern: {error}"
        ) from error

    parameters = entry.get("parameters")
    response = entry.get("response")
    defaults = entry.get("defaults")
    supersedes = entry.get("supersedes")
    if not isinstance(parameters, list) or not all(isinstance(v, dict) for v in parameters):
        raise ManifestError(f"command {command_id!r} parameters must be an array of objects")
    if not isinstance(response, dict) or "type" not in response:
        raise ManifestError(f"command {command_id!r} response requires a type")
    if not isinstance(defaults, dict):
        raise ManifestError(f"command {command_id!r} defaults must be an object")
    if not isinstance(supersedes, list) or not all(isinstance(v, str) for v in supersedes):
        raise ManifestError(f"command {command_id!r} supersedes must be an array of ids")
    for parameter in parameters:
        if not {"name", "type"} <= set(parameter):
            raise ManifestError(f"command {command_id!r} parameter requires name and type")

    return ManifestCommand(
        command_id,
        syntax,
        key,
        frozenset(models),
        firmware_pattern,
        tuple(parameters),
        response,
        defaults,
        tuple(supersedes),
        source_id,
    )


def _mapping(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"manifest {key} must be an object")
    return value


def _nonempty_string(raw: Mapping[str, Any], key: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{context} {key} must be a non-empty string")
    return value
