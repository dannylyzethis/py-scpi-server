"""Stateful VNA channels, measurements, display traces, and markers."""

from __future__ import annotations

import cmath
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock
from typing import Callable

from .parser import NumericValue
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)

MAX_CHANNEL = 200
MAX_WINDOW = 24
MAX_TRACE = 24
MAX_MARKER = 15

DISPLAY_FORMATS = (
    "MLOGarithmic",
    "PHASe",
    "GDELay",
    "SLINear",
    "SLOGarithmic",
    "SCOMplex",
    "SMITh",
    "SADMittance",
    "POLar",
    "MLINear",
    "SWR",
    "REAL",
    "IMAGinary",
)
MATH_FUNCTIONS = ("NORMal", "ADD", "SUBTract", "MULTiply", "DIVide")
MARKER_FORMATS = (
    "DEFault",
    "MLINear",
    "MLOGarithmic",
    "PHASe",
    "REAL",
    "IMAGinary",
    "POLar",
    "GDELay",
)


@dataclass
class MarkerState:
    enabled: bool = False
    x: float = 1e9
    bucket: int = 0
    format: str = "DEFault"
    delta: bool = False


@dataclass
class MeasurementState:
    name: str
    parameter: str
    number: int
    format: str = "MLOGarithmic"
    markers: dict[int, MarkerState] = field(default_factory=dict)
    math_function: str = "NORMal"
    math_interpolate: bool = False
    memory: tuple[complex, ...] | None = None
    limit_enabled: bool = False
    limit_failed: bool = False
    equation_enabled: bool = False
    equation: str = ""
    stimulus: tuple[float, ...] = (1e9,)
    samples: tuple[complex, ...] = (0j,)

    def marker(self, number: int) -> MarkerState:
        if not 1 <= number <= MAX_MARKER:
            raise SCPICommandError(-222, "Data out of range; marker number")
        return self.markers.setdefault(number, MarkerState())


@dataclass
class ChannelState:
    number: int
    measurements: OrderedDict[str, MeasurementState] = field(default_factory=OrderedDict)
    selected: str | None = None
    next_measurement_number: int = 1


@dataclass
class TraceState:
    number: int
    measurement: str
    visible: bool = True
    title: str = ""


@dataclass
class WindowState:
    number: int
    traces: dict[int, TraceState] = field(default_factory=dict)
    active_trace: int | None = None


