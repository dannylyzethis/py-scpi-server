"""Composable active-source and multi-source VNA applications."""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from decimal import Decimal

from scpi_emulator.scenario import ScenarioError, ScenarioPlayer

from .measurements import VNAMeasurementSystem
from .output import DataFormat
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
class ActiveSourceChannel:
    hot_parameters_enabled: bool = False
    bias_voltage: float = 0.0
    bias_current: float = 0.0
    true_mode_enabled: bool = False
    true_mode: str = "DIFFerential"
    amplitude_ratio: float = 1.0
    display_interpolate: bool = False
    display_input_powers: dict[int, float] = field(default_factory=dict)
    input_port: int = 1
    output_port: int = 2
    phase_points: int = 8
    power_start: float = -10.0
    power_steps: int = 201
    power_stop: float = 0.0
    sweep_type: str = "LINear"
    tuning_mode: str = "ABSolute"
    tuning_absolute: float = -5.0
    tuning_relative: float = -15.0


@dataclass
class SourcePhaseState:
    enabled: bool = False
    angle_degrees: float = 0.0


class VNAActiveSourceSystem:
    """Apply multi-source controls to ordinary VNA trace data."""

    def __init__(
        self,
        measurements: VNAMeasurementSystem,
        data_format: DataFormat,
        source_count: int,
        port_count: int = 2,
    ) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.source_count = source_count
        self.port_count = port_count
        self.channels: dict[int, ActiveSourceChannel] = {}
        self.sources: dict[int, SourcePhaseState] = {}
        self.player: ScenarioPlayer | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player

    def reset(self) -> None:
        self.channels.clear()
        self.sources.clear()

    def channel(self, number: int) -> ActiveSourceChannel:
        return self.channels.setdefault(number, ActiveSourceChannel())

    def source(self, number: int) -> SourcePhaseState:
        if not 1 <= number <= self.source_count:
            raise SCPICommandError(-222, "Data out of range; source number")
        return self.sources.setdefault(number, SourcePhaseState())

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        state = self.channel(channel)
        if state.hot_parameters_enabled and state.sweep_type == "POWer":
            return _linear(state.power_start, state.power_stop, state.power_steps)
        return stimulus

    def samples(
        self,
        channel: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        state = self.channel(channel)
        points = (
            state.power_steps
            if state.hot_parameters_enabled and state.sweep_type == "POWer"
            else len(samples)
        )
        result = _resample(samples, points)
        if state.hot_parameters_enabled:
            scenario = self._scenario_trace(points, advance=True)
            if scenario is not None:
                result = scenario
        phase = sum(source.angle_degrees for source in self.sources.values() if source.enabled)
        if phase:
            rotation = cmath.exp(1j * math.radians(phase))
            result = tuple(value * rotation for value in result)
        if state.true_mode_enabled:
            polarity = -1.0 if state.true_mode == "DIFFerential" else 1.0
            result = tuple(value * state.amplitude_ratio * polarity for value in result)
        return result

    def hot_parameter_data(self, channel: int):
        state = self.channel(channel)
        if not state.hot_parameters_enabled:
            raise SCPICommandError(-221, "Settings conflict; active hot parameters are disabled")
        measurement = self.measurements.selected(channel)
        values = self._scenario_trace(len(measurement.stimulus), advance=True)
        if values is None:
            values = tuple(measurement.samples)
        return self.data_format.encode_values(
            component for value in values for component in (value.real, value.imag)
        )

    def _scenario_trace(self, points: int, *, advance: bool) -> tuple[complex, ...] | None:
        if self.player is None:
            return None
        names = {name.casefold(): name for name in self.player.stream_names}
        key = "active_hot_parameters.trace"
        if key not in names:
            return None
        stream = names[key]
        try:
            value = self.player.read(stream) if advance else self.player.peek(stream)
        except ScenarioError as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; {error}") from error
        try:
            values = (
                (complex(value),) * points
                if isinstance(value, (int, float, complex))
                else tuple(complex(item) for item in value)
            )
        except (TypeError, ValueError) as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r}") from error
        if len(values) != points:
            raise SCPICommandError(
                -230,
                f"Data corrupt or stale; stream {stream!r} length {len(values)}, expected {points}",
            )
        return values


