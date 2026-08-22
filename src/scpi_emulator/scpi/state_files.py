"""Existence-only PNA composition persistence through named MMEM files."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .measurements import MAX_CHANNEL, MAX_TRACE, MAX_WINDOW, PNAMeasurementSystem
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)


_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9_. -]{1,128}\Z")


class PNAStateFileStore:
    """Save and recall only channel, measurement, window, and trace existence."""

    def __init__(
        self,
        measurements: PNAMeasurementSystem,
        instrument_id: str,
        root: str | Path | None = None,
    ) -> None:
        self.measurements = measurements
        base = Path(root) if root is not None else Path.cwd() / ".scpi-state"
        self.directory = base / _safe_instrument_id(instrument_id)

    def store(self, filename: str) -> None:
        path = self._path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _serialize(self.measurements)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary.replace(path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise SCPICommandError(-256, f"File name not found; {filename}") from exc

    def load(self, filename: str) -> None:
        path = self._path(filename)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SCPICommandError(-256, f"File name not found; {filename}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SCPICommandError(-257, f"File name error; invalid state file {filename}") from exc
        composition = _validate(raw)
        _restore(self.measurements, composition)

    def catalog(self) -> str:
        if not self.directory.exists():
            return "EMPTY"
        names = sorted(path.name for path in self.directory.iterdir() if path.is_file())
        return f'"{",".join(names)}"' if names else "EMPTY"

    def delete(self, filename: str) -> None:
        path = self._path(filename)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise SCPICommandError(-256, f"File name not found; {filename}") from exc
        except OSError as exc:
            raise SCPICommandError(-257, f"File name error; {filename}") from exc

    def _path(self, filename: str) -> Path:
        if (
            not _SAFE_COMPONENT.fullmatch(filename)
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or ":" in filename
        ):
            raise SCPICommandError(-257, "File name error; unsafe state filename")
        return self.directory / filename


def register_state_file_commands(registry: CommandRegistry, store: PNAStateFileStore) -> None:
    """Register named PNA save/recall commands."""
    memory = HeaderNode("MMEMory")
    filename = (ParameterSpec(ParameterType.STRING, name="filename"),)

    def add(path, handler, *, query=False, parameters=()):
        registry.register(CommandSpec(tuple(path), handler, tuple(parameters), query=query))

    add(
        (memory, HeaderNode("STORe"), HeaderNode("STATe")),
        lambda inv, name: store.store(name) or "",
        parameters=filename,
    )
    add(
        (memory, HeaderNode("LOAD"), HeaderNode("STATe")),
        lambda inv, name: store.load(name) or "",
        parameters=filename,
    )
    add((memory, HeaderNode("CATalog")), lambda inv: store.catalog(), query=True)
    add(
        (memory, HeaderNode("DELete")),
        lambda inv, name: store.delete(name) or "",
        parameters=filename,
    )


def _serialize(state: PNAMeasurementSystem) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "channels": [
            {
                "number": channel.number,
                "measurements": [
                    {"name": measurement.name, "parameter": measurement.parameter}
                    for measurement in channel.measurements.values()
                ],
            }
            for channel in sorted(state.channels.values(), key=lambda item: item.number)
        ],
        "windows": [
            {
                "number": window.number,
                "traces": [
                    {"number": trace.number, "measurement": trace.measurement}
                    for trace in sorted(window.traces.values(), key=lambda item: item.number)
                ],
            }
            for window in sorted(state.windows.values(), key=lambda item: item.number)
        ],
    }


def _validate(raw: Any) -> dict[str, Any]:
    _object(raw, {"schema_version", "channels", "windows"}, "root")
    if raw["schema_version"] != 1:
        _invalid("unsupported schema version")
    channels = _list(raw["channels"], "channels")
    windows = _list(raw["windows"], "windows")
    names: set[str] = set()
    channel_numbers: set[int] = set()
    normalized_channels = []
    for channel in channels:
        _object(channel, {"number", "measurements"}, "channel")
        number = _number(channel["number"], 1, MAX_CHANNEL, "channel")
        if number in channel_numbers:
            _invalid("duplicate channel number")
        channel_numbers.add(number)
        normalized_measurements = []
        for measurement in _list(channel["measurements"], "measurements"):
            _object(measurement, {"name", "parameter"}, "measurement")
            name = _text(measurement["name"], "measurement name")
            parameter = _text(measurement["parameter"], "measurement parameter").upper()
            if name in names:
                _invalid("duplicate measurement name")
            names.add(name)
            normalized_measurements.append({"name": name, "parameter": parameter})
        normalized_channels.append({"number": number, "measurements": normalized_measurements})

    window_numbers: set[int] = set()
    normalized_windows = []
    for window in windows:
        _object(window, {"number", "traces"}, "window")
        number = _number(window["number"], 1, MAX_WINDOW, "window")
        if number in window_numbers:
            _invalid("duplicate window number")
        window_numbers.add(number)
        trace_numbers: set[int] = set()
        normalized_traces = []
        for trace in _list(window["traces"], "traces"):
            _object(trace, {"number", "measurement"}, "trace")
            trace_number = _number(trace["number"], 1, MAX_TRACE, "trace")
            measurement = _text(trace["measurement"], "trace measurement")
            if trace_number in trace_numbers:
                _invalid("duplicate trace number")
            if measurement not in names:
                _invalid("trace references an unknown measurement")
            trace_numbers.add(trace_number)
            normalized_traces.append({"number": trace_number, "measurement": measurement})
        normalized_windows.append({"number": number, "traces": normalized_traces})
    return {"channels": normalized_channels, "windows": normalized_windows}


def _restore(state: PNAMeasurementSystem, composition: dict[str, Any]) -> None:
    with state._lock:
        state.channels.clear()
        state.windows.clear()
        state.active_window = None
        for channel_data in composition["channels"]:
            channel = state.create_channel(channel_data["number"])
            for measurement in channel_data["measurements"]:
                state.define(channel.number, measurement["name"], measurement["parameter"])
            channel.selected = next(iter(channel.measurements), None)
        for window_data in composition["windows"]:
            window = state.create_window(window_data["number"])
            for trace in window_data["traces"]:
                state.feed(window.number, trace["number"], trace["measurement"])
        if state.windows:
            state.active_window = min(state.windows)
            window = state.windows[state.active_window]
            window.active_trace = min(window.traces, default=None)


def _safe_instrument_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return cleaned[:80] or "instrument"


def _object(value: Any, keys: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        _invalid(f"invalid {label} fields")


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _invalid(f"{label} must be a list")
    return value


def _number(value: Any, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _invalid(f"invalid {label} number")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"invalid {label}")
    return value.strip()


def _invalid(reason: str) -> None:
    raise SCPICommandError(-257, f"File name error; invalid state file: {reason}")
