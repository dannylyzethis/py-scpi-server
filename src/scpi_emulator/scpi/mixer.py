"""VNA frequency-offset, converter, mixer-segment, and embedded-LO behavior."""

from __future__ import annotations

import cmath
import math
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
class FrequencyOffsetSegment:
    number: int
    start: float
    stop: float
    bandwidth: float = 1e3
    powers: dict[int, float] = field(default_factory=dict)
    points: int = 21
    sweep_time: float = 1.0
    enabled: bool = False


@dataclass
class FrequencyOffsetRange:
    number: int
    start: float
    stop: float
    role: str = "OUTPut"
    enabled: bool = True
    name: str = "Primary"
    coupled: bool = False
    cw_frequency: float = 1e9
    divisor: float = 1.0
    multiplier: float = 1.0
    offset: float = 0.0
    sweep_type: str = "LINear"
    bandwidth_control: bool = False
    power_control: bool = False
    sweep_time_control: bool = False
    segments: dict[int, FrequencyOffsetSegment] = field(default_factory=dict)


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
    fom_display_range: int = 1
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
    avoid_spurs: bool = True
    input_frequency_start: float = 1e9
    input_frequency_stop: float = 2e9
    input_frequency_mode: str = "FIXED"
    input_numerator: float = 1.0
    input_denominator: float = 1.0
    input_power_start: float = -20.0
    input_power_stop: float = 0.0
    input_power: float = 0.0
    input_use_nominal: bool = True
    lo_frequency_start: float = 1e9
    lo_frequency_stop: float = 2e9
    lo_frequency_mode: str = "FIXED"
    lo_numerator: float = 1.0
    lo_denominator: float = 1.0
    lo_power_start: float = -20.0
    lo_power_stop: float = -10.0
    if_frequency_start: float = 1e6
    if_frequency_stop: float = 10e6
    if_sideband: str = "LOW"
    output_frequency_start: float = 1e9
    output_frequency_stop: float = 2e9
    output_frequency_fixed: float = 1e9
    output_frequency_mode: str = "FIXED"
    output_sideband: str = "LOW"
    phase_enabled: bool = False
    phase_absolute: bool = False
    input_port: int = 1
    output_port: int = 2
    reverse_enabled: bool = True
    normalize_point: int = 1
    stage_count: int = 1
    x_axis: str = "OUTPut"
    standard_setup_applied: bool = False
    lo_settings: dict[int, dict[str, object]] = field(default_factory=dict)
    embedded_lo_delta: float = 0.0
    embedded_lo_normalize_point: int = 1
    embedded_lo_ifbw: float = 30e3
    embedded_lo_interval: int = 1
    embedded_lo_iterations: int = 5
    embedded_lo_mode: str = "BROadband"
    embedded_lo_noise_bandwidth: float = 3.2e3
    embedded_lo_tolerance: float = 1.0


