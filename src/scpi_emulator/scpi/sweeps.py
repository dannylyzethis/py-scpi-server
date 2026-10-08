"""VNA stimulus, source, and sweep configuration tied to acquisition timing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock

from .acquisition import AcquisitionController
from .capabilities import VNACapabilities
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
    sweep_delay: float = 0.0
    generation: str = "ANALog"
    sweep_time: float = 0.0
    shift_lo: bool = False
    receiver_reference_attenuation: float = 35.0
    receiver_test_attenuation: float = 35.0
    port_if_bandwidth: dict[int, float] = field(default_factory=dict)
    port_power: dict[int, float] = field(default_factory=dict)
    port_receiver_reference_attenuation: dict[int, float] = field(default_factory=dict)
    port_receiver_test_attenuation: dict[int, float] = field(default_factory=dict)

    def axis(self) -> tuple[float, ...]:
        return _linear(self.frequency_start, self.frequency_stop, self.points)


@dataclass
class VNASweepChannel:
    number: int
    frequency_start: float
    frequency_stop: float
    frequency_cw: float
    points: int = 201
    sweep_type: str = "LINear"
    if_bandwidth: float = 1_000.0
    dwell: float = 0.0
    dwell_auto: bool = True
    sweep_delay: float = 0.0
    power_start: float = -10.0
    power_stop: float = 0.0
    port_power: dict[int, float] = field(default_factory=dict)
    port_power_start: dict[int, float] = field(default_factory=dict)
    port_power_stop: dict[int, float] = field(default_factory=dict)
    source_attenuation: dict[int, float] = field(default_factory=dict)
    source_attenuation_auto: dict[int, bool] = field(default_factory=dict)
    source_reference_attenuation: dict[int, float] = field(default_factory=dict)
    source_test_attenuation: dict[int, float] = field(default_factory=dict)
    source_alc_mode: dict[int, str] = field(default_factory=dict)
    source_mode: dict[int, str] = field(default_factory=dict)
    receiver_attenuation: dict[str, float] = field(default_factory=dict)
    segments: list[SegmentState] = field(default_factory=list)
    generation: str = "ANALog"
    point_sweep: bool = False
    low_frequency_extension: bool = False
    shift_lo_maximum: float = 0.0
    shift_lo_enabled: bool = False
    speed: str = "NORMal"
    sweep_time_auto: bool = True
    manual_sweep_time: float = 0.0
    trigger_mode: str = "CHANnel"
    power_coupled: bool = False
    power_slope: float = 0.0
    power_slope_enabled: bool = False
    arbitrary_segments: bool = False
    segment_bandwidth_control: bool = False
    segment_power_control: bool = False
    segment_receiver_attenuation_control: bool = False
    segment_shift_lo_control: bool = False
    segment_sweep_delay_control: bool = False
    segment_dwell_control: bool = False
    segment_generation_control: bool = False
    segment_points_control: bool = False
    segment_time_control: bool = False
    segment_spacing: str = "LINear"
    segment_list: str = ""
    segment_port_bandwidth_control: dict[int, bool] = field(default_factory=dict)
    coupled: bool = False
    coupling_parameter: str = "ALL"

    @property
    def frequency_center(self) -> float:
        return (self.frequency_start + self.frequency_stop) / 2

    @property
    def frequency_span(self) -> float:
        return self.frequency_stop - self.frequency_start

    def axis(self) -> tuple[float, ...]:
        if self.sweep_type == "SEGMent":
            return tuple(
                value for segment in self.segments if segment.enabled for value in segment.axis()
            )
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
        if not self.sweep_time_auto:
            return self.manual_sweep_time
        # Deterministic approximation: one IF settling interval plus dwell per point.
        if self.sweep_type == "SEGMent":
            return sum(
                segment.points * (1 / segment.if_bandwidth + segment.dwell)
                for segment in self.segments
                if segment.enabled
            )
        return self.points * (1 / self.if_bandwidth + self.dwell)


class VNASweepSystem:
    """Maintain coherent per-channel axes and acquisition durations."""

    def __init__(
        self,
        capabilities: VNACapabilities,
        measurements: VNAMeasurementSystem,
        acquisition: AcquisitionController,
    ) -> None:
        self.capabilities = capabilities
        self.measurements = measurements
        self.acquisition = acquisition
        self.channels: dict[int, VNASweepChannel] = {}
        self.output_enabled = True
        self.manual_noise_enabled = False
        self._lock = RLock()
        measurements.axis_provider = lambda number: self.channel(number).axis()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.channels.clear()
            self.output_enabled = True
            self.manual_noise_enabled = False
            self.channel(1)
            self._synchronize(1)

    def channel(self, number: int) -> VNASweepChannel:
        with self._lock:
            if number not in self.channels:
                minimum = float(self.capabilities.frequency_minimum)
                maximum = float(self.capabilities.frequency_maximum)
                self.channels[number] = VNASweepChannel(
                    number,
                    minimum,
                    maximum,
                    (minimum + maximum) / 2,
                    shift_lo_maximum=maximum,
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
        self._validate_port(port)
        self.channel(number).port_power[port] = value

    def port_power(self, number: int, port: int) -> float:
        self._validate_port(port)
        return self.channel(number).port_power.get(port, -10.0)

    def source_port_value(self, number: int, port: int, attribute: str, default):
        self._validate_port(port)
        return getattr(self.channel(number), attribute).get(port, default)

    def set_source_port_value(self, number: int, port: int, attribute: str, value) -> None:
        self._validate_port(port)
        getattr(self.channel(number), attribute)[port] = value

    def set_source_attenuation(self, number: int, port: int, value: float) -> None:
        self.set_source_port_value(number, port, "source_attenuation", value)
        self.set_source_port_value(number, port, "source_attenuation_auto", False)

    def set_power_center(self, number: int, value: float) -> None:
        channel = self.channel(number)
        half_span = (channel.power_stop - channel.power_start) / 2
        self._validate_power(value - half_span)
        self._validate_power(value + half_span)
        channel.power_start = value - half_span
        channel.power_stop = value + half_span
        self._synchronize(number)

    def set_power_span(self, number: int, value: float) -> None:
        if value < 0:
            raise SCPICommandError(-222, "Data out of range; power span")
        channel = self.channel(number)
        center = (channel.power_start + channel.power_stop) / 2
        self._validate_power(center - value / 2)
        self._validate_power(center + value / 2)
        channel.power_start = center - value / 2
        channel.power_stop = center + value / 2
        self._synchronize(number)

    def set_sweep_step(self, number: int, value: float) -> None:
        if value <= 0:
            raise SCPICommandError(-222, "Data out of range; sweep step")
        channel = self.channel(number)
        points = round(channel.frequency_span / value) + 1
        if not 1 <= points <= 100001:
            raise SCPICommandError(-222, "Data out of range; sweep step")
        channel.points = points
        self._synchronize(number)

    def sweep_step(self, number: int) -> float:
        channel = self.channel(number)
        return channel.frequency_span / max(1, channel.points - 1)

    def set_sweep_time(self, number: int, value: float) -> None:
        if value < 0:
            raise SCPICommandError(-222, "Data out of range; sweep time")
        channel = self.channel(number)
        channel.manual_sweep_time = value
        channel.sweep_time_auto = False
        self._synchronize(number)

    def set_sweep_time_auto(self, number: int, enabled: bool) -> None:
        self.channel(number).sweep_time_auto = enabled
        self._synchronize(number)

    def set_dwell(self, number: int, value: float) -> None:
        channel = self.channel(number)
        channel.dwell = value
        channel.dwell_auto = value == 0
        self._synchronize(number)

    def set_dwell_auto(self, number: int, enabled: bool) -> None:
        channel = self.channel(number)
        channel.dwell_auto = enabled
        if enabled:
            channel.dwell = 0.0
        self._synchronize(number)

    def set_segment_center(self, number: int, segment_number: int, value: float) -> None:
        segment = self.segment(number, segment_number)
        half_span = (segment.frequency_stop - segment.frequency_start) / 2
        self._validate_frequency(value - half_span)
        self._validate_frequency(value + half_span)
        segment.frequency_start = value - half_span
        segment.frequency_stop = value + half_span
        self._synchronize(number)

    def set_segment_span(self, number: int, segment_number: int, value: float) -> None:
        if value < 0:
            raise SCPICommandError(-222, "Data out of range; segment frequency span")
        segment = self.segment(number, segment_number)
        center = (segment.frequency_start + segment.frequency_stop) / 2
        self._validate_frequency(center - value / 2)
        self._validate_frequency(center + value / 2)
        segment.frequency_start = center - value / 2
        segment.frequency_stop = center + value / 2
        self._synchronize(number)

    def segment_total_points(self, number: int) -> int:
        return sum(segment.points for segment in self.channel(number).segments if segment.enabled)

    def segment_total_time(self, number: int) -> float:
        return sum(
            segment.sweep_time
            if segment.sweep_time > 0
            else segment.points * (1 / segment.if_bandwidth + segment.dwell)
            for segment in self.channel(number).segments
            if segment.enabled
        )

    def segment_port_value(
        self, number: int, segment_number: int, port: int, attribute: str, default
    ):
        self._validate_port(port)
        return getattr(self.segment(number, segment_number), attribute).get(port, default)

    def set_segment_port_value(
        self, number: int, segment_number: int, port: int, attribute: str, value
    ) -> None:
        self._validate_port(port)
        getattr(self.segment(number, segment_number), attribute)[port] = value
        self._synchronize(number)

    def set_receiver_attenuation(self, number: int, receiver: str, value: float) -> None:
        self.channel(number).receiver_attenuation[receiver] = value

    def receiver_attenuation(self, number: int, receiver: str) -> float:
        return self.channel(number).receiver_attenuation.get(receiver, 0.0)

    def add_segment(self, number: int, segment_number: int) -> None:
        channel = self.channel(number)
        if not 1 <= segment_number <= len(channel.segments) + 1:
            raise SCPICommandError(-222, "Data out of range; segment number")
        segment = SegmentState(
            segment_number,
            channel.frequency_start,
            channel.frequency_stop,
            if_bandwidth=channel.if_bandwidth,
            dwell=channel.dwell,
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
        if attribute in {"power"}:
            self._validate_power(float(value))
        if attribute in {"dwell", "sweep_delay", "sweep_time"} and float(value) < 0:
            raise SCPICommandError(-222, f"Data out of range; segment {attribute}")
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
    def _renumber(channel: VNASweepChannel) -> None:
        for number, segment in enumerate(channel.segments, 1):
            segment.number = number

    def _validate_frequency(self, value: float) -> None:
        if not self.capabilities.frequency_minimum <= value <= self.capabilities.frequency_maximum:
            raise SCPICommandError(-222, "Data out of range; frequency")

    def _validate_port(self, port: int) -> None:
        if not 1 <= port <= self.capabilities.ports:
            raise SCPICommandError(-222, "Data out of range; source port")

    @staticmethod
    def _validate_power(value: float) -> None:
        if not -120 <= value <= 50:
            raise SCPICommandError(-222, "Data out of range; source power")

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


def register_sweep_commands(registry: CommandRegistry, state: VNASweepSystem) -> None:
    """Register the common VNA frequency, power, IFBW, points, and type commands."""
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    source = HeaderNode("SOURce", index="channel", index_default=1)
    power = HeaderNode("POWer", index="port", index_default=1)
    frequency = (sense, HeaderNode("FREQuency"))
    sweep = (sense, HeaderNode("SWEep"))
    segment = HeaderNode("SEGMent", index="segment", index_default=1)
    frequency_number = ParameterSpec(
        ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"})
    )
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

    def boolean_pair(path, attribute):
        add(
            path,
            lambda inv, value, attr=attribute: _empty(
                state.configure(inv.indices["channel"], attr, value)
            ),
            parameters=(ParameterSpec(ParameterType.BOOLEAN),),
        )
        add(
            path,
            lambda inv, attr=attribute: _bool(getattr(state.channel(inv.indices["channel"]), attr)),
            query=True,
        )

    for path, attribute in (
        ((HeaderNode("OUTPut"),), "output_enabled"),
        ((HeaderNode("OUTPut"), HeaderNode("STATe")), "output_enabled"),
        (
            (HeaderNode("OUTPut"), HeaderNode("MANual"), HeaderNode("NOISe")),
            "manual_noise_enabled",
        ),
        (
            (
                HeaderNode("OUTPut"),
                HeaderNode("MANual"),
                HeaderNode("NOISe"),
                HeaderNode("STATe"),
            ),
            "manual_noise_enabled",
        ),
    ):
        add(
            path,
            lambda inv, value, attr=attribute: _set_output(state, attr, value),
            parameters=(ParameterSpec(ParameterType.BOOLEAN),),
        )
        add(
            path,
            lambda inv, attr=attribute: _bool(getattr(state, attr)),
            query=True,
        )

    for header, attribute in (
        ("STARt", "frequency_start"),
        ("STOP", "frequency_stop"),
        ("CW", "frequency_cw"),
    ):
        add(
            (*frequency, HeaderNode(header)),
            lambda inv, value, attr=attribute: _set_number(state, inv, attr, value),
            parameters=(frequency_number,),
        )
        add(
            (*frequency, HeaderNode(header)),
            lambda inv, attr=attribute: _format_number(
                getattr(state.channel(inv.indices["channel"]), attr)
            ),
            query=True,
        )
    add(
        frequency,
        lambda inv, value: _set_number(state, inv, "frequency_cw", value),
        parameters=(frequency_number,),
    )
    add(
        frequency,
        lambda inv: _format_number(state.channel(inv.indices["channel"]).frequency_cw),
        query=True,
    )
    add(
        (*frequency, HeaderNode("CENTer")),
        lambda inv, value: _empty(state.set_center(inv.indices["channel"], _number(value))),
        parameters=(frequency_number,),
    )
    add(
        (*frequency, HeaderNode("CENTer")),
        lambda inv: _format_number(state.channel(inv.indices["channel"]).frequency_center),
        query=True,
    )
    add(
        (*frequency, HeaderNode("SPAN")),
        lambda inv, value: _empty(state.set_span(inv.indices["channel"], _number(value))),
        parameters=(frequency_number,),
    )
    add(
        (*frequency, HeaderNode("SPAN")),
        lambda inv: _format_number(state.channel(inv.indices["channel"]).frequency_span),
        query=True,
    )

    add(
        (*sweep, HeaderNode("POINts")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "points", value)),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001),),
    )
    add(
        (*sweep, HeaderNode("POINts")),
        lambda inv: str(state.channel(inv.indices["channel"]).points),
        query=True,
    )
    add(
        (*sweep, HeaderNode("TYPE")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "sweep_type", value)),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=SWEEP_TYPES),),
    )
    add(
        (*sweep, HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).sweep_type,
        query=True,
    )
    add(
        (*sweep, HeaderNode("DWELl")),
        lambda inv, value: _empty(state.set_dwell(inv.indices["channel"], _number(value))),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"S"})),
        ),
    )
    add(
        (*sweep, HeaderNode("DWELl")),
        lambda inv: str(state.channel(inv.indices["channel"]).dwell),
        query=True,
    )
    add(
        (*sweep, HeaderNode("BLOCked")),
        lambda inv: "0",
        query=True,
    )
    add(
        (*sweep, HeaderNode("DWELl"), HeaderNode("AUTO")),
        lambda inv, value: _empty(state.set_dwell_auto(inv.indices["channel"], value)),
        parameters=(ParameterSpec(ParameterType.BOOLEAN),),
    )
    add(
        (*sweep, HeaderNode("DWELl"), HeaderNode("AUTO")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).dwell_auto),
        query=True,
    )
    for path, attribute, maximum in (
        ((*sweep, HeaderNode("DWELl"), HeaderNode("SDELay")), "sweep_delay", 3600),
        ((*sweep, HeaderNode("TRIGger"), HeaderNode("DELay")), None, 3),
    ):
        add(
            path,
            (
                lambda inv, value, attr=attribute: (
                    _set_number(state, inv, attr, value)
                    if attr
                    else _empty(
                        state.acquisition.set_trigger_delay(_number(value), inv.indices["channel"])
                    )
                )
            ),
            parameters=(
                ParameterSpec(
                    ParameterType.NUMBER,
                    minimum=Decimal(0),
                    maximum=Decimal(maximum),
                    units=frozenset({"S"}),
                ),
            ),
        )
        add(
            path,
            (
                lambda inv, attr=attribute: (
                    str(getattr(state.channel(inv.indices["channel"]), attr))
                    if attr
                    else str(state.acquisition.trigger_delay(inv.indices["channel"]))
                )
            ),
            query=True,
        )
    add(
        (*sweep, HeaderNode("GENeration")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "generation", value)),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("STEPped", "ANALog")),),
    )
    add(
        (*sweep, HeaderNode("GENeration")),
        lambda inv: state.channel(inv.indices["channel"]).generation,
        query=True,
    )
    boolean_pair((*sweep, HeaderNode("GENeration"), HeaderNode("POINtsweep")), "point_sweep")
    boolean_pair(
        (*sweep, HeaderNode("LFEXtension"), HeaderNode("STATe")),
        "low_frequency_extension",
    )
    add(
        (*sweep, HeaderNode("SLOCal"), HeaderNode("MAXimum")),
        lambda inv, value: _set_number(state, inv, "shift_lo_maximum", value),
        parameters=(frequency_number,),
    )
    add(
        (*sweep, HeaderNode("SLOCal"), HeaderNode("MAXimum")),
        lambda inv: _format_number(state.channel(inv.indices["channel"]).shift_lo_maximum),
        query=True,
    )
    boolean_pair((*sweep, HeaderNode("SLOCal"), HeaderNode("STATe")), "shift_lo_enabled")
    add(
        (*sweep, HeaderNode("SPEed")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "speed", value)),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("FAST", "NORMal")),),
    )
    add(
        (*sweep, HeaderNode("SPEed")),
        lambda inv: state.channel(inv.indices["channel"]).speed,
        query=True,
    )
    add(
        (*sweep, HeaderNode("STEP")),
        lambda inv, value: _empty(state.set_sweep_step(inv.indices["channel"], _number(value))),
        parameters=(frequency_number,),
    )
    add(
        (*sweep, HeaderNode("STEP")),
        lambda inv: _format_number(state.sweep_step(inv.indices["channel"])),
        query=True,
    )
    add(
        (*sweep, HeaderNode("TIME")),
        lambda inv, value: _empty(state.set_sweep_time(inv.indices["channel"], _number(value))),
        parameters=(
            ParameterSpec(
                ParameterType.NUMBER,
                minimum=Decimal(0),
                maximum=Decimal(1_000_000),
                units=frozenset({"S"}),
            ),
        ),
    )
    add(
        (*sweep, HeaderNode("TIME")),
        lambda inv: str(state.channel(inv.indices["channel"]).duration),
        query=True,
    )
    add(
        (*sweep, HeaderNode("TIME"), HeaderNode("AUTO")),
        lambda inv, value: _empty(state.set_sweep_time_auto(inv.indices["channel"], value)),
        parameters=(ParameterSpec(ParameterType.BOOLEAN),),
    )
    add(
        (*sweep, HeaderNode("TIME"), HeaderNode("AUTO")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).sweep_time_auto),
        query=True,
    )
    add(
        (*sweep, HeaderNode("TRIGger"), HeaderNode("MODE")),
        lambda inv, value: _empty(state.configure(inv.indices["channel"], "trigger_mode", value)),
        parameters=(
            ParameterSpec(
                ParameterType.ENUM,
                choices=("CHANnel", "SWEep", "POINt", "TRACe"),
            ),
        ),
    )
    add(
        (*sweep, HeaderNode("TRIGger"), HeaderNode("MODE")),
        lambda inv: state.channel(inv.indices["channel"]).trigger_mode,
        query=True,
    )
    add(
        (sense, HeaderNode("BANDwidth")),
        lambda inv, value: _set_number(state, inv, "if_bandwidth", value),
        parameters=(frequency_number,),
    )
    add(
        (sense, HeaderNode("BANDwidth")),
        lambda inv: str(state.channel(inv.indices["channel"]).if_bandwidth),
        query=True,
    )
    add(
        (sense, HeaderNode("BANDwidth"), HeaderNode("RESolution")),
        lambda inv, value: _set_number(state, inv, "if_bandwidth", value),
        parameters=(frequency_number,),
    )
    add(
        (sense, HeaderNode("BANDwidth"), HeaderNode("RESolution")),
        lambda inv: str(state.channel(inv.indices["channel"]).if_bandwidth),
        query=True,
    )
    for path in (
        (sense, HeaderNode("COUPle")),
        (sense, HeaderNode("COUPle"), HeaderNode("STATe")),
    ):
        boolean_pair(path, "coupled")
    add(
        (sense, HeaderNode("COUPle"), HeaderNode("PARameter")),
        lambda inv, value: _empty(
            state.configure(inv.indices["channel"], "coupling_parameter", value)
        ),
        parameters=(
            ParameterSpec(
                ParameterType.ENUM,
                choices=("ALL", "FREQuency", "POWer", "BWIDth"),
            ),
        ),
    )
    add(
        (sense, HeaderNode("COUPle"), HeaderNode("PARameter")),
        lambda inv: state.channel(inv.indices["channel"]).coupling_parameter,
        query=True,
    )
    boolean_pair(
        (
            sense,
            HeaderNode("COUPle"),
            HeaderNode("PARameter"),
            HeaderNode("STATe"),
        ),
        "coupled",
    )
    add(
        (sense, HeaderNode("CLASs"), HeaderNode("NAME")),
        lambda inv: '"VNA"',
        query=True,
    )

    receiver = ParameterSpec(ParameterType.ENUM, choices=RECEIVERS)
    attenuation = ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(70))
    add(
        (sense, HeaderNode("POWer"), HeaderNode("ATTenuator")),
        lambda inv, name, value: _empty(
            state.set_receiver_attenuation(inv.indices["channel"], name, _number(value))
        ),
        parameters=(receiver, attenuation),
    )
    add(
        (sense, HeaderNode("POWer"), HeaderNode("ATTenuator")),
        lambda inv, name: str(state.receiver_attenuation(inv.indices["channel"], name)),
        query=True,
        parameters=(receiver,),
    )

    add(
        (sense, segment, HeaderNode("ADD")),
        lambda inv: _empty(state.add_segment(inv.indices["channel"], inv.indices["segment"])),
    )
    add(
        (sense, segment, HeaderNode("DELete")),
        lambda inv: _empty(state.delete_segment(inv.indices["channel"], inv.indices["segment"])),
    )
    add(
        (sense, HeaderNode("SEGMent"), HeaderNode("DELete"), HeaderNode("ALL")),
        lambda inv: _empty(state.delete_all_segments(inv.indices["channel"])),
    )
    add(
        (sense, HeaderNode("SEGMent"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).segments)),
        query=True,
    )
    add(
        (sense, HeaderNode("SEGMent"), HeaderNode("LIST")),
        lambda inv, value: _set_channel_value(state, inv, "segment_list", value),
        parameters=(ParameterSpec(ParameterType.STRING, "segment list"),),
    )
    add(
        (sense, HeaderNode("SEGMent"), HeaderNode("LIST")),
        lambda inv: state.channel(inv.indices["channel"]).segment_list,
        query=True,
    )
    for path, attribute in (
        ((HeaderNode("ARBitrary"),), "arbitrary_segments"),
        ((HeaderNode("BWIDth"), HeaderNode("CONTrol")), "segment_bandwidth_control"),
        (
            (
                HeaderNode("BWIDth"),
                HeaderNode("RESolution"),
                HeaderNode("CONTrol"),
            ),
            "segment_bandwidth_control",
        ),
        (
            (
                HeaderNode("POWer"),
                HeaderNode("ATTenuation"),
                HeaderNode("RECeiver"),
                HeaderNode("CONTrol"),
            ),
            "segment_receiver_attenuation_control",
        ),
        ((HeaderNode("POWer"), HeaderNode("CONTrol")), "segment_power_control"),
        (
            (HeaderNode("POWer"), HeaderNode("LEVel"), HeaderNode("CONTrol")),
            "segment_power_control",
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("TIME"), HeaderNode("CONTrol")),
            "segment_time_control",
        ),
    ):
        boolean_pair((sense, HeaderNode("SEGMent"), *path), attribute)

    segment_port_node = HeaderNode("PORT", index="segment_port", index_default=1)
    for path in (
        (sense, HeaderNode("SEGMent"), HeaderNode("BANDwidth"), segment_port_node),
        (sense, HeaderNode("SEGMent"), HeaderNode("BWIDth"), segment_port_node),
        (
            sense,
            HeaderNode("SEGMent"),
            HeaderNode("BANDwidth"),
            segment_port_node,
            HeaderNode("RESolution"),
        ),
        (
            sense,
            HeaderNode("SEGMent"),
            HeaderNode("BWIDth"),
            segment_port_node,
            HeaderNode("RESolution"),
        ),
    ):
        add(
            (*path, HeaderNode("CONTrol")),
            lambda inv, value: _set_segment_port_control(state, inv, value),
            parameters=(ParameterSpec(ParameterType.BOOLEAN),),
        )
        add(
            (*path, HeaderNode("CONTrol")),
            lambda inv: _bool(
                state.channel(inv.indices["channel"]).segment_port_bandwidth_control.get(
                    _validated_segment_port(state, inv), False
                )
            ),
            query=True,
        )

    add(
        (sense, segment),
        lambda inv, value: _set_segment_value(state, inv, "enabled", value),
        parameters=(ParameterSpec(ParameterType.BOOLEAN),),
    )
    add(
        (sense, segment),
        lambda inv: _segment_query(state, inv, "enabled"),
        query=True,
    )
    for header, attribute in (("STARt", "frequency_start"), ("STOP", "frequency_stop")):
        add(
            (sense, segment, HeaderNode("FREQuency"), HeaderNode(header)),
            lambda inv, value, attr=attribute: _set_segment_number(state, inv, attr, value),
            parameters=(frequency_number,),
        )
        add(
            (sense, segment, HeaderNode("FREQuency"), HeaderNode(header)),
            lambda inv, attr=attribute: _format_number(
                getattr(state.segment(inv.indices["channel"], inv.indices["segment"]), attr)
            ),
            query=True,
        )
    for header, setter, getter in (
        (
            "CENTer",
            state.set_segment_center,
            lambda segment_state: (
                (segment_state.frequency_start + segment_state.frequency_stop) / 2
            ),
        ),
        (
            "SPAN",
            state.set_segment_span,
            lambda segment_state: segment_state.frequency_stop - segment_state.frequency_start,
        ),
    ):
        add(
            (sense, segment, HeaderNode("FREQuency"), HeaderNode(header)),
            lambda inv, value, operation=setter: _empty(
                operation(inv.indices["channel"], inv.indices["segment"], _number(value))
            ),
            parameters=(frequency_number,),
        )
        add(
            (sense, segment, HeaderNode("FREQuency"), HeaderNode(header)),
            lambda inv, operation=getter: _format_number(
                operation(state.segment(inv.indices["channel"], inv.indices["segment"]))
            ),
            query=True,
        )
    for path, attribute, parameter in (
        (
            (HeaderNode("SWEep"), HeaderNode("POINts")),
            "points",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100001),
        ),
        ((HeaderNode("BWIDth"),), "if_bandwidth", frequency_number),
        (
            (HeaderNode("BWIDth"), HeaderNode("RESolution")),
            "if_bandwidth",
            frequency_number,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("DWELl")),
            "dwell",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"S"})),
        ),
        ((HeaderNode("STATe"),), "enabled", ParameterSpec(ParameterType.BOOLEAN)),
        (
            (HeaderNode("SWEep"), HeaderNode("DELay")),
            "sweep_delay",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"S"})),
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("GENeration")),
            "generation",
            ParameterSpec(ParameterType.ENUM, choices=("STEPped", "ANALog")),
        ),
    ):
        add(
            (sense, segment, *path),
            lambda inv, value, attr=attribute: _set_segment_value(state, inv, attr, value),
            parameters=(parameter,),
        )
        add(
            (sense, segment, *path),
            lambda inv, attr=attribute: _segment_query(state, inv, attr),
            query=True,
        )

    for path, attribute in (
        ((HeaderNode("SHLO"), HeaderNode("CONTrol")), "segment_shift_lo_control"),
        (
            (HeaderNode("SWEep"), HeaderNode("DELay"), HeaderNode("CONTrol")),
            "segment_sweep_delay_control",
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("DWELl"), HeaderNode("CONTrol")),
            "segment_dwell_control",
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("GENeration"), HeaderNode("CONTrol")),
            "segment_generation_control",
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("POINts"), HeaderNode("CONTrol")),
            "segment_points_control",
        ),
    ):
        boolean_pair((sense, segment, *path), attribute)

    add(
        (sense, segment, HeaderNode("SHLO")),
        lambda inv, value: _set_segment_value(state, inv, "shift_lo", value),
        parameters=(ParameterSpec(ParameterType.BOOLEAN),),
    )

    segment_power = HeaderNode("POWer", index="port", index_default=1)
    for path in (
        (sense, segment, segment_power),
        (sense, segment, segment_power, HeaderNode("LEVel")),
    ):
        add(
            path,
            lambda inv, value: _set_segment_port_number(state, inv, "port_power", value),
            parameters=(power_number,),
        )
        add(
            path,
            lambda inv: _format_number(
                state.segment_port_value(
                    inv.indices["channel"],
                    inv.indices["segment"],
                    inv.indices.get("port", 1),
                    "port_power",
                    state.segment(inv.indices["channel"], inv.indices["segment"]).power,
                )
            ),
            query=True,
        )

    for receiver_header, attribute in (
        ("REFerence", "port_receiver_reference_attenuation"),
        ("TEST", "port_receiver_test_attenuation"),
    ):
        path = (
            sense,
            segment,
            segment_power,
            HeaderNode("ATTenuation"),
            HeaderNode("RECeiver"),
            HeaderNode(receiver_header),
        )
        add(
            path,
            lambda inv, value, attr=attribute: _set_segment_port_number(state, inv, attr, value),
            parameters=(attenuation,),
        )
        add(
            path,
            lambda inv, attr=attribute: _format_number(
                state.segment_port_value(
                    inv.indices["channel"],
                    inv.indices["segment"],
                    inv.indices["port"],
                    attr,
                    35.0,
                )
            ),
            query=True,
        )

    segment_bandwidth_port = HeaderNode("PORT", index="port", index_default=1)
    for path in (
        (sense, segment, HeaderNode("BWIDth"), segment_bandwidth_port),
        (
            sense,
            segment,
            HeaderNode("BWIDth"),
            segment_bandwidth_port,
            HeaderNode("RESolution"),
        ),
    ):
        add(
            path,
            lambda inv, value: _set_segment_port_number(state, inv, "port_if_bandwidth", value),
            parameters=(frequency_number,),
        )
        add(
            path,
            lambda inv: _format_number(
                state.segment_port_value(
                    inv.indices["channel"],
                    inv.indices["segment"],
                    inv.indices["port"],
                    "port_if_bandwidth",
                    state.segment(inv.indices["channel"], inv.indices["segment"]).if_bandwidth,
                )
            ),
            query=True,
        )

    add(
        (sense, segment, HeaderNode("SWEep"), HeaderNode("TIME")),
        lambda inv, value: _set_segment_number(state, inv, "sweep_time", value),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"S"})),
        ),
    )
    add(
        (sense, segment, HeaderNode("SWEep"), HeaderNode("POINts"), HeaderNode("TOTal")),
        lambda inv: str(state.segment_total_points(inv.indices["channel"])),
        query=True,
    )
    add(
        (sense, segment, HeaderNode("SWEep"), HeaderNode("TIME"), HeaderNode("TOTal")),
        lambda inv: str(state.segment_total_time(inv.indices["channel"])),
        query=True,
    )
    add(
        (sense, HeaderNode("SEGMent"), HeaderNode("X"), HeaderNode("SPACing")),
        lambda inv, value: _set_channel_value(state, inv, "segment_spacing", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("LINear", "LOGarithmic")),),
    )
    add(
        (sense, HeaderNode("SEGMent"), HeaderNode("X"), HeaderNode("SPACing")),
        lambda inv: state.channel(inv.indices["channel"]).segment_spacing,
        query=True,
    )

    add(
        (source, HeaderNode("CATalog")),
        lambda inv: (
            '"' + ",".join(f"Port {port}" for port in range(1, state.capabilities.ports + 1)) + '"'
        ),
        query=True,
    )
    add(
        (source, HeaderNode("PORT"), HeaderNode("NUMber")),
        lambda inv, name: str(_source_port_number(state, name)),
        query=True,
        parameters=(
            ParameterSpec(
                ParameterType.STRING,
                "source name",
                required=False,
                default="Port 1",
            ),
        ),
    )

    power_level_paths = (
        (source, power),
        (source, power, HeaderNode("LEVel")),
        (source, power, HeaderNode("LEVel"), HeaderNode("IMMediate")),
        (
            source,
            power,
            HeaderNode("LEVel"),
            HeaderNode("IMMediate"),
            HeaderNode("AMPLitude"),
        ),
    )
    for path in power_level_paths:
        add(
            path,
            lambda inv, value: _empty(
                state.set_port_power(inv.indices["channel"], inv.indices["port"], _number(value))
            ),
            parameters=(power_number,),
        )
        add(
            path,
            lambda inv: str(state.port_power(inv.indices["channel"], inv.indices["port"])),
            query=True,
        )

    for path in (
        (source, power, HeaderNode("ALC")),
        (source, power, HeaderNode("ALC"), HeaderNode("MODE")),
    ):
        add(
            path,
            lambda inv, value: _set_source_port(state, inv, "source_alc_mode", value),
            parameters=(ParameterSpec(ParameterType.ENUM, choices=("INTernal", "OPENloop")),),
        )
        add(
            path,
            lambda inv: state.source_port_value(
                inv.indices["channel"],
                inv.indices["port"],
                "source_alc_mode",
                "INTernal",
            ),
            query=True,
        )
    for path in (
        (source, power, HeaderNode("ALC"), HeaderNode("CATalog")),
        (
            source,
            power,
            HeaderNode("ALC"),
            HeaderNode("MODE"),
            HeaderNode("CATalog"),
        ),
    ):
        add(path, lambda inv: "INTernal,OPENloop", query=True)

    attenuation_path = (source, power, HeaderNode("ATTenuation"))
    add(
        attenuation_path,
        lambda inv, value: _empty(
            state.set_source_attenuation(
                inv.indices["channel"], inv.indices["port"], _number(value)
            )
        ),
        parameters=(ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(70)),),
    )
    add(
        attenuation_path,
        lambda inv: str(
            state.source_port_value(
                inv.indices["channel"],
                inv.indices["port"],
                "source_attenuation",
                0.0,
            )
        ),
        query=True,
    )
    for path, attribute, default in (
        ((*attenuation_path, HeaderNode("AUTO")), "source_attenuation_auto", True),
        (
            (
                *attenuation_path,
                HeaderNode("RECeiver"),
                HeaderNode("REFerence"),
            ),
            "source_reference_attenuation",
            35.0,
        ),
        (
            (*attenuation_path, HeaderNode("RECeiver"), HeaderNode("TEST")),
            "source_test_attenuation",
            35.0,
        ),
    ):
        parameter = (
            ParameterSpec(ParameterType.BOOLEAN)
            if attribute == "source_attenuation_auto"
            else ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(70))
        )
        add(
            path,
            lambda inv, value, attr=attribute: _set_source_port(
                state,
                inv,
                attr,
                value if isinstance(value, bool) else _number(value),
            ),
            parameters=(parameter,),
        )
        add(
            path,
            lambda inv, attr=attribute, fallback=default: _format_source_value(
                state.source_port_value(inv.indices["channel"], inv.indices["port"], attr, fallback)
            ),
            query=True,
        )

    add(
        (source, power, HeaderNode("CENTer")),
        lambda inv, value: _empty(state.set_power_center(inv.indices["channel"], _number(value))),
        parameters=(power_number,),
    )
    add(
        (source, power, HeaderNode("CENTer")),
        lambda inv: str(
            (
                state.channel(inv.indices["channel"]).power_start
                + state.channel(inv.indices["channel"]).power_stop
            )
            / 2
        ),
        query=True,
    )
    add(
        (source, power, HeaderNode("SPAN")),
        lambda inv, value: _empty(state.set_power_span(inv.indices["channel"], _number(value))),
        parameters=(ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(170)),),
    )
    add(
        (source, power, HeaderNode("SPAN")),
        lambda inv: str(
            state.channel(inv.indices["channel"]).power_stop
            - state.channel(inv.indices["channel"]).power_start
        ),
        query=True,
    )
    boolean_pair((source, power, HeaderNode("COUPle")), "power_coupled")
    add(
        (source, power, HeaderNode("MODE")),
        lambda inv, value: _set_source_port(state, inv, "source_mode", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("AUTO", "ON", "OFF", "NOCTL")),),
    )
    add(
        (source, power, HeaderNode("MODE")),
        lambda inv: state.source_port_value(
            inv.indices["channel"], inv.indices["port"], "source_mode", "AUTO"
        ),
        query=True,
    )
    for header, attribute in (("STARt", "power_start"), ("STOP", "power_stop")):
        add(
            (source, power, HeaderNode(header)),
            lambda inv, value, attr=attribute: _set_number(state, inv, attr, value),
            parameters=(power_number,),
        )
        add(
            (source, power, HeaderNode(header)),
            lambda inv, attr=attribute: str(getattr(state.channel(inv.indices["channel"]), attr)),
            query=True,
        )
        port_attribute = f"port_{attribute}"
        add(
            (source, power, HeaderNode("PORT"), HeaderNode(header)),
            lambda inv, value, attr=port_attribute: _set_source_port(
                state, inv, attr, _number(value)
            ),
            parameters=(power_number,),
        )
        add(
            (source, power, HeaderNode("PORT"), HeaderNode(header)),
            lambda inv, attr=port_attribute, fallback=(-10.0 if header == "STARt" else 0.0): str(
                state.source_port_value(inv.indices["channel"], inv.indices["port"], attr, fallback)
            ),
            query=True,
        )

    for path in (
        (source, power, HeaderNode("SLOPe")),
        (source, power, HeaderNode("LEVel"), HeaderNode("SLOPe")),
    ):
        add(
            path,
            lambda inv, value: _set_number(state, inv, "power_slope", value),
            parameters=(
                ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-2), maximum=Decimal(2)),
            ),
        )
        add(
            path,
            lambda inv: str(state.channel(inv.indices["channel"]).power_slope),
            query=True,
        )
        boolean_pair((*path, HeaderNode("STATe")), "power_slope_enabled")


def _set_number(state: VNASweepSystem, invocation, attribute: str, value: NumericValue) -> str:
    state.configure(invocation.indices["channel"], attribute, _number(value))
    return ""


def _set_output(state: VNASweepSystem, attribute: str, value: bool) -> str:
    setattr(state, attribute, value)
    return ""


def _set_channel_value(state: VNASweepSystem, invocation, attribute: str, value) -> str:
    state.configure(invocation.indices["channel"], attribute, value)
    return ""


def _set_source_port(state: VNASweepSystem, invocation, attribute: str, value) -> str:
    state.set_source_port_value(
        invocation.indices["channel"], invocation.indices["port"], attribute, value
    )
    return ""


def _validated_segment_port(state: VNASweepSystem, invocation) -> int:
    port = invocation.indices["segment_port"]
    state._validate_port(port)
    return port


def _set_segment_port_control(state: VNASweepSystem, invocation, value: bool) -> str:
    port = _validated_segment_port(state, invocation)
    state.channel(invocation.indices["channel"]).segment_port_bandwidth_control[port] = value
    return ""


def _set_segment_port_number(
    state: VNASweepSystem, invocation, attribute: str, value: NumericValue
) -> str:
    converted = _number(value)
    if "attenuation" in attribute and not 0 <= converted <= 70:
        raise SCPICommandError(-222, "Data out of range; segment attenuation")
    if attribute == "port_if_bandwidth" and converted <= 0:
        raise SCPICommandError(-222, "Data out of range; segment IF bandwidth")
    if attribute == "port_power":
        state._validate_power(converted)
    state.set_segment_port_value(
        invocation.indices["channel"],
        invocation.indices["segment"],
        invocation.indices.get("port", 1),
        attribute,
        converted,
    )
    return ""


def _source_port_number(state: VNASweepSystem, name: str) -> int:
    normalized = name.strip().strip('"').upper().replace("_", " ")
    for prefix in ("PORT ", "PORT"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    try:
        port = int(normalized)
    except ValueError as exc:
        raise SCPICommandError(-224, "Illegal parameter value; source name") from exc
    state._validate_port(port)
    return port


def _format_source_value(value) -> str:
    if isinstance(value, bool):
        return _bool(value)
    if isinstance(value, float):
        return _format_number(value)
    return str(value)


def _set_segment_number(state, invocation, attribute: str, value: NumericValue) -> str:
    state.configure_segment(
        invocation.indices["channel"], invocation.indices["segment"], attribute, _number(value)
    )
    return ""


def _set_segment_value(state, invocation, attribute: str, value) -> str:
    if isinstance(value, NumericValue):
        value = _number(value)
    state.configure_segment(
        invocation.indices["channel"], invocation.indices["segment"], attribute, value
    )
    return ""


def _segment_query(state, invocation, attribute: str) -> str:
    value = getattr(
        state.segment(invocation.indices["channel"], invocation.indices["segment"]), attribute
    )
    if isinstance(value, bool):
        return _bool(value)
    if isinstance(value, float):
        return _format_number(value)
    return str(value)


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


def _bool(value: bool) -> str:
    return "1" if value else "0"


def _empty(_value=None) -> str:
    return ""
