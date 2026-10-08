"""Stateful VNA channels, measurements, display traces, and markers."""

from __future__ import annotations

import cmath
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
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
    discrete: bool = False
    coupling: bool = False
    coupling_method: str = "CHANnel"
    compression_level: float = 1.0
    function: str = "MAXimum"
    peak_excursion: float = 3.0
    peak_threshold: float = -100.0
    function_domain_enabled: bool = False
    function_domain_start: float = 0.0
    function_domain_stop: float = 0.0
    tracking: bool = False
    target: float = 0.0
    marker_type: str = "NORMal"


@dataclass
class LimitSegmentState:
    number: int
    stimulus_start: float = 0.0
    stimulus_stop: float = 0.0
    amplitude_start: float = 0.0
    amplitude_stop: float = 0.0
    segment_type: str = "UPPer"


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
    marker_bandwidth_enabled: bool = False
    marker_reference_enabled: bool = False
    marker_reference_x: float = 0.0
    marker_reference_y: float = 0.0
    smoothing_enabled: bool = False
    smoothing_aperture: float = 10.0
    smoothing_points: int = 3
    group_delay_frequency: float = 0.0
    group_delay_percent: float = 1.0
    group_delay_points: int = 3
    hold_type: str = "OFF"
    function_type: str = "MEAN"
    function_domain_enabled: bool = False
    function_domain_start: float = 0.0
    function_domain_stop: float = 0.0
    function_statistics_enabled: bool = False
    limit_display_enabled: bool = True
    limit_sound_enabled: bool = False
    limit_segments: dict[int, LimitSegmentState] = field(default_factory=dict)
    x_axis: str = "AUTO"
    x_axis_domain: str = "FREQuency"

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
    title_enabled: bool = False
    memory_enabled: bool = False
    scale_per_division: float = 10.0
    reference_level: float = 0.0
    reference_position: float = 5.0


@dataclass
class WindowState:
    number: int
    traces: dict[int, TraceState] = field(default_factory=dict)
    active_trace: int | None = None
    title: str = ""
    title_enabled: bool = False
    table_enabled: bool = False
    divisions: int = 10
    y_coupled: bool = False
    y_couple_method: str = "ALL"
    annotation_enabled: bool = True
    marker_annotation_enabled: bool = True
    marker_coupled: bool = False
    marker_number: int = 1
    marker_response_resolution: int = 3
    marker_stimulus_resolution: int = 6
    marker_size: str = "NORMal"
    marker_symbol: str = "TRIangle"
    marker_symbol_above: bool = True
    marker_visible: bool = True
    marker_x_position: float = 0.0
    marker_y_position: float = 0.0
    limit_x_position: float = 0.0
    limit_y_position: float = 0.0