class VNAMixerSystem:
    """Translate generic traces through deterministic converter configuration."""

    def __init__(
        self,
        measurements: VNAMeasurementSystem,
        frequency_minimum: float,
        frequency_maximum: float,
        source_count: int,
        port_count: int = 2,
    ) -> None:
        self.measurements = measurements
        self.frequency_minimum = frequency_minimum
        self.frequency_maximum = frequency_maximum
        self.source_count = source_count
        self.port_count = port_count
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
                if selected.sweep_type == "CW":
                    axis = (selected.cw_frequency,) * len(stimulus)
                elif selected.coupled:
                    axis = tuple(
                        point * selected.multiplier / selected.divisor + selected.offset
                        for point in stimulus
                    )
                elif selected.sweep_type == "SEGMent" and selected.segments:
                    axis = (
                        tuple(
                            point
                            for segment in sorted(
                                selected.segments.values(), key=lambda item: item.number
                            )
                            if segment.enabled
                            for point in _linear(segment.start, segment.stop, segment.points)
                        )
                        or axis
                    )
                elif selected.sweep_type == "LOGarithmic":
                    axis = _logarithmic(selected.start, selected.stop, len(stimulus))
                else:
                    axis = _linear(selected.start, selected.stop, len(stimulus))
        if channel.mixer_enabled:
            if channel.standard_setup_applied:
                return self._standard_axis(channel, len(stimulus))
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
        lo_phase = channel.embedded_lo_delta / max(channel.embedded_lo_center, 1.0)
        if channel.embedded_lo_enabled:
            lo_phase += channel.embedded_lo_span / max(channel.embedded_lo_center, 1.0)
        if channel.phase_enabled:
            lo_phase += 0.01 if channel.phase_absolute else 0.005
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
            number,
            self.frequency_minimum,
            self.frequency_maximum,
            name=f"Range{number}",
        )

    def delete_range(self, channel: int, number: int) -> None:
        if self.channel(channel).ranges.pop(number, None) is None:
            raise SCPICommandError(-200, "Execution error; FOM range does not exist")

    def range(self, channel: int, number: int) -> FrequencyOffsetRange:
        try:
            return self.channel(channel).ranges[number]
        except KeyError as exc:
            raise SCPICommandError(-200, "Execution error; FOM range does not exist") from exc

    def add_fom_segment(self, channel: int, range_number: int, number: int) -> None:
        target = self.range(channel, range_number)
        if number in target.segments:
            raise SCPICommandError(-200, "Execution error; FOM segment exists")
        target.segments[number] = FrequencyOffsetSegment(number, target.start, target.stop)

    def delete_fom_segment(self, channel: int, range_number: int, number: int) -> None:
        target = self.range(channel, range_number)
        if target.segments.pop(number, None) is None:
            raise SCPICommandError(-200, "Execution error; FOM segment does not exist")

    def fom_segment(self, channel: int, range_number: int, number: int) -> FrequencyOffsetSegment:
        try:
            return self.range(channel, range_number).segments[number]
        except KeyError as exc:
            raise SCPICommandError(-200, "Execution error; FOM segment does not exist") from exc

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

    def apply(self, channel: int) -> None:
        state = self.channel(channel)
        state.standard_setup_applied = True
        state.mixer_enabled = True
        self.recalculate(channel)

    def _standard_axis(self, channel: MixerChannel, points: int) -> tuple[float, ...]:
        if channel.x_axis == "INPut":
            if channel.input_frequency_mode == "FIXED":
                return (channel.fixed_frequency,) * points
            return _linear(channel.input_frequency_start, channel.input_frequency_stop, points)
        if channel.x_axis in {"LO_1", "LO_2"}:
            number = int(channel.x_axis[-1])
            mode = self.lo_value(channel, number, "mode")
            fixed = self.lo_value(channel, number, "fixed")
            if mode == "FIXED":
                return (float(fixed),) * points
            return _linear(
                float(self.lo_value(channel, number, "start")),
                float(self.lo_value(channel, number, "stop")),
                points,
            )
        if channel.output_frequency_mode == "FIXED":
            return (channel.output_frequency_fixed,) * points
        return _linear(channel.output_frequency_start, channel.output_frequency_stop, points)

    def lo_value(self, channel: MixerChannel, number: int, name: str):
        self._lo(channel, number)
        defaults = {
            "start": channel.lo_frequency_start,
            "stop": channel.lo_frequency_stop,
            "fixed": channel.lo_frequency,
            "mode": channel.lo_frequency_mode,
            "numerator": channel.lo_numerator,
            "denominator": channel.lo_denominator,
            "power_start": channel.lo_power_start,
            "power_stop": channel.lo_power_stop,
            "power": 0.0,
            "ilti": True,
            "name": "Not Controlled",
        }
        return channel.lo_settings.get(number, {}).get(name, defaults[name])

    def set_lo_value(self, channel_number: int, number: int, name: str, value) -> None:
        channel = self.channel(channel_number)
        self._lo(channel, number)
        channel.lo_settings.setdefault(number, {})[name] = value
        if number == 1:
            aliases = {
                "start": "lo_frequency_start",
                "stop": "lo_frequency_stop",
                "fixed": "lo_frequency",
                "mode": "lo_frequency_mode",
                "numerator": "lo_numerator",
                "denominator": "lo_denominator",
                "power_start": "lo_power_start",
                "power_stop": "lo_power_stop",
            }
            if name in aliases:
                setattr(channel, aliases[name], value)

    def _effective_lo(self, channel: MixerChannel) -> float:
        if channel.embedded_lo_enabled:
            return channel.embedded_lo_center + channel.embedded_lo_delta
        return channel.lo_frequency

    def _frequency(self, value: float) -> None:
        if not self.frequency_minimum <= value <= self.frequency_maximum:
            raise SCPICommandError(-222, "Data out of range; converter frequency")

    def _source(self, source: int) -> None:
        if not 1 <= source <= self.source_count:
            raise SCPICommandError(-222, "Data out of range; source number")

    @staticmethod
    def _lo(channel: MixerChannel, number: int) -> None:
        if not 1 <= number <= channel.stage_count:
            raise SCPICommandError(-222, "Data out of range; mixer LO number")


