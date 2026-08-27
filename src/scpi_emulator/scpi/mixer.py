"""VNA frequency-offset, converter, mixer-segment, and embedded-LO behavior."""

from __future__ import annotations

import cmath
from dataclasses import dataclass, field
from decimal import Decimal

from .measurements import VNAMeasurementSystem
from .parser import NumericValue
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)


@dataclass
class FrequencyOffsetRange:
    number: int
    start: float
    stop: float
    role: str = "OUTPut"
    enabled: bool = True


@dataclass
class MixerSegment:
    number: int
    start: float
    stop: float
    power: float = 0.0
    points: int = 201
    enabled: bool = True


@dataclass
class MixerChannel:
    mixer_enabled: bool = False
    fom_enabled: bool = False
    fixed_frequency: float = 1e9
    lo_frequency: float = 1e9
    if_frequency: float = 1e9
    mode: str = "UPConverter"
    converter_type: str = "VECTor"
    source_roles: dict[int, str] = field(default_factory=dict)
    ranges: dict[int, FrequencyOffsetRange] = field(default_factory=dict)
    segments: dict[int, MixerSegment] = field(default_factory=dict)
    embedded_lo_enabled: bool = False
    embedded_lo_center: float = 1e9
    embedded_lo_span: float = 1e6


class VNAMixerSystem:
    """Translate generic traces through deterministic converter configuration."""

    def __init__(
        self,
        measurements: VNAMeasurementSystem,
        frequency_minimum: float,
        frequency_maximum: float,
        source_count: int,
    ) -> None:
        self.measurements = measurements
        self.frequency_minimum = frequency_minimum
        self.frequency_maximum = frequency_maximum
        self.source_count = source_count
        self.channels: dict[int, MixerChannel] = {}

    def reset(self) -> None:
        self.channels.clear()

    def channel(self, number: int) -> MixerChannel:
        if number not in self.channels:
            channel = MixerChannel()
            channel.ranges[1] = FrequencyOffsetRange(
                1, self.frequency_minimum, self.frequency_maximum
            )
            self.channels[number] = channel
        return self.channels[number]

    def axis(self, channel_number: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        channel = self.channel(channel_number)
        axis = stimulus
        segments = [segment for segment in channel.segments.values() if segment.enabled]
        if segments:
            axis = tuple(
                point
                for segment in sorted(segments, key=lambda item: item.number)
                for point in _linear(segment.start, segment.stop, segment.points)
            )
        elif channel.fom_enabled:
            ranges = [item for item in channel.ranges.values() if item.enabled]
            if ranges:
                selected = sorted(ranges, key=lambda item: item.number)[0]
                axis = _linear(selected.start, selected.stop, len(stimulus))
        if channel.mixer_enabled:
            lo = self._effective_lo(channel)
            if channel.mode == "UPConverter":
                axis = tuple(point + lo for point in axis)
            else:
                axis = tuple(abs(point - lo) for point in axis)
        return axis

    def samples(
        self,
        channel_number: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        channel = self.channel(channel_number)
        target_count = len(self.axis(channel_number, stimulus))
        result = _resample(samples, target_count)
        if not channel.mixer_enabled:
            return result
        magnitude = 0.5 if channel.converter_type == "SCALar" else 0.8
        direction = 1 if channel.mode == "UPConverter" else -1
        lo_phase = (
            channel.embedded_lo_span / max(channel.embedded_lo_center, 1.0)
            if channel.embedded_lo_enabled
            else 0.0
        )
        return tuple(
            value * cmath.rect(magnitude, direction * (index + 1) * (0.02 + lo_phase))
            for index, value in enumerate(result)
        )

    def set_frequency(self, channel: int, attribute: str, value: float) -> None:
        self._frequency(value)
        setattr(self.channel(channel), attribute, value)

    def set_source_role(self, channel: int, source: int, role: str) -> None:
        self._source(source)
        self.channel(channel).source_roles[source] = role

    def source_role(self, channel: int, source: int) -> str:
        self._source(source)
        return self.channel(channel).source_roles.get(source, "OFF")

    def add_range(self, channel: int, number: int) -> None:
        if number in self.channel(channel).ranges:
            raise SCPICommandError(-200, "Execution error; FOM range exists")
        self.channel(channel).ranges[number] = FrequencyOffsetRange(
            number, self.frequency_minimum, self.frequency_maximum
        )

    def delete_range(self, channel: int, number: int) -> None:
        if self.channel(channel).ranges.pop(number, None) is None:
            raise SCPICommandError(-200, "Execution error; FOM range does not exist")

    def range(self, channel: int, number: int) -> FrequencyOffsetRange:
        try:
            return self.channel(channel).ranges[number]
        except KeyError as exc:
            raise SCPICommandError(-200, "Execution error; FOM range does not exist") from exc

    def add_segment(self, channel: int, number: int) -> None:
        if number in self.channel(channel).segments:
            raise SCPICommandError(-200, "Execution error; mixer segment exists")
        self.channel(channel).segments[number] = MixerSegment(
            number, self.frequency_minimum, self.frequency_maximum
        )

    def delete_segment(self, channel: int, number: int) -> None:
        if self.channel(channel).segments.pop(number, None) is None:
            raise SCPICommandError(-200, "Execution error; mixer segment does not exist")

    def segment(self, channel: int, number: int) -> MixerSegment:
        try:
            return self.channel(channel).segments[number]
        except KeyError as exc:
            raise SCPICommandError(-200, "Execution error; mixer segment does not exist") from exc

    def recalculate(self, channel: int) -> None:
        state = self.channel(channel)
        state.if_frequency = abs(state.fixed_frequency - self._effective_lo(state))

    def _effective_lo(self, channel: MixerChannel) -> float:
        if channel.embedded_lo_enabled:
            return channel.embedded_lo_center + channel.embedded_lo_span * 0.05
        return channel.lo_frequency

    def _frequency(self, value: float) -> None:
        if not self.frequency_minimum <= value <= self.frequency_maximum:
            raise SCPICommandError(-222, "Data out of range; converter frequency")

    def _source(self, source: int) -> None:
        if not 1 <= source <= self.source_count:
            raise SCPICommandError(-222, "Data out of range; source number")


def register_mixer_commands(registry: CommandRegistry, state: VNAMixerSystem) -> None:
    """Register profile-gated FOM, mixer, segment, and embedded-LO commands."""
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    fom = (sense, HeaderNode("FOM"))
    mixer = (sense, HeaderNode("MIXer"))
    range_node = HeaderNode("RANGe", index="range", index_default=1)
    segment_node = HeaderNode("SEGMent", index="segment", index_default=1)
    source_node = HeaderNode("SOURce", index="source", index_default=1)
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    frequency = ParameterSpec(
        ParameterType.NUMBER,
        minimum=Decimal(0),
        units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
    )

    def measurement_exists(inv):
        channel = state.measurements.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def range_exists(inv):
        return (
            measurement_exists(inv)
            and inv.indices.get("range", 1) in state.channel(inv.indices.get("channel", 1)).ranges
        )

    def segment_exists(inv):
        return (
            measurement_exists(inv)
            and inv.indices.get("segment", 1)
            in state.channel(inv.indices.get("channel", 1)).segments
        )

    def licensed(*names):
        return lambda inv: bool(set(names) & inv.capabilities)

    fom_license = licensed("frequency_offset", "frequency-offset")
    converter_license = licensed(
        "scalar_mixer", "scalar-mixer", "frequency_converter", "frequency-converter"
    )
    embedded_license = licensed("embedded_lo", "embedded-lo")

    def add(
        path, handler, *, query=False, parameters=(), available=None, exists=measurement_exists
    ):
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

    add(
        (*fom, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "fom_enabled", value),
        parameters=(boolean,),
        available=fom_license,
    )
    add(
        (*fom, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).fom_enabled),
        query=True,
        available=fom_license,
    )
    add(
        (*fom, HeaderNode("RANGe"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).ranges)),
        query=True,
        available=fom_license,
    )
    add(
        (*fom, range_node, HeaderNode("ADD")),
        lambda inv: state.add_range(inv.indices["channel"], inv.indices["range"]) or "",
        available=fom_license,
    )
    add(
        (*fom, range_node, HeaderNode("DELete")),
        lambda inv: state.delete_range(inv.indices["channel"], inv.indices["range"]) or "",
        available=fom_license,
        exists=range_exists,
    )
    for header, attribute in (("STARt", "start"), ("STOP", "stop")):
        path = (*fom, range_node, HeaderNode("FREQuency"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_range_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=fom_license,
            exists=range_exists,
        )
        add(
            path,
            lambda inv, name=attribute: str(
                getattr(state.range(inv.indices["channel"], inv.indices["range"]), name)
            ),
            query=True,
            available=fom_license,
            exists=range_exists,
        )
    add(
        (*fom, range_node, HeaderNode("ROLE")),
        lambda inv, value: _set(
            state.range(inv.indices["channel"], inv.indices["range"]), "role", value
        ),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("INPut", "OUTPut", "LO")),),
        available=fom_license,
        exists=range_exists,
    )
    add(
        (*fom, range_node, HeaderNode("ROLE")),
        lambda inv: state.range(inv.indices["channel"], inv.indices["range"]).role,
        query=True,
        available=fom_license,
        exists=range_exists,
    )

    add(
        (*mixer, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "mixer_enabled", value),
        parameters=(boolean,),
        available=converter_license,
    )
    add(
        (*mixer, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).mixer_enabled),
        query=True,
        available=converter_license,
    )
    for header, attribute in (
        ("FIXed", "fixed_frequency"),
        ("LO", "lo_frequency"),
        ("IF", "if_frequency"),
    ):
        path = (*mixer, HeaderNode("FREQuency"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=converter_license,
        )
        add(
            path,
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True,
            available=converter_license,
        )
    add(
        (*mixer, HeaderNode("MODE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "mode", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("UPConverter", "DOWNconverter")),),
        available=converter_license,
    )
    add(
        (*mixer, HeaderNode("MODE")),
        lambda inv: state.channel(inv.indices["channel"]).mode,
        query=True,
        available=converter_license,
    )
    add(
        (*mixer, HeaderNode("CONVerter"), HeaderNode("TYPE")),
        lambda inv, value: _set_converter_type(state, inv, value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("SCALar", "VECTor")),),
        available=converter_license,
    )
    add(
        (*mixer, HeaderNode("CONVerter"), HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).converter_type,
        query=True,
        available=converter_license,
    )
    add(
        (*mixer, source_node, HeaderNode("ROLE")),
        lambda inv, value: (
            state.set_source_role(inv.indices["channel"], inv.indices["source"], value) or ""
        ),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("RF", "LO", "IF", "OFF")),),
        available=converter_license,
    )
    add(
        (*mixer, source_node, HeaderNode("ROLE")),
        lambda inv: state.source_role(inv.indices["channel"], inv.indices["source"]),
        query=True,
        available=converter_license,
    )
    add(
        (*mixer, HeaderNode("RECalculate")),
        lambda inv: state.recalculate(inv.indices["channel"]) or "",
        available=converter_license,
    )

    add(
        (*mixer, HeaderNode("SEGMent"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).segments)),
        query=True,
        available=converter_license,
    )
    add(
        (*mixer, segment_node, HeaderNode("ADD")),
        lambda inv: state.add_segment(inv.indices["channel"], inv.indices["segment"]) or "",
        available=converter_license,
    )
    add(
        (*mixer, segment_node, HeaderNode("DELete")),
        lambda inv: state.delete_segment(inv.indices["channel"], inv.indices["segment"]) or "",
        available=converter_license,
        exists=segment_exists,
    )
    add(
        (*mixer, segment_node, HeaderNode("CALCulate")),
        lambda inv: state.recalculate(inv.indices["channel"]) or "",
        available=converter_license,
        exists=segment_exists,
    )
    for header, attribute in (("STARt", "start"), ("STOP", "stop")):
        path = (*mixer, segment_node, HeaderNode("FREQuency"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_segment_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=converter_license,
            exists=segment_exists,
        )
        add(
            path,
            lambda inv, name=attribute: str(
                getattr(state.segment(inv.indices["channel"], inv.indices["segment"]), name)
            ),
            query=True,
            available=converter_license,
            exists=segment_exists,
        )
    add(
        (*mixer, segment_node, HeaderNode("POWer")),
        lambda inv, value: _set(
            state.segment(inv.indices["channel"], inv.indices["segment"]),
            "power",
            float(value.value),
        ),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
        ),
        available=converter_license,
        exists=segment_exists,
    )
    add(
        (*mixer, segment_node, HeaderNode("POWer")),
        lambda inv: str(state.segment(inv.indices["channel"], inv.indices["segment"]).power),
        query=True,
        available=converter_license,
        exists=segment_exists,
    )
    points_path = (*mixer, segment_node, HeaderNode("SWEep"), HeaderNode("POINts"))
    add(
        points_path,
        lambda inv, value: _set(
            state.segment(inv.indices["channel"], inv.indices["segment"]), "points", value
        ),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=2, maximum=100001),),
        available=converter_license,
        exists=segment_exists,
    )
    add(
        points_path,
        lambda inv: str(state.segment(inv.indices["channel"], inv.indices["segment"]).points),
        query=True,
        available=converter_license,
        exists=segment_exists,
    )

    elo = (*mixer, HeaderNode("ELO"))
    add(
        (*elo, HeaderNode("STATe")),
        lambda inv, value: _set(
            state.channel(inv.indices["channel"]), "embedded_lo_enabled", value
        ),
        parameters=(boolean,),
        available=embedded_license,
    )
    add(
        (*elo, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).embedded_lo_enabled),
        query=True,
        available=embedded_license,
    )
    for header, attribute in (("CENTer", "embedded_lo_center"), ("SPAN", "embedded_lo_span")):
        add(
            (*elo, HeaderNode(header)),
            lambda inv, value, name=attribute: _set_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=embedded_license,
        )
        add(
            (*elo, HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True,
            available=embedded_license,
        )

    add(
        (*mixer, HeaderNode("CALibration"), HeaderNode("STATe")),
        lambda inv: "0",
        query=True,
        available=converter_license,
    )
    add(
        (*fom, HeaderNode("CORRection"), HeaderNode("STATe")),
        lambda inv: "0",
        query=True,
        available=fom_license,
    )


def _set(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _set_frequency(state, invocation, attribute: str, value: NumericValue) -> str:
    state.set_frequency(invocation.indices["channel"], attribute, _number(value))
    return ""


def _set_converter_type(state, invocation, value: str) -> str:
    vector_capabilities = {"frequency_converter", "frequency-converter"}
    if value == "VECTor" and not vector_capabilities & invocation.capabilities:
        raise SCPICommandError(-224, "Illegal parameter value; vector converter is not licensed")
    return _set(state.channel(invocation.indices["channel"]), "converter_type", value)


def _set_range_frequency(state, invocation, attribute: str, value: NumericValue) -> str:
    frequency = _number(value)
    state._frequency(frequency)
    item = state.range(invocation.indices["channel"], invocation.indices["range"])
    if attribute == "start" and frequency > item.stop:
        raise SCPICommandError(-222, "Data out of range; FOM range start")
    if attribute == "stop" and frequency < item.start:
        raise SCPICommandError(-222, "Data out of range; FOM range stop")
    setattr(item, attribute, frequency)
    return ""


def _set_segment_frequency(state, invocation, attribute: str, value: NumericValue) -> str:
    frequency = _number(value)
    state._frequency(frequency)
    item = state.segment(invocation.indices["channel"], invocation.indices["segment"])
    if attribute == "start" and frequency > item.stop:
        raise SCPICommandError(-222, "Data out of range; mixer segment start")
    if attribute == "stop" and frequency < item.start:
        raise SCPICommandError(-222, "Data out of range; mixer segment stop")
    setattr(item, attribute, frequency)
    return ""


def _number(value: NumericValue) -> float:
    scale = {None: 1, "HZ": 1, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[value.unit]
    return float(value.value) * scale


def _linear(start: float, stop: float, points: int) -> tuple[float, ...]:
    if points <= 1:
        return (start,)
    step = (stop - start) / (points - 1)
    return tuple(start + index * step for index in range(points))


def _resample(samples: tuple[complex, ...], points: int) -> tuple[complex, ...]:
    if points == len(samples):
        return samples
    if not samples:
        return (0j,) * points
    if points == 1:
        return (samples[0],)
    return tuple(
        samples[round(index * (len(samples) - 1) / (points - 1))] for index in range(points)
    )


def _bool(value: bool) -> str:
    return "1" if value else "0"