class VNAMeasurementSystem:
    """Own coherent VNA measurement and front-panel addressing state."""

    def __init__(self) -> None:
        self.channels: dict[int, ChannelState] = {}
        self.windows: dict[int, WindowState] = {}
        self.active_window: int | None = None
        self.display_enabled = True
        self.display_visible = True
        self.display_update_enabled = True
        self.display_annotations_enabled = True
        self.frequency_annotation_enabled = True
        self.message_annotation_enabled = False
        self.frequency_significant_digits = 6
        self.power_spin_resolution = 0.1
        self.maximum_traces = MAX_TRACE
        self.grid_line_type = "SOLid"
        self.trace_y_couple_method = "ALL"
        self.channel_coupling_enabled = False
        self.channel_coupling_group = 1
        self.channel_parallel_enabled = False
        self.noise_parallel_enabled = False
        self.noise_parallel_group = 1
        self.noise_parallel_group_list = "1"
        self.system_abort_threshold = 0.0
        self.system_setting = ""
        self.axis_provider: Callable[[int], tuple[float, ...]] | None = None
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.channels.clear()
            self.windows.clear()
            self.display_enabled = True
            self.display_visible = True
            self.display_update_enabled = True
            self.display_annotations_enabled = True
            self.frequency_annotation_enabled = True
            self.message_annotation_enabled = False
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

    def trace_values(self, channel: int) -> tuple[float, ...]:
        measurement = self.selected(channel)
        return tuple(abs(value) for value in self._math_samples(measurement))

    def function_data(self, channel: int) -> str:
        measurement = self.selected(channel)
        values = list(self.trace_values(channel))
        if measurement.function_domain_enabled:
            values = [
                value
                for x, value in zip(measurement.stimulus, values)
                if measurement.function_domain_start <= x <= measurement.function_domain_stop
            ]
        if not values:
            return "0"
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        results = {
            "MAXimum": max(values),
            "MINimum": min(values),
            "MEAN": mean,
            "SDEViation": math.sqrt(variance),
            "PTPeak": max(values) - min(values),
        }
        return f"{results[measurement.function_type]:.12g}"

    def marker_reference_y(self, channel: int) -> float:
        measurement = self.selected(channel)
        values = self.trace_values(channel)
        if not values:
            return 0.0
        bucket = _nearest_bucket(measurement.stimulus, measurement.marker_reference_x)
        return values[min(bucket, len(values) - 1)]

    def evaluate_limits(self, channel: int) -> tuple[int, ...]:
        measurement = self.selected(channel)
        failed = []
        values = self.trace_values(channel)
        for point, (stimulus, value) in enumerate(zip(measurement.stimulus, values), 1):
            for segment in measurement.limit_segments.values():
                if not segment.stimulus_start <= stimulus <= segment.stimulus_stop:
                    continue
                span = segment.stimulus_stop - segment.stimulus_start
                fraction = (stimulus - segment.stimulus_start) / span if span else 0.0
                limit = segment.amplitude_start + fraction * (
                    segment.amplitude_stop - segment.amplitude_start
                )
                violates = value > limit if segment.segment_type == "UPPer" else value < limit
                if violates:
                    failed.append(point)
                    break
        measurement.limit_failed = bool(failed)
        return tuple(failed)


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
    number = ParameterSpec(ParameterType.NUMBER)
    frequency_number = ParameterSpec(
        ParameterType.NUMBER, units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"})
    )

    def measurement_pair(path, attribute, parameter, *, formatter=str):
        add(
            path,
            lambda inv, value, attr=attribute: _measurement_attribute(state, inv, attr, value),
            parameters=(parameter,),
            available=channel_available,
            exists=selected_measurement_exists,
        )
        add(
            path,
            lambda inv, attr=attribute, render=formatter: render(
                getattr(state.selected(inv.indices["channel"]), attr)
            ),
            query=True,
            available=channel_available,
            exists=selected_measurement_exists,
        )

    def marker_pair(path, attribute, parameter, *, formatter=str):
        add(
            path,
            lambda inv, value, attr=attribute: _marker_attribute(state, inv, attr, value),
            parameters=(parameter,),
            available=marker_available,
            exists=selected_measurement_exists,
        )
        add(
            path,
            lambda inv, attr=attribute, render=formatter: render(
                getattr(_marker(state, inv), attr)
            ),
            query=True,
            available=marker_available,
            exists=selected_measurement_exists,
        )

    def state_pair(path, attribute, parameter, *, formatter=str):
        add(
            path,
            lambda inv, value, attr=attribute: _setattr_response(
                state, attr, _coerce_numeric(value)
            ),
            parameters=(parameter,),
        )
        add(
            path,
            lambda inv, attr=attribute, render=formatter: render(getattr(state, attr)),
            query=True,
        )

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
        (calc, HeaderNode("PARameter"), HeaderNode("SELect")),
        lambda inv: state.selected(inv.indices["channel"]).name,
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("COUNt")),
        lambda inv, count: _set_measurement_count(state, inv.indices["channel"], count),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=200),),
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).measurements)),
        query=True,
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("TAG"), HeaderNode("NEXT")),
        lambda inv: str(state.channel(inv.indices["channel"]).next_measurement_number),
        query=True,
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("DELete"), HeaderNode("NAME")),
        lambda inv, name: state.delete(inv.indices["channel"], name) or "",
        parameters=(string,),
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("MNUMber"), HeaderNode("SELect")),
        lambda inv, value: state.select_number(inv.indices["channel"], value) and "",
        parameters=(integer,),
        available=channel_available,
        exists=channel_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("MNUMber"), HeaderNode("SELect")),
        lambda inv: str(state.selected(inv.indices["channel"]).number),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("PARameter"), HeaderNode("EXTended")),
        lambda inv, name, parameter: state.define(inv.indices["channel"], name, parameter) and "",
        parameters=(string, string),
        available=channel_available,
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
    measurement_pair(
        (calc, HeaderNode("MARKer"), HeaderNode("BWIDth")),
        "marker_bandwidth_enabled",
        boolean,
        formatter=_bool,
    )
    for path in (
        (calc, HeaderNode("MARKer"), HeaderNode("REFerence")),
        (calc, HeaderNode("MARKer"), HeaderNode("REFerence"), HeaderNode("STATe")),
    ):
        measurement_pair(path, "marker_reference_enabled", boolean, formatter=_bool)
    measurement_pair(
        (calc, HeaderNode("MARKer"), HeaderNode("REFerence"), HeaderNode("X")),
        "marker_reference_x",
        frequency_number,
        formatter=_format_number,
    )
    add(
        (calc, HeaderNode("MARKer"), HeaderNode("REFerence"), HeaderNode("Y")),
        lambda inv: f"{state.marker_reference_y(inv.indices['channel']):.12g}",
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    marker_pair(
        (calc, marker, HeaderNode("COMPression"), HeaderNode("LEVel")),
        "compression_level",
        number,
        formatter=_format_number,
    )
    for header, response in (("PIN", _marker_compression_pin), ("POUT", _marker_compression_pout)):
        add(
            (calc, marker, HeaderNode("COMPression"), HeaderNode(header)),
            lambda inv, render=response: render(state, inv),
            query=True,
            available=marker_available,
            exists=selected_measurement_exists,
        )
    for path in (
        (calc, marker, HeaderNode("COUPling")),
        (calc, marker, HeaderNode("COUPling"), HeaderNode("STATe")),
    ):
        marker_pair(path, "coupling", boolean, formatter=_bool)
    marker_pair(
        (calc, marker, HeaderNode("COUPling"), HeaderNode("METHod")),
        "coupling_method",
        ParameterSpec(ParameterType.ENUM, choices=("CHANnel", "TRACe")),
    )
    marker_pair((calc, marker, HeaderNode("DELTa")), "delta", boolean, formatter=_bool)
    marker_pair((calc, marker, HeaderNode("DISCrete")), "discrete", boolean, formatter=_bool)
    add(
        (calc, marker, HeaderNode("DISTance")),
        lambda inv, value: _marker_distance(state, inv, value),
        parameters=(ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0)),),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    marker_pair(
        (calc, marker, HeaderNode("FUNCtion"), HeaderNode("APEak"), HeaderNode("EXCursion")),
        "peak_excursion",
        number,
        formatter=_format_number,
    )
    marker_pair(
        (calc, marker, HeaderNode("FUNCtion"), HeaderNode("APEak"), HeaderNode("THReshold")),
        "peak_threshold",
        number,
        formatter=_format_number,
    )
    for path in (
        (calc, marker, HeaderNode("FUNCtion"), HeaderNode("DOMain"), HeaderNode("USER")),
        (
            calc,
            marker,
            HeaderNode("FUNCtion"),
            HeaderNode("DOMain"),
            HeaderNode("USER"),
            HeaderNode("RANGe"),
        ),
    ):
        marker_pair(path, "function_domain_enabled", boolean, formatter=_bool)
    for header, attribute in (
        ("STARt", "function_domain_start"),
        ("STOP", "function_domain_stop"),
    ):
        marker_pair(
            (
                calc,
                marker,
                HeaderNode("FUNCtion"),
                HeaderNode("DOMain"),
                HeaderNode("USER"),
                HeaderNode(header),
            ),
            attribute,
            frequency_number,
            formatter=_format_number,
        )
    marker_pair(
        (calc, marker, HeaderNode("FUNCtion"), HeaderNode("TRACking")),
        "tracking",
        boolean,
        formatter=_bool,
    )
    for path in (
        (calc, marker, HeaderNode("FUNCtion")),
        (calc, marker, HeaderNode("FUNCtion"), HeaderNode("SELect")),
    ):
        marker_pair(
            path,
            "function",
            ParameterSpec(
                ParameterType.ENUM,
                choices=("MAXimum", "MINimum", "TARGet", "PEAK", "NPEak"),
            ),
        )
    add(
        (calc, marker, HeaderNode("SET")),
        lambda inv: _marker_set_target(state, inv),
        available=marker_available,
        exists=selected_measurement_exists,
    )
    for path in (
        (calc, marker, HeaderNode("TARGet")),
        (calc, marker, HeaderNode("TARGet"), HeaderNode("VALue")),
    ):
        marker_pair(path, "target", number, formatter=_format_number)
    marker_pair(
        (calc, marker, HeaderNode("TYPE")),
        "marker_type",
        ParameterSpec(ParameterType.ENUM, choices=("NORMal", "FIXed")),
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

    # Deterministic trace-data and analysis commands operate on the selected
    # measurement so scenario-backed samples flow through without a second data model.
    add(
        (calc, HeaderNode("DATA"), HeaderNode("MFData")),
        lambda inv: _trace_csv(state, inv.indices["channel"], complex_pairs=True),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    data_node = HeaderNode("DATA", index="data", index_default=1)
    add(
        (calc, data_node, HeaderNode("MSData")),
        lambda inv: _trace_csv(state, inv.indices["channel"], complex_pairs=False),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("DATA"), HeaderNode("SNP"), HeaderNode("PORTs"), HeaderNode("SAVE")),
        lambda inv: "",
        available=channel_available,
        exists=selected_measurement_exists,
    )
    measure_node = HeaderNode("MEASure", index="measurement", index_default=1)
    add(
        (calc, measure_node, HeaderNode("RDATa")),
        lambda inv: _measurement_csv(state, inv.indices["channel"], inv.indices["measurement"]),
        query=True,
        available=channel_available,
        exists=channel_exists,
    )

    function = (calc, HeaderNode("FUNCtion"))
    measurement_pair(
        (*function, HeaderNode("TYPE")),
        "function_type",
        ParameterSpec(
            ParameterType.ENUM,
            choices=("MAXimum", "MINimum", "MEAN", "SDEViation", "PTPeak"),
        ),
    )
    measurement_pair(
        (*function, HeaderNode("DOMain"), HeaderNode("USER")),
        "function_domain_enabled",
        boolean,
        formatter=_bool,
    )
    measurement_pair(
        (*function, HeaderNode("DOMain"), HeaderNode("USER"), HeaderNode("RANGe")),
        "function_domain_enabled",
        boolean,
        formatter=_bool,
    )
    measurement_pair(
        (*function, HeaderNode("DOMain"), HeaderNode("USER"), HeaderNode("STARt")),
        "function_domain_start",
        frequency_number,
        formatter=_format_number,
    )
    measurement_pair(
        (*function, HeaderNode("DOMain"), HeaderNode("USER"), HeaderNode("STOP")),
        "function_domain_stop",
        frequency_number,
        formatter=_format_number,
    )
    for path in (
        (*function, HeaderNode("STATistics")),
        (*function, HeaderNode("STATistics"), HeaderNode("STATe")),
    ):
        measurement_pair(path, "function_statistics_enabled", boolean, formatter=_bool)
    add(
        (*function, HeaderNode("EXECute")),
        lambda inv: _execute_function(state, inv.indices["channel"]),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (*function, HeaderNode("DATA")),
        lambda inv: state.function_data(inv.indices["channel"]),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )

    for header, attribute, parameter, formatter in (
        ("FREQuency", "group_delay_frequency", frequency_number, _format_number),
        (
            "PERCent",
            "group_delay_percent",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(100)),
            _format_number,
        ),
        (
            "POINts",
            "group_delay_points",
            ParameterSpec(ParameterType.INTEGER, minimum=1),
            str,
        ),
    ):
        measurement_pair(
            (calc, HeaderNode("GDELay"), HeaderNode(header)),
            attribute,
            parameter,
            formatter=formatter,
        )

    measurement_pair(
        (calc, HeaderNode("HOLD"), HeaderNode("TYPE")),
        "hold_type",
        ParameterSpec(ParameterType.ENUM, choices=("OFF", "MINimum", "MAXimum")),
    )
    add(
        (calc, HeaderNode("HOLD"), HeaderNode("CLEar")),
        lambda inv: _setattr_response(state.selected(inv.indices["channel"]), "hold_type", "OFF"),
        available=channel_available,
        exists=selected_measurement_exists,
    )

    for path in (
        (calc, HeaderNode("LIMit")),
        (calc, HeaderNode("LIMit"), HeaderNode("STATe")),
    ):
        if len(path) == 2:
            measurement_pair(path, "limit_enabled", boolean, formatter=_bool)
    measurement_pair(
        (calc, HeaderNode("LIMit"), HeaderNode("DISPlay")),
        "limit_display_enabled",
        boolean,
        formatter=_bool,
    )
    measurement_pair(
        (calc, HeaderNode("LIMit"), HeaderNode("DISPlay"), HeaderNode("STATe")),
        "limit_display_enabled",
        boolean,
        formatter=_bool,
    )
    measurement_pair(
        (calc, HeaderNode("LIMit"), HeaderNode("SOUNd")),
        "limit_sound_enabled",
        boolean,
        formatter=_bool,
    )
    measurement_pair(
        (calc, HeaderNode("LIMit"), HeaderNode("SOUNd"), HeaderNode("STATe")),
        "limit_sound_enabled",
        boolean,
        formatter=_bool,
    )
    limit_data_parameters = (
        number,
        ParameterSpec(ParameterType.NUMBER, required=False, default=None),
        ParameterSpec(ParameterType.NUMBER, required=False, default=None),
        ParameterSpec(ParameterType.NUMBER, required=False, default=None),
        ParameterSpec(ParameterType.NUMBER, required=False, default=None),
    )
    add(
        (calc, HeaderNode("LIMit"), HeaderNode("DATA")),
        lambda inv, *values: _set_limit_data(state, inv, values),
        parameters=limit_data_parameters,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("LIMit"), HeaderNode("DATA")),
        lambda inv: _limit_data(state.selected(inv.indices["channel"])),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    add(
        (calc, HeaderNode("LIMit"), HeaderNode("DATA"), HeaderNode("DELete")),
        lambda inv: _clear_limits(state.selected(inv.indices["channel"])),
        available=channel_available,
        exists=selected_measurement_exists,
    )
    limit_segment = HeaderNode("SEGMent", index="limit_segment", index_default=1)
    add(
        (calc, HeaderNode("LIMit"), HeaderNode("SEGMent"), HeaderNode("COUNt")),
        lambda inv: str(len(state.selected(inv.indices["channel"]).limit_segments)),
        query=True,
        available=channel_available,
        exists=selected_measurement_exists,
    )
    for path, attribute, parameter in (
        ((HeaderNode("AMPLitude"), HeaderNode("STARt")), "amplitude_start", number),
        ((HeaderNode("AMPLitude"), HeaderNode("STOP")), "amplitude_stop", number),
        ((HeaderNode("STIMulus"), HeaderNode("STARt")), "stimulus_start", frequency_number),
        ((HeaderNode("STIMulus"), HeaderNode("STOP")), "stimulus_stop", frequency_number),
        (
            (HeaderNode("TYPE"),),
            "segment_type",
            ParameterSpec(ParameterType.ENUM, choices=("UPPer", "LOWer")),
        ),
    ):
        add(
            (calc, HeaderNode("LIMit"), limit_segment, *path),
            lambda inv, value, attr=attribute: _set_limit_segment(state, inv, attr, value),
            parameters=(parameter,),
            available=channel_available,
            exists=selected_measurement_exists,
        )
        add(
            (calc, HeaderNode("LIMit"), limit_segment, *path),
            lambda inv, attr=attribute: _limit_segment_query(state, inv, attr),
            query=True,
            available=channel_available,
            exists=selected_measurement_exists,
        )
    for path, response in (
        ((HeaderNode("REPort"),), _limit_report),
        ((HeaderNode("REPort"), HeaderNode("DATA")), _limit_report),
        ((HeaderNode("REPort"), HeaderNode("ALL")), _limit_report),
        ((HeaderNode("REPort"), HeaderNode("POINts")), _limit_points),
    ):
        add(
            (calc, HeaderNode("LIMit"), *path),
            lambda inv, render=response: render(state, inv.indices["channel"]),
            query=True,
            available=channel_available,
            exists=selected_measurement_exists,
        )

    smoothing = (calc, HeaderNode("SMOothing"))
    measurement_pair(smoothing, "smoothing_enabled", boolean, formatter=_bool)
    measurement_pair(
        (*smoothing, HeaderNode("STATe")),
        "smoothing_enabled",
        boolean,
        formatter=_bool,
    )
    measurement_pair(
        (*smoothing, HeaderNode("APERture")),
        "smoothing_aperture",
        ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(100)),
        formatter=_format_number,
    )
    measurement_pair(
        (*smoothing, HeaderNode("POINts")),
        "smoothing_points",
        ParameterSpec(ParameterType.INTEGER, minimum=1),
    )

    measurement_pair(
        (calc, HeaderNode("X"), HeaderNode("AXIS")),
        "x_axis",
        ParameterSpec(ParameterType.ENUM, choices=("AUTO", "LINear", "LOGarithmic")),
    )
    measurement_pair(
        (calc, HeaderNode("X"), HeaderNode("AXIS"), HeaderNode("DOMain")),
        "x_axis_domain",
        ParameterSpec(ParameterType.ENUM, choices=("FREQuency", "TIME", "POWer")),
    )
    for path in (
        (calc, HeaderNode("X")),
        (calc, HeaderNode("X"), HeaderNode("VALues")),
    ):
        add(
            path,
            lambda inv: ",".join(
                f"{value:.12g}" for value in state.selected(inv.indices["channel"]).stimulus
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

    # Display commands are modeled as automation-visible composition and scale
    # metadata. They do not attempt to render or control a physical front panel.
    for path, attribute in (
        ((display, HeaderNode("ENABle")), "display_enabled"),
        ((display, HeaderNode("VISible")), "display_visible"),
        ((display, HeaderNode("UPDate")), "display_update_enabled"),
        ((display, HeaderNode("UPDate"), HeaderNode("STATe")), "display_update_enabled"),
        ((display, HeaderNode("ANNotation")), "display_annotations_enabled"),
        (
            (display, HeaderNode("ANNotation"), HeaderNode("STATus")),
            "display_annotations_enabled",
        ),
        (
            (display, HeaderNode("ANNotation"), HeaderNode("FREQuency")),
            "frequency_annotation_enabled",
        ),
        (
            (
                display,
                HeaderNode("ANNotation"),
                HeaderNode("FREQuency"),
                HeaderNode("STATe"),
            ),
            "frequency_annotation_enabled",
        ),
        (
            (
                display,
                HeaderNode("ANNotation"),
                HeaderNode("MESSage"),
                HeaderNode("STATe"),
            ),
            "message_annotation_enabled",
        ),
    ):
        state_pair(path, attribute, boolean, formatter=_bool)
    state_pair(
        (display, HeaderNode("FSIGn")),
        "frequency_significant_digits",
        ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=15),
    )
    state_pair(
        (
            display,
            HeaderNode("GUI"),
            HeaderNode("POWer"),
            HeaderNode("SPIN"),
            HeaderNode("RESolution"),
        ),
        "power_spin_resolution",
        ParameterSpec(ParameterType.NUMBER, minimum=Decimal("0.001")),
        formatter=_format_number,
    )
    state_pair(
        (display, HeaderNode("TMAX")),
        "maximum_traces",
        ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=MAX_TRACE),
    )
    state_pair(
        (
            display,
            HeaderNode("WINDow"),
            HeaderNode("TRACe"),
            HeaderNode("GRATicule"),
            HeaderNode("GRID"),
            HeaderNode("LTYPE"),
        ),
        "grid_line_type",
        ParameterSpec(ParameterType.ENUM, choices=("SOLid", "DOTTed", "DASHed")),
    )
    add((display, HeaderNode("UPDate"), HeaderNode("IMMediate")), lambda inv: "")
    for header in ("ARRange", "SPLit"):
        add(
            (display, HeaderNode(header)),
            lambda inv, value: "",
            parameters=(ParameterSpec(ParameterType.CHARACTER),),
        )

    display_measurement = HeaderNode("MEASure", index="display_measurement", index_default=1)
    for path in (
        (display, display_measurement),
        (display, display_measurement, HeaderNode("STATe")),
    ):
        add(
            path,
            lambda inv, enabled: _display_measurement_state(state, inv, enabled),
            parameters=(boolean,),
            exists=lambda inv: _display_measurement_exists(state, inv),
        )
        add(
            path,
            lambda inv: _bool(_display_measurement_trace(state, inv).visible),
            query=True,
            exists=lambda inv: _display_measurement_exists(state, inv),
        )
    add(
        (display, display_measurement, HeaderNode("FEED")),
        lambda inv, name: _display_measurement_feed(state, inv, name),
        parameters=(string,),
    )
    add(
        (display, display_measurement, HeaderNode("DELete")),
        lambda inv: _display_measurement_delete(state, inv),
        exists=lambda inv: _display_measurement_exists(state, inv),
    )
    add(
        (display, display_measurement, HeaderNode("SELect")),
        lambda inv: _display_measurement_select(state, inv),
        exists=lambda inv: _display_measurement_exists(state, inv),
    )
    add(
        (display, display_measurement, HeaderNode("MOVE")),
        lambda inv, target: _display_measurement_move(state, inv, target),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=MAX_TRACE),),
        exists=lambda inv: _display_measurement_exists(state, inv),
    )
    for path, attribute, parameter, formatter in (
        ((HeaderNode("MEMory"),), "memory_enabled", boolean, _bool),
        ((HeaderNode("MEMory"), HeaderNode("STATe")), "memory_enabled", boolean, _bool),
        ((HeaderNode("TITLe"), HeaderNode("STATe")), "title_enabled", boolean, _bool),
        (
            (HeaderNode("Y"), HeaderNode("PDIVision")),
            "scale_per_division",
            number,
            _format_number,
        ),
        (
            (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("PDIVision")),
            "scale_per_division",
            number,
            _format_number,
        ),
        ((HeaderNode("Y"), HeaderNode("RLEVel")), "reference_level", number, _format_number),
        (
            (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("RLEVel")),
            "reference_level",
            number,
            _format_number,
        ),
        (
            (HeaderNode("Y"), HeaderNode("RPOSition")),
            "reference_position",
            number,
            _format_number,
        ),
        (
            (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("RPOSition")),
            "reference_position",
            number,
            _format_number,
        ),
    ):
        _register_display_measurement_pair(
            add,
            state,
            (display, display_measurement, *path),
            attribute,
            parameter,
            formatter,
        )
    add(
        (display, display_measurement, HeaderNode("TITLe"), HeaderNode("DATA")),
        lambda inv, value: _setattr_response(
            _display_measurement_trace(state, inv), "title", value
        ),
        parameters=(string,),
        exists=lambda inv: _display_measurement_exists(state, inv),
    )
    _register_display_measurement_pair(
        add,
        state,
        (display, display_measurement, HeaderNode("TITLe")),
        "title_enabled",
        boolean,
        _bool,
    )
    for scale_path in (
        (HeaderNode("Y"), HeaderNode("AUTO")),
        (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("AUTO")),
    ):
        add(
            (display, display_measurement, *scale_path),
            lambda inv: _auto_scale(_display_measurement_trace(state, inv)),
            exists=lambda inv: _display_measurement_exists(state, inv),
        )

    # Window/trace aliases complete the modern and legacy addressing shapes.
    add(
        (display, window),
        lambda inv, enabled: state.set_window(inv.indices["window"], enabled) or "",
        parameters=(boolean,),
        available=window_available,
    )
    add(
        (display, window),
        lambda inv: _bool(inv.indices["window"] in state.windows),
        query=True,
        available=window_available,
    )
    add(
        (display, window, HeaderNode("ENABle")),
        lambda inv, enabled: state.set_window(inv.indices["window"], enabled) or "",
        parameters=(boolean,),
        available=window_available,
    )
    for path in (
        (display, window, HeaderNode("NEXT")),
        (display, window, HeaderNode("NEXT"), HeaderNode("NUMBer")),
    ):
        add(path, lambda inv: str(_next_window(state)), query=True, available=window_available)
    add(
        (display, window, HeaderNode("TRACe"), HeaderNode("NEXT"), HeaderNode("NUMBer")),
        lambda inv: str(_next_trace(state, inv.indices["window"])),
        query=True,
        available=window_available,
        exists=window_exists,
    )
    for path, attribute, parameter, formatter in (
        ((HeaderNode("TABLe"),), "table_enabled", boolean, _bool),
        ((HeaderNode("TITLe"), HeaderNode("STATe")), "title_enabled", boolean, _bool),
        (
            (HeaderNode("Y"), HeaderNode("DIVisions")),
            "divisions",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=20),
            str,
        ),
        (
            (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("DIVisions")),
            "divisions",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=20),
            str,
        ),
    ):
        _register_window_pair(add, state, (display, window, *path), attribute, parameter, formatter)
    add(
        (display, window, HeaderNode("TITLe"), HeaderNode("DATA")),
        lambda inv, value: _setattr_response(state.windows[inv.indices["window"]], "title", value),
        parameters=(string,),
        available=window_available,
        exists=window_exists,
    )
    _register_window_pair(
        add,
        state,
        (display, window, HeaderNode("TITLe")),
        "title_enabled",
        boolean,
        _bool,
    )
    add(
        (display, window, HeaderNode("TITLe"), HeaderNode("DATA")),
        lambda inv: state.windows[inv.indices["window"]].title,
        query=True,
        available=window_available,
        exists=window_exists,
    )
    add(
        (display, window, HeaderNode("Y"), HeaderNode("AUTO")),
        lambda inv: _auto_scale_window(state, inv.indices["window"]),
        available=window_available,
        exists=window_exists,
    )
    add(
        (display, window, trace, HeaderNode("DELete")),
        lambda inv: _delete_trace(state, inv),
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace, HeaderNode("FEED"), HeaderNode("MNUMber")),
        lambda inv, number: _feed_measurement_number(state, inv, number),
        parameters=(integer,),
        available=display_available,
    )
    add(
        (display, window, trace, HeaderNode("MOVE")),
        lambda inv, target: _move_trace(state, inv, target),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=MAX_TRACE),),
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace),
        lambda inv, enabled: _trace_visible(state, inv, enabled),
        parameters=(boolean,),
        available=display_available,
        exists=trace_exists,
    )
    add(
        (display, window, trace),
        lambda inv: _bool(state.trace(inv.indices["window"], inv.indices["trace"]).visible),
        query=True,
        available=display_available,
        exists=trace_exists,
    )
    for path, attribute, parameter, formatter in (
        ((HeaderNode("MEMory"),), "memory_enabled", boolean, _bool),
        ((HeaderNode("MEMory"), HeaderNode("STATe")), "memory_enabled", boolean, _bool),
        ((HeaderNode("TITLe"), HeaderNode("STATe")), "title_enabled", boolean, _bool),
        (
            (HeaderNode("Y"), HeaderNode("PDIVision")),
            "scale_per_division",
            number,
            _format_number,
        ),
        (
            (HeaderNode("Y"), HeaderNode("RPOSition")),
            "reference_position",
            number,
            _format_number,
        ),
    ):
        _register_trace_pair(
            add, state, (display, window, trace, *path), attribute, parameter, formatter
        )
    _register_trace_pair(
        add,
        state,
        (display, window, trace, HeaderNode("TITLe")),
        "title_enabled",
        boolean,
        _bool,
    )
    for path, attribute, parameter, formatter in (
        (
            (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("PDIVision")),
            "scale_per_division",
            number,
            _format_number,
        ),
        (
            (HeaderNode("Y"), HeaderNode("RLEVel")),
            "reference_level",
            number,
            _format_number,
        ),
        (
            (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("RLEVel")),
            "reference_level",
            number,
            _format_number,
        ),
        (
            (HeaderNode("Y"), HeaderNode("SCALe"), HeaderNode("RPOSition")),
            "reference_position",
            number,
            _format_number,
        ),
    ):
        _register_trace_pair(
            add, state, (display, window, trace, *path), attribute, parameter, formatter
        )
    trace_group = HeaderNode("TRACe")
    for path in (
        (
            display,
            window,
            trace_group,
            HeaderNode("Y"),
            HeaderNode("COUPle"),
        ),
        (
            display,
            window,
            trace_group,
            HeaderNode("Y"),
            HeaderNode("COUPle"),
            HeaderNode("STATe"),
        ),
        (
            display,
            window,
            trace_group,
            HeaderNode("Y"),
            HeaderNode("SCALe"),
            HeaderNode("COUPle"),
        ),
        (
            display,
            window,
            trace_group,
            HeaderNode("Y"),
            HeaderNode("SCALe"),
            HeaderNode("COUPle"),
            HeaderNode("STATe"),
        ),
    ):
        _register_window_pair(add, state, path, "y_coupled", boolean, _bool)
    for path in (
        (
            display,
            HeaderNode("WINDow"),
            HeaderNode("TRACe"),
            HeaderNode("Y"),
            HeaderNode("COUPle"),
            HeaderNode("METHod"),
        ),
        (
            display,
            HeaderNode("WINDow"),
            HeaderNode("TRACe"),
            HeaderNode("Y"),
            HeaderNode("SCALe"),
            HeaderNode("COUPle"),
            HeaderNode("METHod"),
        ),
    ):
        state_pair(
            path,
            "trace_y_couple_method",
            ParameterSpec(ParameterType.ENUM, choices=("ALL", "NONE", "UNITs")),
        )
    for path in (
        (display, window, trace, HeaderNode("Y"), HeaderNode("AUTO")),
        (
            display,
            window,
            trace,
            HeaderNode("Y"),
            HeaderNode("SCALe"),
            HeaderNode("AUTO"),
        ),
    ):
        add(
            path,
            lambda inv: _auto_scale(state.trace(inv.indices["window"], inv.indices["trace"])),
            available=display_available,
            exists=trace_exists,
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
    system = HeaderNode("SYSTem")
    add(
        (system, HeaderNode("ACTive"), HeaderNode("MEASurement"), HeaderNode("NUMBer")),
        lambda inv: str(state.active_context()[1].number if state.active_context() else 0),
        query=True,
    )
    add(
        (system, HeaderNode("ACTive"), HeaderNode("SHEet")),
        lambda inv: str(state.active_window or 0),
        query=True,
    )
    add(
        (system, HeaderNode("CHANnels"), HeaderNode("CATalog")),
        lambda inv: ",".join(str(number) for number in sorted(state.channels)) or "EMPTY",
        query=True,
    )
    add(
        (system, HeaderNode("WINDows"), HeaderNode("CATalog")),
        lambda inv: ",".join(str(number) for number in sorted(state.windows)) or "EMPTY",
        query=True,
    )
    add(
        (system, HeaderNode("SHEets"), HeaderNode("CATalog")),
        lambda inv: ",".join(str(number) for number in sorted(state.windows)) or "EMPTY",
        query=True,
    )
    add(
        (system, HeaderNode("MEASurement"), HeaderNode("CATalog")),
        lambda inv: _system_measurement_catalog(state),
        query=True,
    )
    system_measurement = HeaderNode("MEASurement", index="system_measurement", index_default=1)
    system_measure = HeaderNode("MEASure", index="system_measurement", index_default=1)
    add(
        (system, system_measurement, HeaderNode("NAME")),
        lambda inv: _system_measurement(state, inv).name,
        query=True,
        exists=lambda inv: _system_measurement_exists(state, inv),
    )
    add(
        (system, system_measurement, HeaderNode("TRACe")),
        lambda inv: str(_system_measurement_location(state, inv)[1]),
        query=True,
        exists=lambda inv: _system_measurement_exists(state, inv),
    )
    add(
        (system, system_measure, HeaderNode("WINDow")),
        lambda inv: str(_system_measurement_location(state, inv)[0]),
        query=True,
        exists=lambda inv: _system_measurement_exists(state, inv),
    )
    state_pair(
        (system, HeaderNode("ABORt"), HeaderNode("THReshold")),
        "system_abort_threshold",
        number,
        formatter=_format_number,
    )
    for path in (
        (system, HeaderNode("CHANnels"), HeaderNode("COUPle")),
        (system, HeaderNode("CHANnels"), HeaderNode("COUPle"), HeaderNode("STATe")),
    ):
        state_pair(path, "channel_coupling_enabled", boolean, formatter=_bool)
    state_pair(
        (system, HeaderNode("CHANnels"), HeaderNode("COUPle"), HeaderNode("GROup")),
        "channel_coupling_group",
        integer,
    )
    for path in (
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("COUPle"),
            HeaderNode("PARallel"),
        ),
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("COUPle"),
            HeaderNode("PARallel"),
            HeaderNode("ENABle"),
        ),
    ):
        state_pair(path, "channel_parallel_enabled", boolean, formatter=_bool)
    add(
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("COUPle"),
            HeaderNode("PARallel"),
            HeaderNode("STATe"),
        ),
        lambda inv: _bool(state.channel_parallel_enabled),
        query=True,
    )
    add(
        (system, HeaderNode("CHANnels"), HeaderNode("DELete")),
        lambda inv, channel_number: state.delete_channel(channel_number) or "",
        parameters=(integer,),
    )
    state_pair(
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("NOISe"),
            HeaderNode("PARallel"),
            HeaderNode("GROup"),
        ),
        "noise_parallel_group",
        integer,
    )
    state_pair(
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("NOISe"),
            HeaderNode("PARallel"),
            HeaderNode("GROup"),
            HeaderNode("LIST"),
        ),
        "noise_parallel_group_list",
        string,
    )
    for path in (
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("NOISe"),
            HeaderNode("PARallel"),
        ),
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("NOISe"),
            HeaderNode("PARallel"),
            HeaderNode("ENABle"),
        ),
    ):
        state_pair(path, "noise_parallel_enabled", boolean, formatter=_bool)
    add(
        (
            system,
            HeaderNode("CHANnels"),
            HeaderNode("NOISe"),
            HeaderNode("PARallel"),
            HeaderNode("STATe"),
        ),
        lambda inv: _bool(state.noise_parallel_enabled),
        query=True,
    )
    add(
        (system, HeaderNode("DATE")),
        lambda inv: datetime.now().strftime("%Y,%m,%d"),
        query=True,
    )
    add(
        (system, HeaderNode("TIME")),
        lambda inv: datetime.now().strftime("%H,%M,%S"),
        query=True,
    )
    for path, response in (
        ((system, HeaderNode("CONFigure"), HeaderNode("BIT")), "64"),
        ((system, HeaderNode("CONFigure"), HeaderNode("DIRectory")), '""'),
        (
            (system, HeaderNode("CONFigure"), HeaderNode("REVision"), HeaderNode("CPU")),
            "E.1.0",
        ),
        (
            (system, HeaderNode("CONFigure"), HeaderNode("REVision"), HeaderNode("DSP")),
            "E.1.0",
        ),
        (
            (
                system,
                HeaderNode("CONFigure"),
                HeaderNode("REVision"),
                HeaderNode("DSPFpga"),
            ),
            "E.1.0",
        ),
        ((system, HeaderNode("DISK"), HeaderNode("REVision")), "E.1.0"),
        ((system, HeaderNode("MCLass"), HeaderNode("CATalog")), '"VNA"'),
        (
            (system, HeaderNode("MCLass"), HeaderNode("PARameter"), HeaderNode("CATalog")),
            '"S"',
        ),
    ):
        add(path, lambda inv, value=response: value, query=True)
    add(
        (system, HeaderNode("SET")),
        lambda inv, value: _setattr_response(state, "system_setting", value),
        parameters=(string,),
    )
    add((system, HeaderNode("SET")), lambda inv: state.system_setting, query=True)