class VNAMeasurementSystem:
    """Own coherent VNA measurement and front-panel addressing state."""

    def __init__(self) -> None:
        self.channels: dict[int, ChannelState] = {}
        self.windows: dict[int, WindowState] = {}
        self.active_window: int | None = None
        self.axis_provider: Callable[[int], tuple[float, ...]] | None = None
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.channels.clear()
            self.windows.clear()
            channel = self.create_channel(1)
            measurement = self.define(1, "CH1_S11_1", "S11")
            window = self.create_window(1)
            window.traces[1] = TraceState(1, measurement.name)
            window.active_trace = 1
            self.active_window = 1
            channel.selected = measurement.name

    def inspect(self) -> dict[str, object]:
        """Return channel, measurement, window, and trace front-panel state."""
        with self._lock:
            channels = []
            for channel in sorted(self.channels.values(), key=lambda item: item.number):
                channels.append(
                    {
                        "number": channel.number,
                        "selected": channel.selected,
                        "measurements": [
                            {
                                "name": measurement.name,
                                "parameter": measurement.parameter,
                                "number": measurement.number,
                                "format": measurement.format,
                                "limit_enabled": measurement.limit_enabled,
                                "limit_failed": measurement.limit_failed,
                                "points": len(measurement.samples),
                            }
                            for measurement in channel.measurements.values()
                        ],
                    }
                )
            windows = [
                {
                    "number": window.number,
                    "active_trace": window.active_trace,
                    "traces": [
                        {
                            "number": trace.number,
                            "measurement": trace.measurement,
                            "visible": trace.visible,
                            "title": trace.title,
                        }
                        for trace in sorted(window.traces.values(), key=lambda item: item.number)
                    ],
                }
                for window in sorted(self.windows.values(), key=lambda item: item.number)
            ]
            return {
                "active_window": self.active_window,
                "channels": channels,
                "windows": windows,
            }

    def create_channel(self, number: int) -> ChannelState:
        _bounded(number, MAX_CHANNEL, "channel")
        with self._lock:
            return self.channels.setdefault(number, ChannelState(number))

    def delete_channel(self, number: int) -> None:
        _bounded(number, MAX_CHANNEL, "channel")
        with self._lock:
            channel = self.channels.pop(number, None)
            if channel is None:
                return
            names = set(channel.measurements)
            for window in self.windows.values():
                for trace_number in tuple(window.traces):
                    if window.traces[trace_number].measurement in names:
                        del window.traces[trace_number]
                if window.active_trace not in window.traces:
                    window.active_trace = min(window.traces, default=None)
            if not self.channels:
                self.create_channel(1)

    def channel(self, number: int, *, create: bool = False) -> ChannelState:
        _bounded(number, MAX_CHANNEL, "channel")
        with self._lock:
            channel = self.channels.get(number)
            if channel is None and create:
                channel = self.create_channel(number)
            if channel is None:
                raise SCPICommandError(-200, f"Execution error; channel {number} does not exist")
            return channel

    def define(self, channel_number: int, name: str, parameter: str) -> MeasurementState:
        name = _name(name, "measurement name")
        parameter = _parameter(parameter)
        with self._lock:
            if self.find(name) is not None:
                raise SCPICommandError(-200, f"Execution error; measurement {name!r} exists")
            channel = self.create_channel(channel_number)
            measurement = MeasurementState(
                name=name,
                parameter=parameter,
                number=channel.next_measurement_number,
            )
            if self.axis_provider is not None:
                measurement.stimulus = self.axis_provider(channel_number)
                measurement.samples = (0j,) * len(measurement.stimulus)
            channel.next_measurement_number += 1
            channel.measurements[name] = measurement
            if channel.selected is None:
                channel.selected = name
            return measurement

    def define_legacy(self, channel_number: int, parameter: str) -> MeasurementState:
        self.create_channel(channel_number)
        base = f"CH{channel_number}_{parameter.upper()}"
        suffix = 1
        while self.find(f"{base}_{suffix}") is not None:
            suffix += 1
        return self.define(channel_number, f"{base}_{suffix}", parameter)

    def find(self, name: str) -> tuple[ChannelState, MeasurementState] | None:
        for channel in self.channels.values():
            measurement = channel.measurements.get(name)
            if measurement is not None:
                return channel, measurement
        return None

    def select(self, channel_number: int, name: str) -> MeasurementState:
        channel = self.channel(channel_number)
        try:
            measurement = channel.measurements[name]
        except KeyError as exc:
            raise SCPICommandError(
                -200, f"Execution error; measurement {name!r} is not on channel {channel_number}"
            ) from exc
        channel.selected = name
        return measurement

    def select_number(self, channel_number: int, number: int) -> MeasurementState:
        channel = self.channel(channel_number)
        for measurement in channel.measurements.values():
            if measurement.number == number:
                channel.selected = measurement.name
                return measurement
        raise SCPICommandError(-200, f"Execution error; measurement number {number} does not exist")

    def selected(self, channel_number: int) -> MeasurementState:
        channel = self.channel(channel_number)
        if channel.selected is None or channel.selected not in channel.measurements:
            raise SCPICommandError(
                -200, f"Execution error; channel {channel_number} has no selection"
            )
        return channel.measurements[channel.selected]

    def delete(self, channel_number: int, name: str) -> None:
        channel = self.channel(channel_number)
        if name not in channel.measurements:
            raise SCPICommandError(-200, f"Execution error; measurement {name!r} does not exist")
        del channel.measurements[name]
        if channel.selected == name:
            channel.selected = next(iter(channel.measurements), None)
        for window in self.windows.values():
            for trace_number in tuple(window.traces):
                if window.traces[trace_number].measurement == name:
                    del window.traces[trace_number]
            if window.active_trace not in window.traces:
                window.active_trace = min(window.traces, default=None)

    def delete_all(self, channel_number: int) -> None:
        channel = self.channel(channel_number)
        for name in tuple(channel.measurements):
            self.delete(channel_number, name)

    def catalog(self, channel_number: int) -> str:
        channel = self.channel(channel_number)
        if not channel.measurements:
            return "EMPTY"
        fields = []
        for measurement in channel.measurements.values():
            fields.extend((measurement.name, measurement.parameter))
        return f'"{",".join(fields)}"'

    def create_window(self, number: int) -> WindowState:
        _bounded(number, MAX_WINDOW, "window")
        with self._lock:
            return self.windows.setdefault(number, WindowState(number))

    def set_window(self, number: int, enabled: bool) -> None:
        if enabled:
            self.create_window(number)
            self.active_window = number
        else:
            self.windows.pop(number, None)
            if self.active_window == number:
                self.active_window = min(self.windows, default=None)

    def feed(self, window_number: int, trace_number: int, name: str) -> TraceState:
        _bounded(trace_number, MAX_TRACE, "trace")
        found = self.find(name)
        if found is None:
            raise SCPICommandError(-200, f"Execution error; measurement {name!r} does not exist")
        channel, _ = found
        window = self.create_window(window_number)
        trace = TraceState(trace_number, name)
        window.traces[trace_number] = trace
        window.active_trace = trace_number
        self.active_window = window_number
        channel.selected = name
        return trace

    def trace(self, window_number: int, trace_number: int) -> TraceState:
        _bounded(window_number, MAX_WINDOW, "window")
        _bounded(trace_number, MAX_TRACE, "trace")
        window = self.windows.get(window_number)
        if window is None or trace_number not in window.traces:
            raise SCPICommandError(-200, "Execution error; display trace does not exist")
        return window.traces[trace_number]

    def select_trace(self, window_number: int, trace_number: int) -> None:
        trace = self.trace(window_number, trace_number)
        window = self.windows[window_number]
        window.active_trace = trace_number
        self.active_window = window_number
        found = self.find(trace.measurement)
        if found:
            found[0].selected = trace.measurement

    def active_context(self) -> tuple[int, MeasurementState] | None:
        if self.active_window is None:
            return None
        window = self.windows.get(self.active_window)
        if window is None or window.active_trace is None:
            return None
        trace = window.traces.get(window.active_trace)
        if trace is None:
            return None
        found = self.find(trace.measurement)
        return (found[0].number, found[1]) if found else None

    def measurement_window_trace(self, name: str) -> tuple[int, int] | None:
        for window in self.windows.values():
            for trace in window.traces.values():
                if trace.measurement == name:
                    return window.number, trace.number
        return None

    def marker_y(self, channel: int, marker_number: int) -> str:
        measurement = self.selected(channel)
        marker = measurement.marker(marker_number)
        samples = self._math_samples(measurement)
        if not samples:
            return "0,0"
        bucket = _nearest_bucket(measurement.stimulus, marker.x)
        marker.bucket = bucket
        value = samples[min(bucket, len(samples) - 1)]
        return _format_complex(value, marker.format, measurement.format)

    def marker_search(self, channel: int, marker_number: int, function: str) -> None:
        measurement = self.selected(channel)
        marker = measurement.marker(marker_number)
        samples = self._math_samples(measurement)
        if not samples:
            return
        magnitudes = [abs(value) for value in samples]
        bucket = magnitudes.index(max(magnitudes) if function == "MAX" else min(magnitudes))
        marker.bucket = bucket
        marker.x = measurement.stimulus[min(bucket, len(measurement.stimulus) - 1)]

    @staticmethod
    def _math_samples(measurement: MeasurementState) -> tuple[complex, ...]:
        data = measurement.samples
        memory = measurement.memory
        if measurement.math_function == "NORMal" or memory is None:
            return data
        result = []
        for value, stored in zip(data, memory):
            if measurement.math_function == "ADD":
                result.append(value + stored)
            elif measurement.math_function == "SUBTract":
                result.append(value - stored)
            elif measurement.math_function == "MULTiply":
                result.append(value * stored)
            else:
                result.append(value / stored if stored else complex(math.inf, 0))
        return tuple(result)


