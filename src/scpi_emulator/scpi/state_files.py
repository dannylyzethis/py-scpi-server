"""VNA composition and selected power persistence through named MMEM files."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from .active_device import VNAActiveDeviceSystem
from .measurements import MAX_CHANNEL, MAX_TRACE, MAX_WINDOW, VNAMeasurementSystem
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)
from .sweeps import VNASweepSystem

_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9_. -]{1,128}\Z")


class VNAStateFileStore:
    """Save composition plus source and gain-compression power settings."""

    def __init__(
        self,
        measurements: VNAMeasurementSystem,
        sweeps: VNASweepSystem,
        active_device: VNAActiveDeviceSystem,
        instrument_id: str,
        root: str | Path | None = None,
    ) -> None:
        self.measurements = measurements
        self.sweeps = sweeps
        self.active_device = active_device
        base = Path(root) if root is not None else Path.cwd() / ".scpi-state"
        self.directory = base / _safe_instrument_id(instrument_id)

    def store(self, filename: str) -> None:
        path = self._path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _serialize(self.measurements, self.sweeps, self.active_device)
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
        composition = _validate(raw, self.sweeps.capabilities.ports)
        _restore(self.measurements, self.sweeps, self.active_device, composition)

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


def register_state_file_commands(registry: CommandRegistry, store: VNAStateFileStore) -> None:
    """Register named VNA save/recall commands."""
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


def _serialize(
    state: VNAMeasurementSystem,
    sweeps: VNASweepSystem,
    active_device: VNAActiveDeviceSystem,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
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
        "power": [
            {
                "channel": channel.number,
                "start": channel.power_start,
                "stop": channel.power_stop,
                "ports": [
                    {"port": port, "level": level}
                    for port, level in sorted(channel.port_power.items())
                ],
            }
            for channel in sorted(sweeps.channels.values(), key=lambda item: item.number)
        ],
        "compression_power": [
            {
                "channel": channel,
                "start": settings.power_start,
                "stop": settings.power_stop,
                "linear": settings.compression_power,
            }
            for channel, settings in sorted(active_device.gain_channels.items())
        ],
    }


def _validate(raw: Any, maximum_port: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        _invalid("invalid root fields")
    version = raw.get("schema_version")
    expected = {"schema_version", "channels", "windows"}
    if version == 2:
        expected |= {"power", "compression_power"}
    elif version != 1:
        _invalid("unsupported schema version")
    _object(raw, expected, "root")
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
    normalized_power = []
    normalized_compression = []
    if version == 2:
        power_channels: set[int] = set()
        for power in _list(raw["power"], "power"):
            _object(power, {"channel", "start", "stop", "ports"}, "power")
            channel = _number(power["channel"], 1, MAX_CHANNEL, "power channel")
            if channel in power_channels:
                _invalid("duplicate power channel")
            power_channels.add(channel)
            ports = []
            port_numbers: set[int] = set()
            for port in _list(power["ports"], "power ports"):
                _object(port, {"port", "level"}, "port power")
                number = _number(port["port"], 1, maximum_port, "port")
                if number in port_numbers:
                    _invalid("duplicate power port")
                port_numbers.add(number)
                ports.append({"port": number, "level": _power(port["level"], "port power")})
            normalized_power.append(
                {
                    "channel": channel,
                    "start": _power(power["start"], "power start"),
                    "stop": _power(power["stop"], "power stop"),
                    "ports": ports,
                }
            )

        compression_channels: set[int] = set()
        for compression in _list(raw["compression_power"], "compression power"):
            _object(compression, {"channel", "start", "stop", "linear"}, "compression power")
            channel = _number(compression["channel"], 1, MAX_CHANNEL, "compression channel")
            if channel in compression_channels:
                _invalid("duplicate compression channel")
            compression_channels.add(channel)
            start = _power(compression["start"], "compression power start")
            stop = _power(compression["stop"], "compression power stop")
            if start > stop:
                _invalid("compression power start exceeds stop")
            normalized_compression.append(
                {
                    "channel": channel,
                    "start": start,
                    "stop": stop,
                    "linear": _power(compression["linear"], "compression linear power"),
                }
            )
    return {
        "schema_version": version,
        "channels": normalized_channels,
        "windows": normalized_windows,
        "power": normalized_power,
        "compression_power": normalized_compression,
    }


def _restore(
    state: VNAMeasurementSystem,
    sweeps: VNASweepSystem,
    active_device: VNAActiveDeviceSystem,
    composition: dict[str, Any],
) -> None:
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
    if composition["schema_version"] == 1:
        return
    with sweeps._lock:
        sweeps.channels.clear()
        for power in composition["power"]:
            channel = sweeps.channel(power["channel"])
            channel.power_start = power["start"]
            channel.power_stop = power["stop"]
            channel.port_power = {item["port"]: item["level"] for item in power["ports"]}
            sweeps._synchronize(channel.number)
        if not sweeps.channels:
            sweeps.channel(1)
            sweeps._synchronize(1)
    active_device.gain_channels.clear()
    for compression in composition["compression_power"]:
        settings = active_device.gain(compression["channel"])
        settings.power_start = compression["start"]
        settings.power_stop = compression["stop"]
        settings.compression_power = compression["linear"]


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


def _power(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not -120 <= value <= 50
    ):
        _invalid(f"invalid {label}")
    return float(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"invalid {label}")
    return value.strip()


def _invalid(reason: str) -> None:
    raise SCPICommandError(-257, f"File name error; invalid state file: {reason}")