def _modify(measurement: MeasurementState, parameter: str) -> str:
    measurement.parameter = _parameter(parameter)
    return ""


def _setattr_response(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _measurement_attribute(state, invocation, name: str, value) -> str:
    if isinstance(value, NumericValue):
        value = _numeric(value)
    setattr(state.selected(invocation.indices["channel"]), name, value)
    return ""


def _marker_attribute(state, invocation, name: str, value) -> str:
    if isinstance(value, NumericValue):
        value = _numeric(value)
    setattr(_marker(state, invocation), name, value)
    return ""


def _set_measurement_count(state: VNAMeasurementSystem, channel_number: int, count: int) -> str:
    channel = state.channel(channel_number)
    while len(channel.measurements) < count:
        state.define_legacy(channel_number, "S11")
    while len(channel.measurements) > count:
        state.delete(channel_number, next(reversed(channel.measurements)))
    return ""


def _trace_csv(state: VNAMeasurementSystem, channel: int, *, complex_pairs: bool) -> str:
    samples = state._math_samples(state.selected(channel))
    if complex_pairs:
        values = [component for sample in samples for component in (sample.real, sample.imag)]
    else:
        values = [abs(sample) for sample in samples]
    return ",".join(f"{value:.12g}" for value in values)


def _measurement_csv(state: VNAMeasurementSystem, channel: int, number: int) -> str:
    channel_state = state.channel(channel)
    measurement = next(
        (item for item in channel_state.measurements.values() if item.number == number), None
    )
    if measurement is None:
        raise SCPICommandError(-200, "Execution error; measurement does not exist")
    return ",".join(
        f"{component:.12g}"
        for sample in state._math_samples(measurement)
        for component in (sample.real, sample.imag)
    )


def _execute_function(state: VNAMeasurementSystem, channel: int) -> str:
    state.function_data(channel)
    return ""


def _set_limit_data(state, invocation, values) -> str:
    converted = [_numeric(value) for value in values if isinstance(value, NumericValue)]
    if len(converted) < 4:
        converted.extend([0.0] * (4 - len(converted)))
    measurement = state.selected(invocation.indices["channel"])
    segment = LimitSegmentState(
        1,
        stimulus_start=converted[0],
        stimulus_stop=converted[1],
        amplitude_start=converted[2],
        amplitude_stop=converted[3],
        segment_type="LOWer" if len(converted) > 4 and converted[4] < 0 else "UPPer",
    )
    measurement.limit_segments = {1: segment}
    state.evaluate_limits(invocation.indices["channel"])
    return ""


def _limit_data(measurement: MeasurementState) -> str:
    fields = []
    for segment in measurement.limit_segments.values():
        fields.extend(
            (
                segment.stimulus_start,
                segment.stimulus_stop,
                segment.amplitude_start,
                segment.amplitude_stop,
                1 if segment.segment_type == "UPPer" else -1,
            )
        )
    return ",".join(f"{value:.12g}" for value in fields)


def _clear_limits(measurement: MeasurementState) -> str:
    measurement.limit_segments.clear()
    measurement.limit_failed = False
    return ""


def _limit_segment(state, invocation) -> LimitSegmentState:
    measurement = state.selected(invocation.indices["channel"])
    number = invocation.indices["limit_segment"]
    return measurement.limit_segments.setdefault(number, LimitSegmentState(number))


def _set_limit_segment(state, invocation, attribute: str, value) -> str:
    if isinstance(value, NumericValue):
        value = _numeric(value)
    setattr(_limit_segment(state, invocation), attribute, value)
    state.evaluate_limits(invocation.indices["channel"])
    return ""


def _limit_segment_query(state, invocation, attribute: str) -> str:
    value = getattr(_limit_segment(state, invocation), attribute)
    return _format_number(value) if isinstance(value, float) else str(value)


def _limit_points(state: VNAMeasurementSystem, channel: int) -> str:
    return ",".join(str(point) for point in state.evaluate_limits(channel)) or "0"


def _limit_report(state: VNAMeasurementSystem, channel: int) -> str:
    points = state.evaluate_limits(channel)
    return f"{int(bool(points))},{len(points)}"


def _marker_compression_pin(state: VNAMeasurementSystem, invocation) -> str:
    return _format_number(_marker(state, invocation).x)


def _marker_compression_pout(state: VNAMeasurementSystem, invocation) -> str:
    measurement = state.selected(invocation.indices["channel"])
    marker = _marker(state, invocation)
    values = state.trace_values(invocation.indices["channel"])
    if not values:
        return "0"
    bucket = _nearest_bucket(measurement.stimulus, marker.x)
    return f"{values[min(bucket, len(values) - 1)]:.12g}"


def _marker_distance(state, invocation, value: NumericValue) -> str:
    _marker(state, invocation).x = _numeric(value)
    return ""


def _marker_set_target(state: VNAMeasurementSystem, invocation) -> str:
    measurement = state.selected(invocation.indices["channel"])
    marker = _marker(state, invocation)
    values = state.trace_values(invocation.indices["channel"])
    if values:
        bucket = min(range(len(values)), key=lambda item: abs(values[item] - marker.target))
        marker.bucket = bucket
        marker.x = measurement.stimulus[min(bucket, len(measurement.stimulus) - 1)]
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


def _next_window(state: VNAMeasurementSystem) -> int:
    available = next(
        (number for number in range(1, MAX_WINDOW + 1) if number not in state.windows), None
    )
    if available is None:
        raise SCPICommandError(-200, "Execution error; no free display windows")
    return available


def _display_measurements(state: VNAMeasurementSystem) -> list[MeasurementState]:
    return [
        measurement
        for channel in sorted(state.channels.values(), key=lambda item: item.number)
        for measurement in channel.measurements.values()
    ]


def _system_measurement_catalog(state: VNAMeasurementSystem) -> str:
    measurements = _display_measurements(state)
    return f'"{",".join(item.name for item in measurements)}"' if measurements else "EMPTY"


def _system_measurement_exists(state, invocation) -> bool:
    return 1 <= invocation.indices["system_measurement"] <= len(_display_measurements(state))


def _system_measurement(state, invocation) -> MeasurementState:
    if not _system_measurement_exists(state, invocation):
        raise SCPICommandError(-200, "Execution error; system measurement does not exist")
    return _display_measurements(state)[invocation.indices["system_measurement"] - 1]


def _system_measurement_location(state, invocation) -> tuple[int, int]:
    location = state.measurement_window_trace(_system_measurement(state, invocation).name)
    return location or (0, 0)


def _display_measurement(state, invocation) -> MeasurementState:
    number = invocation.indices["display_measurement"]
    measurements = _display_measurements(state)
    if not 1 <= number <= len(measurements):
        raise SCPICommandError(-200, "Execution error; display measurement does not exist")
    return measurements[number - 1]


def _display_measurement_exists(state, invocation) -> bool:
    return 1 <= invocation.indices["display_measurement"] <= len(_display_measurements(state))


def _display_measurement_trace(state, invocation) -> TraceState:
    measurement = _display_measurement(state, invocation)
    location = state.measurement_window_trace(measurement.name)
    if location is None:
        window = state.active_window or 1
        state.set_window(window, True)
        return state.feed(window, _next_trace(state, window), measurement.name)
    return state.trace(*location)


def _display_measurement_state(state, invocation, enabled: bool) -> str:
    _display_measurement_trace(state, invocation).visible = enabled
    return ""


def _display_measurement_feed(state, invocation, name: str) -> str:
    if state.find(name) is None:
        raise SCPICommandError(-200, f"Execution error; measurement {name!r} does not exist")
    window = state.active_window or 1
    state.set_window(window, True)
    state.feed(window, invocation.indices["display_measurement"], name)
    return ""


def _display_measurement_delete(state, invocation) -> str:
    measurement = _display_measurement(state, invocation)
    location = state.measurement_window_trace(measurement.name)
    if location is not None:
        del state.windows[location[0]].traces[location[1]]
    return ""


def _display_measurement_select(state, invocation) -> str:
    trace = _display_measurement_trace(state, invocation)
    location = state.measurement_window_trace(trace.measurement)
    if location is not None:
        state.select_trace(*location)
    return ""


def _display_measurement_move(state, invocation, target: int) -> str:
    trace = _display_measurement_trace(state, invocation)
    location = state.measurement_window_trace(trace.measurement)
    if location is None:
        return ""
    window = state.windows[location[0]]
    del window.traces[location[1]]
    trace.number = target
    window.traces[target] = trace
    window.active_trace = target
    return ""


def _register_display_measurement_pair(add, state, path, attribute, parameter, formatter) -> None:
    def exists(inv):
        return _display_measurement_exists(state, inv)

    add(
        path,
        lambda inv, value: _setattr_response(
            _display_measurement_trace(state, inv), attribute, _coerce_numeric(value)
        ),
        parameters=(parameter,),
        exists=exists,
    )
    add(
        path,
        lambda inv: formatter(getattr(_display_measurement_trace(state, inv), attribute)),
        query=True,
        exists=exists,
    )


def _register_window_pair(add, state, path, attribute, parameter, formatter) -> None:
    def exists(inv):
        return inv.indices["window"] in state.windows

    add(
        path,
        lambda inv, value: _setattr_response(
            state.windows[inv.indices["window"]], attribute, _coerce_numeric(value)
        ),
        parameters=(parameter,),
        exists=exists,
    )
    add(
        path,
        lambda inv: formatter(getattr(state.windows[inv.indices["window"]], attribute)),
        query=True,
        exists=exists,
    )


def _register_trace_pair(add, state, path, attribute, parameter, formatter) -> None:
    def exists(inv):
        return (
            inv.indices["window"] in state.windows
            and inv.indices["trace"] in state.windows[inv.indices["window"]].traces
        )

    add(
        path,
        lambda inv, value: _setattr_response(
            state.trace(inv.indices["window"], inv.indices["trace"]),
            attribute,
            _coerce_numeric(value),
        ),
        parameters=(parameter,),
        exists=exists,
    )
    add(
        path,
        lambda inv: formatter(
            getattr(state.trace(inv.indices["window"], inv.indices["trace"]), attribute)
        ),
        query=True,
        exists=exists,
    )


def _coerce_numeric(value):
    return _numeric(value) if isinstance(value, NumericValue) else value


def _auto_scale(trace: TraceState) -> str:
    trace.scale_per_division = 10.0
    trace.reference_level = 0.0
    trace.reference_position = 5.0
    return ""


def _auto_scale_window(state: VNAMeasurementSystem, window: int) -> str:
    for trace in state.windows[window].traces.values():
        _auto_scale(trace)
    return ""


def _delete_trace(state: VNAMeasurementSystem, invocation) -> str:
    window = state.windows[invocation.indices["window"]]
    del window.traces[invocation.indices["trace"]]
    if window.active_trace not in window.traces:
        window.active_trace = min(window.traces, default=None)
    return ""


def _feed_measurement_number(state, invocation, number: int) -> str:
    measurement = next(
        (item for item in _display_measurements(state) if item.number == number), None
    )
    if measurement is None:
        raise SCPICommandError(-200, "Execution error; measurement number does not exist")
    state.feed(invocation.indices["window"], invocation.indices["trace"], measurement.name)
    return ""


def _move_trace(state, invocation, target: int) -> str:
    window = state.windows[invocation.indices["window"]]
    trace = window.traces.pop(invocation.indices["trace"])
    trace.number = target
    window.traces[target] = trace
    window.active_trace = target
    return ""


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


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