def register_active_source_commands(
    registry: CommandRegistry, state: VNAActiveSourceSystem
) -> None:
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    source = HeaderNode("SOURce", index="source", index_default=1)
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    number = ParameterSpec(ParameterType.NUMBER)

    def channel_exists(invocation) -> bool:
        channel = state.measurements.channels.get(invocation.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def option_enabled(name: str):
        names = {name, name.replace("_", "-")}
        return lambda invocation: bool(names & invocation.capabilities)

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

    hot = (sense, HeaderNode("AHP"))
    active = (sense, HeaderNode("ACTive"))
    hot_option = option_enabled("active_hot_parameters")
    add(
        (*hot, HeaderNode("STATe")),
        lambda inv, value: _set(
            state.channel(inv.indices["channel"]), "hot_parameters_enabled", value
        ),
        parameters=(boolean,),
        available=hot_option,
        exists=channel_exists,
    )

    def active_pair(path, attribute, parameter, transform=lambda value: value):
        add(
            (*active, *path),
            lambda inv, value, name=attribute, convert=transform: _set(
                state.channel(inv.indices["channel"]), name, convert(value)
            ),
            parameters=(parameter,),
            available=hot_option,
            exists=channel_exists,
        )
        add(
            (*active, *path),
            lambda inv, name=attribute: _render(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=hot_option,
            exists=channel_exists,
        )

    active_pair(
        (HeaderNode("DISPlay"), HeaderNode("INTerpolate")),
        "display_interpolate",
        boolean,
    )
    display_trace = (
        *active,
        HeaderNode("DISPlay"),
        HeaderNode("TRACe", index="trace", index_default=1),
        HeaderNode("IPWer"),
    )
    add(
        display_trace,
        lambda inv, value: _set_display_power(state.channel(inv.indices["channel"]), inv, value),
        parameters=(ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-10), maximum=Decimal(0)),),
        available=hot_option,
        exists=channel_exists,
    )
    add(
        display_trace,
        lambda inv: _number(
            state.channel(inv.indices["channel"]).display_input_powers.get(
                inv.indices["trace"], 0.0
            )
        ),
        query=True,
        available=hot_option,
        exists=channel_exists,
    )
    port = ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=state.port_count)
    add(
        (*active, HeaderNode("PMAP")),
        lambda inv, input_port, output_port: _set_port_map(
            state.channel(inv.indices["channel"]), input_port, output_port
        ),
        parameters=(port, port),
        available=hot_option,
        exists=channel_exists,
    )
    for header, attribute in (("INPut", "input_port"), ("OUTPut", "output_port")):
        add(
            (*active, HeaderNode("PMAP"), HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True,
            available=hot_option,
            exists=channel_exists,
        )

    active_pair(
        (HeaderNode("SWEep"), HeaderNode("PHASe"), HeaderNode("POINt")),
        "phase_points",
        ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=50),
    )
    power_sweep = (HeaderNode("SWEep"), HeaderNode("POWer"))
    for header, attribute in (("STARt", "power_start"), ("STOP", "power_stop")):
        add(
            (*active, *power_sweep, HeaderNode(header)),
            lambda inv, value, name=attribute: _set_power_range(
                state.channel(inv.indices["channel"]), name, value
            ),
            parameters=(
                ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
            ),
            available=hot_option,
            exists=channel_exists,
        )
        add(
            (*active, *power_sweep, HeaderNode(header)),
            lambda inv, name=attribute: _number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=hot_option,
            exists=channel_exists,
        )
    active_pair(
        (*power_sweep, HeaderNode("STEP")),
        "power_steps",
        ParameterSpec(ParameterType.INTEGER, minimum=2, maximum=20001),
    )
    active_pair(
        (HeaderNode("SWEep"), HeaderNode("TYPE")),
        "sweep_type",
        ParameterSpec(
            ParameterType.ENUM,
            choices=("LINear", "LOGarithmic", "POWer", "MULTiple"),
        ),
    )
    active_pair(
        (HeaderNode("TTONe"), HeaderNode("MODE")),
        "tuning_mode",
        ParameterSpec(ParameterType.ENUM, choices=("ABSolute", "RELative")),
    )
    for header, attribute in (("ABSolute", "tuning_absolute"), ("RELative", "tuning_relative")):
        active_pair(
            (HeaderNode("TTONe"), HeaderNode(header)),
            attribute,
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
            _numeric,
        )
    add(
        (*hot, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).hot_parameters_enabled),
        query=True,
        available=hot_option,
        exists=channel_exists,
    )
    for header, attribute in (("VOLTage", "bias_voltage"), ("CURRent", "bias_current")):
        path = (*hot, HeaderNode("BIAS"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_numeric(
                state.channel(inv.indices["channel"]), name, value
            ),
            parameters=(number,),
            available=hot_option,
            exists=channel_exists,
        )
        add(
            path,
            lambda inv, name=attribute: _number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=hot_option,
            exists=channel_exists,
        )
    add(
        (
            HeaderNode("CALCulate", index="channel", index_default=1),
            HeaderNode("AHP"),
            HeaderNode("DATA"),
        ),
        lambda inv: state.hot_parameter_data(inv.indices["channel"]),
        query=True,
        available=hot_option,
        exists=channel_exists,
    )

    phase = (source, HeaderNode("PHASe"))
    phase_option = option_enabled("source_phase_control")
    add(
        (*phase, HeaderNode("STATe")),
        lambda inv, value: _set(state.source(inv.indices["source"]), "enabled", value),
        parameters=(boolean,),
        available=phase_option,
    )
    add(
        (*phase, HeaderNode("STATe")),
        lambda inv: _boolean(state.source(inv.indices["source"]).enabled),
        query=True,
        available=phase_option,
    )
    add(
        (*phase, HeaderNode("ANGLe")),
        lambda inv, value: _set_phase(state.source(inv.indices["source"]), value),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-360), maximum=Decimal(360)),
        ),
        available=phase_option,
    )
    add(
        (*phase, HeaderNode("ANGLe")),
        lambda inv: _number(state.source(inv.indices["source"]).angle_degrees),
        query=True,
        available=phase_option,
    )

    true_mode = (sense, HeaderNode("TMSTimulus"))
    true_mode_option = option_enabled("true_mode_stimulus")
    add(
        (*true_mode, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "true_mode_enabled", value),
        parameters=(boolean,),
        available=true_mode_option,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).true_mode_enabled),
        query=True,
        available=true_mode_option,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("MODE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "true_mode", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("DIFFerential", "COMMon")),),
        available=true_mode_option,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("MODE")),
        lambda inv: state.channel(inv.indices["channel"]).true_mode,
        query=True,
        available=true_mode_option,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("AMPLitude"), HeaderNode("RATio")),
        lambda inv, value: _set_ratio(state.channel(inv.indices["channel"]), value),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal("0.001"), maximum=Decimal(1000)),
        ),
        available=true_mode_option,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("AMPLitude"), HeaderNode("RATio")),
        lambda inv: _number(state.channel(inv.indices["channel"]).amplitude_ratio),
        query=True,
        available=true_mode_option,
        exists=channel_exists,
    )