def register_mixer_commands(registry: CommandRegistry, state: VNAMixerSystem) -> None:
    """Register profile-gated FOM, mixer, segment, and embedded-LO commands."""
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    fom = (sense, HeaderNode("FOM"))
    mixer = (sense, HeaderNode("MIXer"))
    range_node = HeaderNode("RANGe", index="range", index_default=1)
    fom_segment_node = HeaderNode("SEGMent", index="fom_segment", index_default=1)
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

    def fom_segment_exists(inv):
        if not range_exists(inv):
            return False
        return (
            inv.indices.get("fom_segment", 1)
            in state.range(inv.indices["channel"], inv.indices.get("range", 1)).segments
        )

    def option_enabled(*names):
        return lambda inv: bool(set(names) & inv.capabilities)

    fom_option = option_enabled("frequency_offset", "frequency-offset")
    converter_option = option_enabled(
        "scalar_mixer", "scalar-mixer", "frequency_converter", "frequency-converter"
    )
    embedded_option = option_enabled("embedded_lo", "embedded-lo")

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

    def channel_pair(path, attribute, parameter, *, available, transform=lambda value: value):
        add(
            path,
            lambda inv, value, name=attribute, convert=transform: _set(
                state.channel(inv.indices["channel"]), name, convert(value)
            ),
            parameters=(parameter,),
            available=available,
        )
        add(
            path,
            lambda inv, name=attribute: _render(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=available,
        )

    def selected_fom_segment(inv):
        return state.fom_segment(
            inv.indices["channel"], inv.indices["range"], inv.indices["fom_segment"]
        )

    add(
        fom,
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "fom_enabled", value),
        parameters=(boolean,),
        available=fom_option,
    )
    add(
        fom,
        lambda inv: _bool(state.channel(inv.indices["channel"]).fom_enabled),
        query=True,
        available=fom_option,
    )
    add(
        (*fom, HeaderNode("CATalog")),
        lambda inv: ",".join(
            item.name
            for item in sorted(
                state.channel(inv.indices["channel"]).ranges.values(),
                key=lambda item: item.number,
            )
        ),
        query=True,
        available=fom_option,
    )
    add(
        (*fom, HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).ranges)),
        query=True,
        available=fom_option,
    )
    add(
        (*fom, HeaderNode("DISPlay"), HeaderNode("SELect")),
        lambda inv, value: _set_fom_display_range(state, inv.indices["channel"], value),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=1),),
        available=fom_option,
    )
    add(
        (*fom, HeaderNode("DISPlay"), HeaderNode("SELect")),
        lambda inv: str(state.channel(inv.indices["channel"]).fom_display_range),
        query=True,
        available=fom_option,
    )
    add(
        (*fom, HeaderNode("RNUMber")),
        lambda inv, name: _range_number(state, inv.indices["channel"], name),
        parameters=(ParameterSpec(ParameterType.STRING),),
        query=True,
        available=fom_option,
    )

    add(
        (*fom, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "fom_enabled", value),
        parameters=(boolean,),
        available=fom_option,
    )
    add(
        (*fom, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).fom_enabled),
        query=True,
        available=fom_option,
    )
    add(
        (*fom, HeaderNode("RANGe"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).ranges)),
        query=True,
        available=fom_option,
    )
    add(
        (*fom, range_node, HeaderNode("ADD")),
        lambda inv: state.add_range(inv.indices["channel"], inv.indices["range"]) or "",
        available=fom_option,
    )
    add(
        (*fom, range_node, HeaderNode("DELete")),
        lambda inv: state.delete_range(inv.indices["channel"], inv.indices["range"]) or "",
        available=fom_option,
        exists=range_exists,
    )
    for header, attribute in (("STARt", "start"), ("STOP", "stop")):
        path = (*fom, range_node, HeaderNode("FREQuency"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_range_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=fom_option,
            exists=range_exists,
        )
        add(
            path,
            lambda inv, name=attribute: str(
                getattr(state.range(inv.indices["channel"], inv.indices["range"]), name)
            ),
            query=True,
            available=fom_option,
            exists=range_exists,
        )
    add(
        (*fom, range_node, HeaderNode("ROLE")),
        lambda inv, value: _set(
            state.range(inv.indices["channel"], inv.indices["range"]), "role", value
        ),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("INPut", "OUTPut", "LO")),),
        available=fom_option,
        exists=range_exists,
    )
    add(
        (*fom, range_node, HeaderNode("NAME")),
        lambda inv, value: _set(
            state.range(inv.indices["channel"], inv.indices["range"]), "name", value
        ),
        parameters=(ParameterSpec(ParameterType.STRING),),
        available=fom_option,
        exists=range_exists,
    )
    add(
        (*fom, range_node, HeaderNode("NAME")),
        lambda inv: state.range(inv.indices["channel"], inv.indices["range"]).name,
        query=True,
        available=fom_option,
        exists=range_exists,
    )
    for path, attribute, parameter, transform in (
        ((HeaderNode("COUPled"),), "coupled", boolean, lambda value: value),
        ((HeaderNode("FREQuency"), HeaderNode("CW")), "cw_frequency", frequency, _number),
        (
            (HeaderNode("FREQuency"), HeaderNode("DIVisor")),
            "divisor",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal("1e-12")),
            _number,
        ),
        (
            (HeaderNode("FREQuency"), HeaderNode("MULTiplier")),
            "multiplier",
            ParameterSpec(ParameterType.NUMBER),
            _number,
        ),
        (
            (HeaderNode("FREQuency"), HeaderNode("OFFSet")),
            "offset",
            ParameterSpec(ParameterType.NUMBER, units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"})),
            _number,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("TYPE")),
            "sweep_type",
            ParameterSpec(ParameterType.ENUM, choices=("LINear", "LOGarithmic", "SEGMent", "CW")),
            lambda value: value,
        ),
    ):
        add(
            (*fom, range_node, *path),
            lambda inv, value, name=attribute, convert=transform: _set(
                state.range(inv.indices["channel"], inv.indices["range"]),
                name,
                convert(value),
            ),
            parameters=(parameter,),
            available=fom_option,
            exists=range_exists,
        )
        add(
            (*fom, range_node, *path),
            lambda inv, name=attribute: _render(
                getattr(state.range(inv.indices["channel"], inv.indices["range"]), name)
            ),
            query=True,
            available=fom_option,
            exists=range_exists,
        )
    fom_segment = (*fom, range_node, fom_segment_node)
    add(
        (*fom_segment, HeaderNode("ADD")),
        lambda inv: (
            state.add_fom_segment(
                inv.indices["channel"], inv.indices["range"], inv.indices["fom_segment"]
            )
            or ""
        ),
        available=fom_option,
        exists=range_exists,
    )
    add(
        (*fom_segment, HeaderNode("DELete")),
        lambda inv: (
            state.delete_fom_segment(
                inv.indices["channel"], inv.indices["range"], inv.indices["fom_segment"]
            )
            or ""
        ),
        available=fom_option,
        exists=fom_segment_exists,
    )
    add(
        (*fom, range_node, HeaderNode("SEGMent"), HeaderNode("DELete"), HeaderNode("ALL")),
        lambda inv: (
            state.range(inv.indices["channel"], inv.indices["range"]).segments.clear() or ""
        ),
        available=fom_option,
        exists=range_exists,
    )
    add(
        (*fom, range_node, HeaderNode("SEGMent"), HeaderNode("COUNt")),
        lambda inv: str(len(state.range(inv.indices["channel"], inv.indices["range"]).segments)),
        query=True,
        available=fom_option,
        exists=range_exists,
    )
    add(
        fom_segment,
        lambda inv, value: _set(selected_fom_segment(inv), "enabled", value),
        parameters=(boolean,),
        available=fom_option,
        exists=fom_segment_exists,
    )
    add(
        fom_segment,
        lambda inv: _bool(selected_fom_segment(inv).enabled),
        query=True,
        available=fom_option,
        exists=fom_segment_exists,
    )
    fom_segment_bandwidth = (*fom_segment, HeaderNode("BWIDth"), HeaderNode("RESolution"))
    add(
        fom_segment_bandwidth,
        lambda inv, value: _set(selected_fom_segment(inv), "bandwidth", _number(value)),
        parameters=(frequency,),
        available=fom_option,
        exists=fom_segment_exists,
    )
    add(
        fom_segment_bandwidth,
        lambda inv: _render(selected_fom_segment(inv).bandwidth),
        query=True,
        available=fom_option,
        exists=fom_segment_exists,
    )
    for branch, attribute in (
        (
            (HeaderNode("BWIDth"), HeaderNode("RESolution"), HeaderNode("CONTrol")),
            "bandwidth_control",
        ),
        ((HeaderNode("POWer"), HeaderNode("LEVel"), HeaderNode("CONTrol")), "power_control"),
        ((HeaderNode("SWEep"), HeaderNode("TIME"), HeaderNode("CONTrol")), "sweep_time_control"),
    ):
        path = (*fom, range_node, HeaderNode("SEGMent"), *branch)
        add(
            path,
            lambda inv, value, name=attribute: _set(
                state.range(inv.indices["channel"], inv.indices["range"]), name, value
            ),
            parameters=(boolean,),
            available=fom_option,
            exists=range_exists,
        )
        add(
            path,
            lambda inv, name=attribute: _bool(
                getattr(state.range(inv.indices["channel"], inv.indices["range"]), name)
            ),
            query=True,
            available=fom_option,
            exists=range_exists,
        )
    for header, attribute in (
        ("CENTer", "center"),
        ("SPAN", "span"),
        ("STARt", "start"),
        ("STOP", "stop"),
    ):
        path = (*fom_segment, HeaderNode("FREQuency"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_fom_segment_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=fom_option,
            exists=fom_segment_exists,
        )
        add(
            path,
            lambda inv, name=attribute: _render(
                _fom_segment_frequency(selected_fom_segment(inv), name)
            ),
            query=True,
            available=fom_option,
            exists=fom_segment_exists,
        )
    fom_power = HeaderNode("POWer", index="port", index_default=1)
    add(
        (*fom_segment, fom_power),
        lambda inv, value: _set_fom_segment_power(state, selected_fom_segment(inv), inv, value),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
        ),
        available=fom_option,
        exists=fom_segment_exists,
    )
    add(
        (*fom_segment, fom_power),
        lambda inv: _render(selected_fom_segment(inv).powers.get(inv.indices["port"], 0.0)),
        query=True,
        available=fom_option,
        exists=fom_segment_exists,
    )
    for path, attribute, parameter, transform in (
        (
            (HeaderNode("SWEep"), HeaderNode("POINts")),
            "points",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=20001),
            lambda value: value,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("TIME")),
            "sweep_time",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(100)),
            _number,
        ),
    ):
        add(
            (*fom_segment, *path),
            lambda inv, value, name=attribute, convert=transform: _set(
                selected_fom_segment(inv), name, convert(value)
            ),
            parameters=(parameter,),
            available=fom_option,
            exists=fom_segment_exists,
        )
        add(
            (*fom_segment, *path),
            lambda inv, name=attribute: _render(getattr(selected_fom_segment(inv), name)),
            query=True,
            available=fom_option,
            exists=fom_segment_exists,
        )
    add(
        (*fom, range_node, HeaderNode("ROLE")),
        lambda inv: state.range(inv.indices["channel"], inv.indices["range"]).role,
        query=True,
        available=fom_option,
        exists=range_exists,
    )

    add(
        (*mixer, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "mixer_enabled", value),
        parameters=(boolean,),
        available=converter_option,
    )
    add(
        (*mixer, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).mixer_enabled),
        query=True,
        available=converter_option,
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
            available=converter_option,
        )
        add(
            path,
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True,
            available=converter_option,
        )
    add(
        (*mixer, HeaderNode("MODE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "mode", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("UPConverter", "DOWNconverter")),),
        available=converter_option,
    )
    add(
        (*mixer, HeaderNode("MODE")),
        lambda inv: state.channel(inv.indices["channel"]).mode,
        query=True,
        available=converter_option,
    )
    add(
        (*mixer, HeaderNode("CONVerter"), HeaderNode("TYPE")),
        lambda inv, value: _set_converter_type(state, inv, value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("SCALar", "VECTor")),),
        available=converter_option,
    )
    add(
        (*mixer, HeaderNode("CONVerter"), HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).converter_type,
        query=True,
        available=converter_option,
    )
    add(
        (*mixer, source_node, HeaderNode("ROLE")),
        lambda inv, value: (
            state.set_source_role(inv.indices["channel"], inv.indices["source"], value) or ""
        ),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("RF", "LO", "IF", "OFF")),),
        available=converter_option,
    )
    add(
        (*mixer, source_node, HeaderNode("ROLE")),
        lambda inv: state.source_role(inv.indices["channel"], inv.indices["source"]),
        query=True,
        available=converter_option,
    )
    add(
        (*mixer, HeaderNode("RECalculate")),
        lambda inv: state.recalculate(inv.indices["channel"]) or "",
        available=converter_option,
    )

    # Standard converter/mixer setup tree. These commands deliberately share
    # the state used by the earlier compact compatibility commands above.
    channel_pair(
        (*mixer, HeaderNode("AVOidspurs")),
        "avoid_spurs",
        boolean,
        available=converter_option,
    )
    for branch, prefix in (
        (HeaderNode("INPut"), "input_frequency"),
        (HeaderNode("OUTPut"), "output_frequency"),
    ):
        for header, suffix in (("STARt", "start"), ("STOP", "stop")):
            path = (*mixer, branch, HeaderNode("FREQuency"), HeaderNode(header))
            attribute = f"{prefix}_{suffix}"
            add(
                path,
                lambda inv, value, name=attribute: _set_channel_frequency(state, inv, name, value),
                parameters=(frequency,),
                available=converter_option,
            )
            add(
                path,
                lambda inv, name=attribute: _render(
                    getattr(state.channel(inv.indices["channel"]), name)
                ),
                query=True,
                available=converter_option,
            )

    for path, attribute in (
        ((HeaderNode("INPut"), HeaderNode("FREQuency"), HeaderNode("FIXed")), "fixed_frequency"),
        (
            (HeaderNode("OUTPut"), HeaderNode("FREQuency"), HeaderNode("FIXed")),
            "output_frequency_fixed",
        ),
    ):
        add(
            (*mixer, *path),
            lambda inv, value, name=attribute: _set_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=converter_option,
        )
        add(
            (*mixer, *path),
            lambda inv, name=attribute: _render(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=converter_option,
        )

    channel_pair(
        (*mixer, HeaderNode("INPut"), HeaderNode("FREQuency"), HeaderNode("MODE")),
        "input_frequency_mode",
        ParameterSpec(ParameterType.ENUM, choices=("FIXED", "SWEPT")),
        available=converter_option,
    )
    channel_pair(
        (*mixer, HeaderNode("OUTPut"), HeaderNode("FREQuency"), HeaderNode("MODE")),
        "output_frequency_mode",
        ParameterSpec(ParameterType.ENUM, choices=("FIXED", "SWEPT")),
        available=converter_option,
    )
    channel_pair(
        (*mixer, HeaderNode("INPut"), HeaderNode("FREQuency"), HeaderNode("NUMerator")),
        "input_numerator",
        ParameterSpec(ParameterType.NUMBER, minimum=Decimal("1e-12")),
        available=converter_option,
        transform=_number,
    )
    channel_pair(
        (*mixer, HeaderNode("INPut"), HeaderNode("FREQuency"), HeaderNode("DENominator")),
        "input_denominator",
        ParameterSpec(ParameterType.NUMBER, minimum=Decimal("1e-12")),
        available=converter_option,
        transform=_number,
    )
    for header, attribute in (("STARt", "input_power_start"), ("STOP", "input_power_stop")):
        channel_pair(
            (*mixer, HeaderNode("INPut"), HeaderNode("POWer"), HeaderNode(header)),
            attribute,
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
            available=converter_option,
            transform=_number,
        )
    channel_pair(
        (*mixer, HeaderNode("INPut"), HeaderNode("POWer")),
        "input_power",
        ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
        available=converter_option,
        transform=_number,
    )
    channel_pair(
        (*mixer, HeaderNode("INPut"), HeaderNode("POWer"), HeaderNode("USENominal")),
        "input_use_nominal",
        boolean,
        available=converter_option,
    )

    for header, attribute in (("STARt", "if_frequency_start"), ("STOP", "if_frequency_stop")):
        path = (*mixer, HeaderNode("IF"), HeaderNode("FREQuency"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_channel_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=converter_option,
        )
        add(
            path,
            lambda inv, name=attribute: _render(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=converter_option,
        )
    channel_pair(
        (*mixer, HeaderNode("IF"), HeaderNode("FREQuency"), HeaderNode("SIDeband")),
        "if_sideband",
        ParameterSpec(ParameterType.ENUM, choices=("LOW", "HIGH")),
        available=converter_option,
    )
    channel_pair(
        (*mixer, HeaderNode("OUTPut"), HeaderNode("FREQuency"), HeaderNode("SIDeband")),
        "output_sideband",
        ParameterSpec(ParameterType.ENUM, choices=("LOW", "HIGH")),
        available=converter_option,
    )

    lo_node = HeaderNode("LO", index="lo", index_default=1)
    for path, attribute, parameter, transform in (
        ((HeaderNode("FREQuency"), HeaderNode("STARt")), "start", frequency, _number),
        ((HeaderNode("FREQuency"), HeaderNode("STOP")), "stop", frequency, _number),
        ((HeaderNode("FREQuency"), HeaderNode("FIXed")), "fixed", frequency, _number),
        (
            (HeaderNode("FREQuency"), HeaderNode("MODE")),
            "mode",
            ParameterSpec(ParameterType.ENUM, choices=("FIXED", "SWEPT")),
            lambda value: value,
        ),
        (
            (HeaderNode("FREQuency"), HeaderNode("NUMerator")),
            "numerator",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal("1e-12")),
            _number,
        ),
        (
            (HeaderNode("FREQuency"), HeaderNode("DENominator")),
            "denominator",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal("1e-12")),
            _number,
        ),
        (
            (HeaderNode("FREQuency"), HeaderNode("ILTI")),
            "ilti",
            boolean,
            lambda value: value,
        ),
        (
            (HeaderNode("POWer"),),
            "power",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
            _number,
        ),
        (
            (HeaderNode("POWer"), HeaderNode("STARt")),
            "power_start",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
            _number,
        ),
        (
            (HeaderNode("POWer"), HeaderNode("STOP")),
            "power_stop",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
            _number,
        ),
    ):
        add(
            (*mixer, lo_node, *path),
            lambda inv, value, name=attribute, convert=transform: _set_lo_value(
                state, inv, name, convert(value)
            ),
            parameters=(parameter,),
            available=converter_option,
        )
        add(
            (*mixer, lo_node, *path),
            lambda inv, name=attribute: _render(
                state.lo_value(state.channel(inv.indices["channel"]), inv.indices["lo"], name)
            ),
            query=True,
            available=converter_option,
        )

    add(
        (*mixer, lo_node, HeaderNode("NAME")),
        lambda inv, value: _set_lo_value(state, inv, "name", value),
        parameters=(ParameterSpec(ParameterType.STRING),),
        available=converter_option,
    )
    add(
        (*mixer, lo_node, HeaderNode("NAME")),
        lambda inv: str(
            state.lo_value(state.channel(inv.indices["channel"]), inv.indices["lo"], "name")
        ),
        query=True,
        available=converter_option,
    )

    channel_pair(
        (*mixer, HeaderNode("PHASe")),
        "phase_enabled",
        boolean,
        available=converter_option,
    )
    channel_pair(
        (*mixer, HeaderNode("PHASe"), HeaderNode("ABSolute")),
        "phase_absolute",
        boolean,
        available=converter_option,
    )
    port = ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=state.port_count)
    add(
        (*mixer, HeaderNode("PMAP")),
        lambda inv, input_port, output_port: _set_port_map(
            state.channel(inv.indices["channel"]), input_port, output_port
        ),
        parameters=(port, port),
        available=converter_option,
    )
    for header, attribute in (("INPut", "input_port"), ("OUTPut", "output_port")):
        add(
            (*mixer, HeaderNode("PMAP"), HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True,
            available=converter_option,
        )
    channel_pair(
        (*mixer, HeaderNode("REVerse")),
        "reverse_enabled",
        boolean,
        available=converter_option,
    )
    channel_pair(
        (*mixer, HeaderNode("NORMalize"), HeaderNode("POINt")),
        "normalize_point",
        ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001),
        available=converter_option,
    )
    channel_pair(
        (*mixer, HeaderNode("STAGe")),
        "stage_count",
        ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=2),
        available=converter_option,
    )
    channel_pair(
        (*mixer, HeaderNode("XAXis")),
        "x_axis",
        ParameterSpec(ParameterType.ENUM, choices=("INPut", "LO_1", "LO_2", "OUTPut")),
        available=converter_option,
    )
    add(
        (*mixer, HeaderNode("APPLy")),
        lambda inv: state.apply(inv.indices["channel"]) or "",
        available=converter_option,
    )
    add(
        (*mixer, HeaderNode("CALCulate")),
        lambda inv, _target: state.recalculate(inv.indices["channel"]) or "",
        parameters=(
            ParameterSpec(
                ParameterType.ENUM,
                choices=("INPut", "BOTH", "OUTPut", "LO_1", "LO_2"),
            ),
        ),
        available=converter_option,
    )

    add(
        (*mixer, HeaderNode("SEGMent"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).segments)),
        query=True,
        available=converter_option,
    )
    add(
        (*mixer, segment_node, HeaderNode("ADD")),
        lambda inv: state.add_segment(inv.indices["channel"], inv.indices["segment"]) or "",
        available=converter_option,
    )
    add(
        (*mixer, segment_node, HeaderNode("DELete")),
        lambda inv: state.delete_segment(inv.indices["channel"], inv.indices["segment"]) or "",
        available=converter_option,
        exists=segment_exists,
    )
    add(
        (*mixer, segment_node, HeaderNode("CALCulate")),
        lambda inv: state.recalculate(inv.indices["channel"]) or "",
        available=converter_option,
        exists=segment_exists,
    )
    for header, attribute in (("STARt", "start"), ("STOP", "stop")):
        path = (*mixer, segment_node, HeaderNode("FREQuency"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_segment_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=converter_option,
            exists=segment_exists,
        )
        add(
            path,
            lambda inv, name=attribute: str(
                getattr(state.segment(inv.indices["channel"], inv.indices["segment"]), name)
            ),
            query=True,
            available=converter_option,
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
        available=converter_option,
        exists=segment_exists,
    )
    add(
        (*mixer, segment_node, HeaderNode("POWer")),
        lambda inv: str(state.segment(inv.indices["channel"], inv.indices["segment"]).power),
        query=True,
        available=converter_option,
        exists=segment_exists,
    )
    points_path = (*mixer, segment_node, HeaderNode("SWEep"), HeaderNode("POINts"))
    add(
        points_path,
        lambda inv, value: _set(
            state.segment(inv.indices["channel"], inv.indices["segment"]), "points", value
        ),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=2, maximum=100001),),
        available=converter_option,
        exists=segment_exists,
    )
    add(
        points_path,
        lambda inv: str(state.segment(inv.indices["channel"], inv.indices["segment"]).points),
        query=True,
        available=converter_option,
        exists=segment_exists,
    )

    elo = (*mixer, HeaderNode("ELO"))
    add(
        (*elo, HeaderNode("STATe")),
        lambda inv, value: _set(
            state.channel(inv.indices["channel"]), "embedded_lo_enabled", value
        ),
        parameters=(boolean,),
        available=embedded_option,
    )
    add(
        (*elo, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).embedded_lo_enabled),
        query=True,
        available=embedded_option,
    )
    for header, attribute in (("CENTer", "embedded_lo_center"), ("SPAN", "embedded_lo_span")):
        add(
            (*elo, HeaderNode(header)),
            lambda inv, value, name=attribute: _set_frequency(state, inv, name, value),
            parameters=(frequency,),
            available=embedded_option,
        )
        add(
            (*elo, HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True,
            available=embedded_option,
        )

    elo_frequency = ParameterSpec(
        ParameterType.NUMBER,
        units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
    )
    channel_pair(
        (*elo, HeaderNode("LO"), HeaderNode("DELTa")),
        "embedded_lo_delta",
        elo_frequency,
        available=embedded_option,
        transform=_number,
    )
    add(
        (*elo, HeaderNode("LO"), HeaderNode("RESet")),
        lambda inv: _set(state.channel(inv.indices["channel"]), "embedded_lo_delta", 0.0),
        available=embedded_option,
    )
    channel_pair(
        (*elo, HeaderNode("NORMalize"), HeaderNode("POINt")),
        "embedded_lo_normalize_point",
        ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001),
        available=embedded_option,
    )
    tuning = (*elo, HeaderNode("TUNing"))
    for path, attribute, parameter, transform in (
        (
            (HeaderNode("IFBW"),),
            "embedded_lo_ifbw",
            ParameterSpec(
                ParameterType.NUMBER,
                minimum=Decimal("1e-12"),
                units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
            ),
            _number,
        ),
        (
            (HeaderNode("INTerval"),),
            "embedded_lo_interval",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001),
            lambda value: value,
        ),
        (
            (HeaderNode("ITERations"),),
            "embedded_lo_iterations",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001),
            lambda value: value,
        ),
        (
            (HeaderNode("MODE"),),
            "embedded_lo_mode",
            ParameterSpec(ParameterType.ENUM, choices=("BROadband", "PRECise", "NONE")),
            lambda value: value,
        ),
        (
            (HeaderNode("NBW"),),
            "embedded_lo_noise_bandwidth",
            ParameterSpec(
                ParameterType.NUMBER,
                minimum=Decimal("1e-12"),
                units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
            ),
            _number,
        ),
        (
            (HeaderNode("SPAN"),),
            "embedded_lo_span",
            ParameterSpec(
                ParameterType.NUMBER,
                minimum=Decimal(0),
                units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
            ),
            _number,
        ),
        (
            (HeaderNode("TOLerance"),),
            "embedded_lo_tolerance",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0)),
            _number,
        ),
    ):
        channel_pair(
            (*tuning, *path),
            attribute,
            parameter,
            available=embedded_option,
            transform=transform,
        )
    add(
        (*tuning, HeaderNode("RESet")),
        lambda inv: _reset_embedded_lo(state.channel(inv.indices["channel"])),
        available=embedded_option,
    )

    add(
        (*mixer, HeaderNode("CALibration"), HeaderNode("STATe")),
        lambda inv: "0",
        query=True,
        available=converter_option,
    )
    add(
        (*fom, HeaderNode("CORRection"), HeaderNode("STATe")),
        lambda inv: "0",
        query=True,
        available=fom_option,
    )