def register_measurement_commands(registry: CommandRegistry, state: VNAMeasurementSystem) -> None:
    """Register indexed VNA measurement and display workflows."""
    calc = HeaderNode("CALCulate", index="channel", index_default=1)
    display = HeaderNode("DISPlay")
    window = HeaderNode("WINDow", index="window", index_default=1)
    trace = HeaderNode("TRACe", index="trace", index_default=1)
    marker = HeaderNode("MARKer", index="marker", index_default=1)

    def channel_available(inv):
        return 1 <= inv.indices.get("channel", 1) <= MAX_CHANNEL

    def window_available(inv):
        return 1 <= inv.indices.get("window", 1) <= MAX_WINDOW

    def display_available(inv):
        return window_available(inv) and 1 <= inv.indices.get("trace", 1) <= MAX_TRACE

    def marker_available(inv):
        return channel_available(inv) and 1 <= inv.indices.get("marker", 1) <= MAX_MARKER

    def channel_exists(inv):
        return inv.indices.get("channel", 1) in state.channels

    def selected_measurement_exists(inv):
        channel = state.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def window_exists(inv):
        return inv.indices.get("window", 1) in state.windows

    def trace_exists(inv):
        window_state = state.windows.get(inv.indices.get("window", 1))
        return window_state is not None and inv.indices.get("trace", 1) in window_state.traces

    def add(path, handler, *, query=False, parameters=(), available=None, exists=None):
        registry.register(
            CommandSpec(
                tuple(path),
                handler,
                tuple(parameters),
                query=query,
                available=available,
                exists=exists,
            )
        )

    string = ParameterSpec(ParameterType.STRING)
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    integer = ParameterSpec(ParameterType.INTEGER, minimum=1)

    add(
        (calc, HeaderNode("PARameter"), HeaderNode("DEFine"), HeaderNode("EXTended")),
        lambda inv, name, parameter: state.define(inv.indices["channel"], name, parameter) and "",
        parameters=(string, string),
        available=channel_available,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("DEFine")),
        lambda inv, parameter: state.define_legacy(inv.indices["channel"], parameter) and "",
        parameters=(ParameterSpec(ParameterType.CHARACTER),),
        available=channel_available,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("CATalog"), HeaderNode("EXTended")),
        lambda inv: state.catalog(inv.indices["channel"]),
        query=True,
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("CATalog")),
        lambda inv: state.catalog(inv.indices["channel"]),
        query=True,
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("SELect")),
        lambda inv, name: state.select(inv.indices["channel"], name) and "",
        parameters=(string,),
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("MNUMber")),
        lambda inv, number: state.select_number(inv.indices["channel"], number) and "",
        parameters=(integer,),
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("MNUMber")),
        lambda inv: str(state.selected(inv.indices["channel"]).number),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("MODify"), HeaderNode("EXTended")),
        lambda inv, parameter: _modify(state.selected(inv.indices["channel"]), parameter),
        parameters=(string,),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("DELete")),
        lambda inv, name: state.delete(inv.indices["channel"], name) or "",
        parameters=(string,),
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("DELete"), HeaderNode("ALL")),
        lambda inv: state.delete_all(inv.indices["channel"]) or "",
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("WNUMber")),
        lambda inv: _window_or_zero(state, state.selected(inv.indices["channel"]).name, 0),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("TNUMber")),
        lambda inv: _window_or_zero(state, state.selected(inv.indices["channel"]).name, 1),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )

    add(
        (calc, HeaderNode("FORMat")),
        lambda inv, value: _setattr_response(
            state.selected(inv.indices["channel"]), "format", value
        ),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=DISPLAY_FORMATS),),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("FORMat")),
        lambda inv: state.selected(inv.indices["channel"]).format,
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )

    add(
        (calc, HeaderNode("MATH"), HeaderNode("FUNCtion")),
        lambda inv, value: _setattr_response(
            state.selected(inv.indices["channel"]), "math_function", value
        ),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=MATH_FUNCTIONS),),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("MATH"), HeaderNode("FUNCtion")),
        lambda inv: state.selected(inv.indices["channel"]).math_function,
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("MATH"), HeaderNode("MEMorize")),
        lambda inv: _memorize(state.selected(inv.indices["channel"])),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("MATH"), HeaderNode("INTerpolate")),
        lambda inv, value: _setattr_response(
            state.selected(inv.indices["channel"]), "math_interpolate", value
        ),
        parameters=(boolean,),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("MATH"), HeaderNode("INTerpolate")),
        lambda inv: _bool(state.selected(inv.indices["channel"]).math_interpolate),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )

    add(
        (calc, marker),
        lambda inv, value: _marker_enable(state, inv, value),
        parameters=(boolean,),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker),
        lambda inv: _marker_query(state, inv, "enabled"),
        query=True,
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("STATe")),
        lambda inv, value: _marker_enable(state, inv, value),
        parameters=(boolean,),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("STATe")),
        lambda inv: _marker_query(state, inv, "enabled"),
        query=True,
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("X")),
        lambda inv, value: _marker_x(state, inv, value),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"})),
        ),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("X")),
        lambda inv: _marker_query(state, inv, "x"),
        query=True,
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("Y")),
        lambda inv: state.marker_y(inv.indices["channel"], inv.indices["marker"]),
        query=True,
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("BUCKet")),
        lambda inv, value: _marker_bucket(state, inv, value),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=0),),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("BUCKet")),
        lambda inv: _marker_query(state, inv, "bucket"),
        query=True,
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("FORMat")),
        lambda inv, value: _marker_format(state, inv, value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=MARKER_FORMATS),),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("FORMat")),
        lambda inv: _marker_query(state, inv, "format"),
        query=True,
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, marker, HeaderNode("FUNCtion"), HeaderNode("EXECute")),
        lambda inv, value: (
            state.marker_search(inv.indices["channel"], inv.indices["marker"], value) or ""
        ),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("MAX", "MIN")),),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("MARKer"), HeaderNode("AOFF")),
        lambda inv: _markers_off(state.selected(inv.indices["channel"])),
        available=channel_available,
        exists=selected_measurement_exists,
    )

    add(
        (calc, HeaderNode("LIMit"), HeaderNode("STATe")),
        lambda inv, value: _setattr_response(
            state.selected(inv.indices["channel"]), "limit_enabled", value
        ),
        parameters=(boolean,),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("LIMit"), HeaderNode("STATe")),
        lambda inv: _bool(state.selected(inv.indices["channel"]).limit_enabled),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("LIMit"), HeaderNode("FAIL")),
        lambda inv: _bool(
            state.selected(inv.indices["channel"]).limit_enabled
            and state.selected(inv.indices["channel"]).limit_failed
        ),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("EQUation"), HeaderNode("TEXT")),
        lambda inv, value: _setattr_response(
            state.selected(inv.indices["channel"]), "equation", value
        ),
        parameters=(string,),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("EQUation"), HeaderNode("TEXT")),
        lambda inv: state.selected(inv.indices["channel"]).equation,
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("EQUation"), HeaderNode("STATe")),
        lambda inv, value: _setattr_response(
            state.selected(inv.indices["channel"]), "equation_enabled", value
        ),
        parameters=(boolean,),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("EQUation"), HeaderNode("STATe")),
        lambda inv: _bool(state.selected(inv.indices["channel"]).equation_enabled),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )

    add(
        (display, HeaderNode("CHANnel", index="channel", index_default=1), HeaderNode("STATe")),
        lambda inv, enabled: _channel_state(state, inv.indices["channel"], enabled),
        parameters=(boolean,),
        available=channel_available,
    )
    add(
        (display, HeaderNode("CHANnel", index="channel", index_default=1), HeaderNode("STATe")),
        lambda inv: _bool(inv.indices["channel"] in state.channels),
        query=True,
        available=channel_available,
    )
    add(
        (display, window, HeaderNode("STATe")),
        lambda inv, enabled: state.set_window(inv.indices["window"], enabled) or "",
        parameters=(boolean,),
        available=window_available,
    )
    add(
        (display, window, HeaderNode("STATe")),
        lambda inv: _bool(inv.indices["window"] in state.windows),
        query=True,
        available=window_available,
    )
    add(
        (display, HeaderNode("CATalog")),
        lambda inv: ",".join(str(number) for number in sorted(state.windows)) or "EMPTY",
        query=True,
    )
    add(
        (display, window, HeaderNode("CATalog")),
        lambda inv: _trace_catalog(state, inv.indices["window"]),
        query=True,
        available=window_available,
        exists=window_exists,
    )
    add(
        (display, window, trace, HeaderNode("FEED")),
        lambda inv, name: state.feed(inv.indices["window"], inv.indices["trace"], name) and "",
        parameters=(string,),
        available=display_available,
    )
    add(
        (display, window, trace, HeaderNode("FEED")),
        lambda inv: state.trace(inv.indices["window"], inv.indices["trace"]).measurement,
        query=True,
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace, HeaderNode("SELect")),
        lambda inv: state.select_trace(inv.indices["window"], inv.indices["trace"]) or "",
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace, HeaderNode("STATe")),
        lambda inv, enabled: _trace_visible(state, inv, enabled),
        parameters=(boolean,),
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace, HeaderNode("STATe")),
        lambda inv: _bool(state.trace(inv.indices["window"], inv.indices["trace"]).visible),
        query=True,
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace, HeaderNode("TITLe"), HeaderNode("DATA")),
        lambda inv, value: _trace_title(state, inv, value),
        parameters=(string,),
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace, HeaderNode("TITLe"), HeaderNode("DATA")),
        lambda inv: state.trace(inv.indices["window"], inv.indices["trace"]).title,
        query=True,
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, HeaderNode("TRACe"), HeaderNode("NEXT")),
        lambda inv: str(_next_trace(state, inv.indices["window"])),
        query=True,
        available=window_available,
        exists=window_exists,
    )

    add(
        (HeaderNode("SYSTem"), HeaderNode("ACTive"), HeaderNode("CHANnel")),
        lambda inv: str(state.active_context()[0] if state.active_context() else 0),
        query=True,
    )
    add(
        (HeaderNode("SYSTem"), HeaderNode("ACTive"), HeaderNode("MEASurement")),
        lambda inv: state.active_context()[1].name if state.active_context() else "",
        query=True,
    )