def _set(target, attribute: str, value) -> str:
    setattr(target, attribute, value)
    return ""


def _set_numeric(target, attribute: str, value: NumericValue) -> str:
    return _set(target, attribute, float(value.value))


def _numeric(value: NumericValue) -> float:
    return float(value.value)


def _render(value) -> str:
    if isinstance(value, bool):
        return _boolean(value)
    if isinstance(value, float):
        return _number(value)
    return str(value)


def _set_display_power(target: ActiveSourceChannel, invocation, value: NumericValue) -> str:
    target.display_input_powers[invocation.indices["trace"]] = float(value.value)
    return ""


def _set_port_map(target: ActiveSourceChannel, input_port: int, output_port: int) -> str:
    if input_port == output_port:
        raise SCPICommandError(-224, "Illegal parameter value; input and output ports must differ")
    target.input_port = input_port
    target.output_port = output_port
    return ""


def _set_power_range(target: ActiveSourceChannel, attribute: str, value: NumericValue) -> str:
    number = float(value.value)
    if attribute == "power_start" and number > target.power_stop:
        raise SCPICommandError(-222, "Data out of range; active power start")
    if attribute == "power_stop" and number < target.power_start:
        raise SCPICommandError(-222, "Data out of range; active power stop")
    return _set(target, attribute, number)


def _set_phase(target: SourcePhaseState, value: NumericValue) -> str:
    return _set(target, "angle_degrees", float(value.value))


def _set_ratio(target: ActiveSourceChannel, value: NumericValue) -> str:
    return _set(target, "amplitude_ratio", float(value.value))


def _boolean(value: bool) -> str:
    return "1" if value else "0"


def _number(value: float) -> str:
    return f"{value:.12g}"


def _linear(start: float, stop: float, points: int) -> tuple[float, ...]:
    step = (stop - start) / (points - 1)
    return tuple(start + index * step for index in range(points))


def _resample(samples: tuple[complex, ...], points: int) -> tuple[complex, ...]:
    if len(samples) == points:
        return samples
    if not samples:
        return (0j,) * points
    return tuple(
        samples[round(index * (len(samples) - 1) / (points - 1))] for index in range(points)
    )