def _set(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _set_frequency(state, invocation, attribute: str, value: NumericValue) -> str:
    state.set_frequency(invocation.indices["channel"], attribute, _number(value))
    return ""


def _set_channel_frequency(state, invocation, attribute: str, value: NumericValue) -> str:
    number = _number(value)
    if attribute.startswith("if_frequency_"):
        if number < 0:
            raise SCPICommandError(-222, "Data out of range; mixer IF frequency")
    else:
        state._frequency(number)
    target = state.channel(invocation.indices["channel"])
    peer = attribute.removesuffix("start") + "stop" if attribute.endswith("start") else None
    if peer is not None and number > getattr(target, peer):
        raise SCPICommandError(-222, "Data out of range; mixer frequency start")
    if attribute.endswith("stop"):
        peer = attribute.removesuffix("stop") + "start"
        if number < getattr(target, peer):
            raise SCPICommandError(-222, "Data out of range; mixer frequency stop")
    setattr(target, attribute, number)
    return ""


def _set_lo_value(state, invocation, attribute: str, value) -> str:
    channel_number = invocation.indices["channel"]
    lo_number = invocation.indices["lo"]
    channel = state.channel(channel_number)
    if attribute in {"start", "stop", "fixed"}:
        state._frequency(float(value))
    if attribute == "start" and float(value) > float(state.lo_value(channel, lo_number, "stop")):
        raise SCPICommandError(-222, "Data out of range; mixer LO frequency start")
    if attribute == "stop" and float(value) < float(state.lo_value(channel, lo_number, "start")):
        raise SCPICommandError(-222, "Data out of range; mixer LO frequency stop")
    state.set_lo_value(channel_number, lo_number, attribute, value)
    return ""


def _set_port_map(target: MixerChannel, input_port: int, output_port: int) -> str:
    if input_port == output_port:
        raise SCPICommandError(-224, "Illegal parameter value; input and output ports must differ")
    target.input_port = input_port
    target.output_port = output_port
    return ""


def _range_number(state: VNAMixerSystem, channel: int, name: str) -> str:
    for number, item in state.channel(channel).ranges.items():
        if item.name.casefold() == name.casefold():
            return str(number)
    raise SCPICommandError(-224, "Illegal parameter value; FOM range name")


def _set_fom_display_range(state: VNAMixerSystem, channel: int, number: int) -> str:
    state.range(channel, number)
    state.channel(channel).fom_display_range = number
    return ""


def _fom_segment_frequency(segment: FrequencyOffsetSegment, attribute: str) -> float:
    if attribute == "center":
        return (segment.start + segment.stop) / 2
    if attribute == "span":
        return segment.stop - segment.start
    return getattr(segment, attribute)


def _set_fom_segment_frequency(state, invocation, attribute: str, value: NumericValue) -> str:
    number = _number(value)
    segment = state.fom_segment(
        invocation.indices["channel"],
        invocation.indices["range"],
        invocation.indices["fom_segment"],
    )
    if attribute == "center":
        state._frequency(number)
        span = segment.stop - segment.start
        start, stop = number - span / 2, number + span / 2
        state._frequency(start)
        state._frequency(stop)
        segment.start, segment.stop = start, stop
    elif attribute == "span":
        if number < 0:
            raise SCPICommandError(-222, "Data out of range; FOM segment span")
        center = (segment.start + segment.stop) / 2
        start, stop = center - number / 2, center + number / 2
        state._frequency(start)
        state._frequency(stop)
        segment.start, segment.stop = start, stop
    elif attribute == "start":
        state._frequency(number)
        if number > segment.stop:
            raise SCPICommandError(-222, "Data out of range; FOM segment start")
        segment.start = number
    else:
        state._frequency(number)
        if number < segment.start:
            raise SCPICommandError(-222, "Data out of range; FOM segment stop")
        segment.stop = number
    return ""


def _set_fom_segment_power(
    state: VNAMixerSystem,
    segment: FrequencyOffsetSegment,
    invocation,
    value: NumericValue,
) -> str:
    port = invocation.indices["port"]
    if not 1 <= port <= state.port_count:
        raise SCPICommandError(-222, "Data out of range; FOM segment port")
    segment.powers[port] = _number(value)
    return ""


def _reset_embedded_lo(target: MixerChannel) -> str:
    target.embedded_lo_ifbw = 30e3
    target.embedded_lo_interval = 1
    target.embedded_lo_iterations = 5
    target.embedded_lo_mode = "BROadband"
    target.embedded_lo_noise_bandwidth = 3.2e3
    target.embedded_lo_span = 1e6
    target.embedded_lo_tolerance = 1.0
    return ""


def _set_converter_type(state, invocation, value: str) -> str:
    vector_capabilities = {"frequency_converter", "frequency-converter"}
    if value == "VECTor" and not vector_capabilities & invocation.capabilities:
        raise SCPICommandError(-224, "Illegal parameter value; vector converter is not enabled")
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


def _logarithmic(start: float, stop: float, points: int) -> tuple[float, ...]:
    if start <= 0 or stop <= 0:
        raise SCPICommandError(-222, "Data out of range; logarithmic FOM frequency")
    if points <= 1:
        return (start,)
    first = math.log10(start)
    step = (math.log10(stop) - first) / (points - 1)
    return tuple(10 ** (first + index * step) for index in range(points))


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


def _render(value) -> str:
    if isinstance(value, bool):
        return _bool(value)
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)