def _modify(measurement: MeasurementState, parameter: str) -> str:
    measurement.parameter = _parameter(parameter)
    return ""


def _setattr_response(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _memorize(measurement: MeasurementState) -> str:
    measurement.memory = tuple(measurement.samples)
    return ""


def _window_or_zero(state: VNAMeasurementSystem, name: str, item: int) -> str:
    result = state.measurement_window_trace(name)
    return str(result[item] if result else 0)


def _marker(state, invocation) -> MarkerState:
    return state.selected(invocation.indices["channel"]).marker(invocation.indices["marker"])


def _marker_enable(state, invocation, enabled: bool) -> str:
    _marker(state, invocation).enabled = enabled
    return ""


def _marker_query(state, invocation, name: str) -> str:
    value = getattr(_marker(state, invocation), name)
    return _bool(value) if isinstance(value, bool) else str(value)


def _marker_x(state, invocation, value: NumericValue) -> str:
    _marker(state, invocation).x = _numeric(value)
    return ""


def _marker_bucket(state, invocation, value: int) -> str:
    marker = _marker(state, invocation)
    measurement = state.selected(invocation.indices["channel"])
    if value >= len(measurement.samples):
        raise SCPICommandError(-222, "Data out of range; marker bucket")
    marker.bucket = value
    marker.x = measurement.stimulus[min(value, len(measurement.stimulus) - 1)]
    return ""


def _marker_format(state, invocation, value: str) -> str:
    _marker(state, invocation).format = value
    return ""


def _markers_off(measurement: MeasurementState) -> str:
    for marker in measurement.markers.values():
        marker.enabled = False
    return ""


def _channel_state(state: VNAMeasurementSystem, channel: int, enabled: bool) -> str:
    state.create_channel(channel) if enabled else state.delete_channel(channel)
    return ""


def _trace_visible(state, invocation, enabled: bool) -> str:
    state.trace(invocation.indices["window"], invocation.indices["trace"]).visible = enabled
    return ""


def _trace_title(state, invocation, value: str) -> str:
    state.trace(invocation.indices["window"], invocation.indices["trace"]).title = value
    return ""


def _trace_catalog(state: VNAMeasurementSystem, window: int) -> str:
    if window not in state.windows or not state.windows[window].traces:
        return "EMPTY"
    return ",".join(str(number) for number in sorted(state.windows[window].traces))


def _next_trace(state: VNAMeasurementSystem, window: int) -> int:
    used = set(state.windows.get(window, WindowState(window)).traces)
    available = next((number for number in range(1, MAX_TRACE + 1) if number not in used), None)
    if available is None:
        raise SCPICommandError(-200, "Execution error; display window has no free traces")
    return available


def _numeric(value: NumericValue) -> float:
    multiplier = {
        None: Decimal(1),
        "HZ": Decimal(1),
        "KHZ": Decimal(1_000),
        "MHZ": Decimal(1_000_000),
        "GHZ": Decimal(1_000_000_000),
    }[value.unit]
    return float(value.value * multiplier)


def _nearest_bucket(axis: tuple[float, ...], x: float) -> int:
    return min(range(len(axis)), key=lambda index: abs(axis[index] - x)) if axis else 0


def _format_complex(value: complex, marker_format: str, display_format: str) -> str:
    selected = display_format if marker_format == "DEFault" else marker_format
    if selected in ("POLar", "SLINear", "SCOMplex", "SMITh", "SADMittance"):
        return f"{value.real:.12g},{value.imag:.12g}"
    if selected == "REAL":
        result = value.real
    elif selected == "IMAGinary":
        result = value.imag
    elif selected == "PHASe":
        result = math.degrees(cmath.phase(value))
    elif selected in ("MLOGarithmic", "SLOGarithmic"):
        result = 20 * math.log10(abs(value)) if value else -math.inf
    elif selected == "SWR":
        magnitude = abs(value)
        result = (1 + magnitude) / (1 - magnitude) if magnitude < 1 else math.inf
    else:
        result = abs(value)
    return f"{result:.12g},0"


def _parameter(value: str) -> str:
    value = value.strip()
    if not value:
        raise SCPICommandError(-224, "Illegal parameter value; measurement parameter")
    return value.upper()


def _name(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise SCPICommandError(-224, f"Illegal parameter value; {label}")
    return value


def _bounded(value: int, maximum: int, label: str) -> None:
    if not 1 <= value <= maximum:
        raise SCPICommandError(-222, f"Data out of range; {label} number")


def _bool(value: bool) -> str:
    return "1" if value else "0"
