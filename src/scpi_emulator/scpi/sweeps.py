"""VNA stimulus, source, and sweep configuration tied to acquisition timing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock

from .acquisition import AcquisitionController
from .capabilities import PNACapabilities
from .measurements import PNAMeasurementSystem
from .parser import NumericValue
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)


SWEEP_TYPES = ("LINear", "LOGarithmic", "CW", "POWer", "SEGMent")
RECEIVERS = ("ARECeiver", "BRECeiver", "CRECeiver", "DRECeiver")


@dataclass
class SegmentState:
    number: int
    frequency_start: float
    frequency_stop: float
    points: int = 201
    if_bandwidth: float = 1_000.0
    dwell: float = 0.0
    power: float = -10.0
    enabled: bool = True

    def axis(self) -> tuple[float, ...]:
        return _linear(self.frequency_start, self.frequency_stop, self.points)


@dataclass
class PNASweepChannel:
    number: int
    frequency_start: float
    frequency_stop: float
    frequency_cw: float
    points: int = 201
    sweep_type: str = "LINear"
    if_bandwidth: float = 1_000.0
    dwell: float = 0.0
    power_start: float = -10.0
    power_stop: float = 0.0
    port_power: dict[int, float] = field(default_factory=dict)
    receiver_attenuation: dict[str, float] = field(default_factory=dict)
    segments: list[SegmentState] = field(default_factory=list)
    generation: str = "ANALog"

    @property
    def frequency_center(self) -> float:
        return (self.frequency_start + self.frequency_stop) / 2

    @property
    def frequency_span(self) -> float:
        return self.frequency_stop - self.frequency_start

    def axis(self) -> tuple[float, ...]:
        if self.sweep_type == "SEGMent":
            return tuple(value for segment in self.segments if segment.enabled for value in segment.axis())
        if self.sweep_type == "CW":
            return (self.frequency_cw,) * self.points
        if self.sweep_type == "POWer":
            return _linear(self.power_start, self.power_stop, self.points)
        if self.sweep_type == "LOGarithmic":
            start = math.log10(self.frequency_start)
            stop = math.log10(self.frequency_stop)
            return tuple(10**value for value in _linear(start, stop, self.points))
        return _linear(self.frequency_start, self.frequency_stop, self.points)

    @property
    def duration(self) -> float:
        # Deterministic approximation: one IF settling interval plus dwell per point.
        if self.sweep_type == "SEGMent":
            return sum(
                segment.points * (1 / segment.if_bandwidth + segment.dwell)
                for segment in self.segments
                if segment.enabled
            )
        return self.points * (1 / self.if_bandwidth + self.dwell)


class PNASweepSystem:
    """Maintain coherent per-channel axes and acquisition durations."""

    def __init__(
        self,
        capabilities: PNACapabilities,
        measurements: PNAMeasurementSystem,
        acquisition: AcquisitionController,
    ) -> None:
        self.capabilities = capabilities
        self.measurements = measurements
        self.acquisition = acquisition
        self.channels: dict[int, PNASweepChannel] = {}
        self._lock = RLock()
        measurements.axis_provider = lambda number: self.channel(number).axis()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.channels.clear()
            self.channel(1)
            self._synchronize(1)

    def channel(self, number: int) -> PNASweepChannel:
        with self._lock:
            if number not in self.channels:
                minimum = float(self.capabilities.frequency_minimum)
                maximum = float(self.capabilities.frequency_maximum)
                self.channels[number] = PNASweepChannel(
                    number, minimum, maximum, (minimum + maximum) / 2
                )
            return self.channels[number]

    def configure(self, number: int, attribute: str, value: float | int | str) -> None:
        with self._lock:
            channel = self.channel(number)
            if attribute.startswith("frequency_"):
                self._validate_frequency(float(value))
            if attribute == "if_bandwidth" and float(value) <= 0:
                raise SCPICommandError(-222, "Data out of range; IF bandwidth")
            setattr(channel, attribute, value)
            if channel.frequency_start > channel.frequency_stop:
                if attribute == "frequency_start":
                    channel.frequency_stop = channel.frequency_start
                else:
                    channel.frequency_start = channel.frequency_stop
            self._synchronize(number)

    def set_center(self, number: int, center: float) -> None:
        channel = self.channel(number)
        half_span = channel.frequency_span / 2
        self._validate_frequency(center - half_span)
        self._validate_frequency(center + half_span)
        channel.frequency_start = center - half_span
        channel.frequency_stop = center + half_span
        self._synchronize(number)

    def set_span(self, number: int, span: float) -> None:
        channel = self.channel(number)
        center = channel.frequency_center
        if span < 0:
            raise SCPICommandError(-222, "Data out of range; frequency span")
        self._validate_frequency(center - span / 2)
        self._validate_frequency(center + span / 2)
        channel.frequency_start = center - span / 2
        channel.frequency_stop = center + span / 2
        self._synchronize(number)

    def set_port_power(self, number: int, port: int, value: float) -> None:
        if not 1 <= port <= self.capabilities.ports:
            raise SCPICommandError(-222, "Data out of range; source port")
        self.channel(number).port_power[port] = value

    def port_power(self, number: int, port: int) -> float:
        if not 1 <= port <= self.capabilities.ports:
            raise SCPICommandError(-222, "Data out of range; source port")
        return self.channel(number).port_power.get(port, -10.0)

    def set_receiver_attenuation(self, number: int, receiver: str, value: float) -> None:
        self.channel(number).receiver_attenuation[receiver] = value

    def receiver_attenuation(self, number: int, receiver: str) -> float:
        return self.channel(number).receiver_attenuation.get(receiver, 0.0)

    def add_segment(self, number: int, segment_number: int) -> None:
        channel = self.channel(number)
        if not 1 <= segment_number <= len(channel.segments) + 1:
            raise SCPICommandError(-222, "Data out of range; segment number")
        segment = SegmentState(
            segment_number, channel.frequency_start, channel.frequency_stop,
            if_bandwidth=channel.if_bandwidth, dwell=channel.dwell,
        )
        channel.segments.insert(segment_number - 1, segment)
        self._renumber(channel)
        self._synchronize(number)

    def delete_segment(self, number: int, segment_number: int) -> None:
        channel = self.channel(number)
        if not 1 <= segment_number <= len(channel.segments):
            raise SCPICommandError(-200, "Execution error; segment does not exist")
        del channel.segments[segment_number - 1]
        self._renumber(channel)
        self._synchronize(number)

    def segment(self, number: int, segment_number: int) -> SegmentState:
        channel = self.channel(number)
        if not 1 <= segment_number <= len(channel.segments):
            raise SCPICommandError(-200, "Execution error; segment does not exist")
        return channel.segments[segment_number - 1]

    def configure_segment(self, number: int, segment_number: int, attribute: str, value) -> None:
        segment = self.segment(number, segment_number)
        if attribute.startswith("frequency_"):
            self._validate_frequency(float(value))
        if attribute == "if_bandwidth" and float(value) <= 0:
            raise SCPICommandError(-222, "Data out of range; segment IF bandwidth")
        setattr(segment, attribute, value)
        if segment.frequency_start > segment.frequency_stop:
            if attribute == "frequency_start":
                segment.frequency_stop = segment.frequency_start
            else:
                segment.frequency_start = segment.frequency_stop
        self._synchronize(number)

    def delete_all_segments(self, number: int) -> None:
        self.channel(number).segments.clear()
        self._synchronize(number)

    @staticmethod
    def _renumber(channel: PNASweepChannel) -> None:
        for number, segment in enumerate(channel.segments, 1):
            segment.number = number

    def _validate_frequency(self, value: float) -> None:
        if not self.capabilities.frequency_minimum <= value <= self.capabilities.frequency_maximum:
            raise SCPICommandError(-222, "Data out of range; frequency")

    def _synchronize(self, number: int) -> None:
        channel = self.channel(number)
        axis = channel.axis()
        measurement_channel = self.measurements.channels.get(number)
        if measurement_channel is not None:
            for measurement in measurement_channel.measurements.values():
                measurement.stimulus = axis
                if len(measurement.samples) != len(axis):
                    measurement.samples = (0j,) * len(axis)
        self.acquisition.set_sweep_time(number, channel.duration)


def register_sweep_commands(registry: CommandRegistry, state: PNASweepSystem) -> None:
    """Register the common VNA frequency, power, IFBW, points, and type commands."""
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    source = HeaderNode("SOURce", index="channel", index_default=1)
    power = HeaderNode("POWer", index="port", index_default=1)
    frequency = (sense, HeaderNode("FREQuency"))
    sweep = (sense, HeaderNode("SWEep"))
    segment = HeaderNode("SEGMent", index="segment", index_default=1)
    frequency_number = ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0),
        units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}))
    power_number = ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50))

    def add(path, handler, *, query=False, parameters=()):
        registry.register(
            CommandSpec(
                tuple(path),
                handler,
                tuple(parameters),
                query=query,
                exists=lambda inv: inv.indices.get("channel", 1) in state.measurements.channels,
            )
        )

    for header, attribute in (
        ("STARt", "frequency_start"),
        ("STOP", "frequency_stop"),
        ("CW", "frequency_cw"),
    ):
        add((*frequency, HeaderNode(header)),
            lambda inv, value, attr=attribute: _set_number(state, inv, attr, value),
            parameters=(frequency_number,))
        add((*frequency, HeaderNode(header)),
            lambda inv, attr=attribute: _format_number(
                getattr(state.channel(inv.indices["channel"]), attr)
            ),
            query=True)
    add((*frequency, HeaderNode("CENTer")),
        lambda inv, value: _empty(state.set_center(inv.indices["channel"], _number(value))),
        parameters=(frequency_number,))
    add((*frequency, HeaderNode("CENTer")),
        lambda inv: _format_number(state.channel(inv.indices["channel"]).frequency_center),
        query=True)
    add((*frequency, HeaderNode("SPAN")),
        lambda inv, value: _empty(state.set_span(inv.indices["channel"], _number(value))),
        parameters=(frequency_number,))
    add((*frequency, HeaderNode("SPAN")),
        lambda inv: _format_number(state.channel(inv.indices["channel"]).frequency_span), query=True)

    add((*sweep, HeaderNode("POINts")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "points", value)),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001),))
    add((*sweep, HeaderNode("POINts")),
        lambda inv: str(state.channel(inv.indices["channel"]).points), query=True)
    add((*sweep, HeaderNode("TYPE")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "sweep_type", value)),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=SWEEP_TYPES),))
    add((*sweep, HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).sweep_type, query=True)
    add((*sweep, HeaderNode("DWELl")),
        lambda inv, value: _set_number(state, inv, "dwell", value),
        parameters=(ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"S"})),))
    add((*sweep, HeaderNode("DWELl")),
        lambda inv: str(state.channel(inv.indices["channel"]).dwell), query=True)
    add((*sweep, HeaderNode("GENeration")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "generation", value)),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("STEPped", "ANALog")),))
    add((*sweep, HeaderNode("GENeration")),
        lambda inv: state.channel(inv.indices["channel"]).generation, query=True)
    add((sense, HeaderNode("BANDwidth")),
        lambda inv, value: _set_number(state, inv, "if_bandwidth", value),
        parameters=(frequency_number,))
    add((sense, HeaderNode("BANDwidth")),
        lambda inv: str(state.channel(inv.indices["channel"]).if_bandwidth), query=True)

    receiver = ParameterSpec(ParameterType.ENUM, choices=RECEIVERS)
    attenuation = ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(70))
    add((sense, HeaderNode("POWer"), HeaderNode("ATTenuator")),
        lambda inv, name, value: _empty(state.set_receiver_attenuation(
            inv.indices["channel"], name, _number(value))), parameters=(receiver, attenuation))
    add((sense, HeaderNode("POWer"), HeaderNode("ATTenuator")),
        lambda inv, name: str(state.receiver_attenuation(inv.indices["channel"], name)),
        query=True, parameters=(receiver,))

    add((sense, segment, HeaderNode("ADD")),
        lambda inv: _empty(state.add_segment(inv.indices["channel"], inv.indices["segment"])))
    add((sense, segment, HeaderNode("DELete")),
        lambda inv: _empty(state.delete_segment(inv.indices["channel"], inv.indices["segment"])))
    add((sense, HeaderNode("SEGMent"), HeaderNode("DELete"), HeaderNode("ALL")),
        lambda inv: _empty(state.delete_all_segments(inv.indices["channel"])))
    add((sense, HeaderNode("SEGMent"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).segments)), query=True)
    for header, attribute in (("STARt", "frequency_start"), ("STOP", "frequency_stop")):
        add((sense, segment, HeaderNode("FREQuency"), HeaderNode(header)),
            lambda inv, value, attr=attribute: _set_segment_number(state, inv, attr, value),
            parameters=(frequency_number,))
        add((sense, segment, HeaderNode("FREQuency"), HeaderNode(header)),
            lambda inv, attr=attribute: str(getattr(state.segment(
                inv.indices["channel"], inv.indices["segment"]), attr)), query=True)
    for path, attribute, parameter in (
        ((HeaderNode("SWEep"), HeaderNode("POINts")), "points",
         ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001)),
        ((HeaderNode("BWIDth"),), "if_bandwidth", frequency_number),
        ((HeaderNode("SWEep"), HeaderNode("DWELl")), "dwell",
         ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"S"}))),
        ((HeaderNode("STATe"),), "enabled", ParameterSpec(ParameterType.BOOLEAN)),
    ):
        add((sense, segment, *path),
            lambda inv, value, attr=attribute: _set_segment_value(state, inv, attr, value),
            parameters=(parameter,))
        add((sense, segment, *path),
            lambda inv, attr=attribute: _segment_query(state, inv, attr), query=True)

    add((source, power), lambda inv, value: _empty(state.set_port_power(
        inv.indices["channel"], inv.indices["port"], _number(value))), parameters=(power_number,))
    add((source, power), lambda inv: str(state.port_power(
        inv.indices["channel"], inv.indices["port"])), query=True)
    for header, attribute in (("STARt", "power_start"), ("STOP", "power_stop")):
        add((source, power, HeaderNode(header)),
            lambda inv, value, attr=attribute: _set_number(state, inv, attr, value),
            parameters=(power_number,))
        add((source, power, HeaderNode(header)),
            lambda inv, attr=attribute: str(getattr(state.channel(inv.indices["channel"]), attr)),
            query=True)


def _set_number(state: PNASweepSystem, invocation, attribute: str, value: NumericValue) -> str:
    state.configure(invocation.indices["channel"], attribute, _number(value))
    return ""


def _set_segment_number(state, invocation, attribute: str, value: NumericValue) -> str:
    state.configure_segment(invocation.indices["channel"], invocation.indices["segment"],
                            attribute, _number(value))
    return ""


def _set_segment_value(state, invocation, attribute: str, value) -> str:
    if isinstance(value, NumericValue):
        value = _number(value)
    state.configure_segment(invocation.indices["channel"], invocation.indices["segment"],
                            attribute, value)
    return ""


def _segment_query(state, invocation, attribute: str) -> str:
    value = getattr(state.segment(invocation.indices["channel"], invocation.indices["segment"]),
                    attribute)
    return str(int(value)) if isinstance(value, bool) else str(value)


def _number(value: NumericValue) -> float:
    scale = {None: 1, "S": 1, "HZ": 1, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[value.unit]
    return float(value.value) * scale


def _linear(start: float, stop: float, points: int) -> tuple[float, ...]:
    if points == 1:
        return (start,)
    step = (stop - start) / (points - 1)
    return tuple(start + step * index for index in range(points))


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _empty(_value=None) -> str:
    return ""
