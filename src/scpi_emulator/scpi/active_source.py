"""Composable active-source and multi-source VNA applications."""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass
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
    ) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.source_count = source_count
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
        return stimulus

    def samples(
        self,
        channel: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        state = self.channel(channel)
        result = samples
        if state.hot_parameters_enabled:
            scenario = self._scenario_trace(len(samples), advance=True)
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

    def licensed(name: str):
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
    hot_license = licensed("active_hot_parameters")
    add(
        (*hot, HeaderNode("STATe")),
        lambda inv, value: _set(
            state.channel(inv.indices["channel"]), "hot_parameters_enabled", value
        ),
        parameters=(boolean,),
        available=hot_license,
        exists=channel_exists,
    )
    add(
        (*hot, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).hot_parameters_enabled),
        query=True,
        available=hot_license,
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
            available=hot_license,
            exists=channel_exists,
        )
        add(
            path,
            lambda inv, name=attribute: _number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=hot_license,
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
        available=hot_license,
        exists=channel_exists,
    )

    phase = (source, HeaderNode("PHASe"))
    phase_license = licensed("source_phase_control")
    add(
        (*phase, HeaderNode("STATe")),
        lambda inv, value: _set(state.source(inv.indices["source"]), "enabled", value),
        parameters=(boolean,),
        available=phase_license,
    )
    add(
        (*phase, HeaderNode("STATe")),
        lambda inv: _boolean(state.source(inv.indices["source"]).enabled),
        query=True,
        available=phase_license,
    )
    add(
        (*phase, HeaderNode("ANGLe")),
        lambda inv, value: _set_phase(state.source(inv.indices["source"]), value),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-360), maximum=Decimal(360)),
        ),
        available=phase_license,
    )
    add(
        (*phase, HeaderNode("ANGLe")),
        lambda inv: _number(state.source(inv.indices["source"]).angle_degrees),
        query=True,
        available=phase_license,
    )

    true_mode = (sense, HeaderNode("TMSTimulus"))
    true_mode_license = licensed("true_mode_stimulus")
    add(
        (*true_mode, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "true_mode_enabled", value),
        parameters=(boolean,),
        available=true_mode_license,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).true_mode_enabled),
        query=True,
        available=true_mode_license,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("MODE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "true_mode", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("DIFFerential", "COMMon")),),
        available=true_mode_license,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("MODE")),
        lambda inv: state.channel(inv.indices["channel"]).true_mode,
        query=True,
        available=true_mode_license,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("AMPLitude"), HeaderNode("RATio")),
        lambda inv, value: _set_ratio(state.channel(inv.indices["channel"]), value),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal("0.001"), maximum=Decimal(1000)),
        ),
        available=true_mode_license,
        exists=channel_exists,
    )
    add(
        (*true_mode, HeaderNode("AMPLitude"), HeaderNode("RATio")),
        lambda inv: _number(state.channel(inv.indices["channel"]).amplitude_ratio),
        query=True,
        available=true_mode_license,
        exists=channel_exists,
    )


def _set(target, attribute: str, value) -> str:
    setattr(target, attribute, value)
    return ""


def _set_numeric(target, attribute: str, value: NumericValue) -> str:
    return _set(target, attribute, float(value.value))


def _set_phase(target: SourcePhaseState, value: NumericValue) -> str:
    return _set(target, "angle_degrees", float(value.value))


def _set_ratio(target: ActiveSourceChannel, value: NumericValue) -> str:
    return _set(target, "amplitude_ratio", float(value.value))


def _boolean(value: bool) -> str:
    return "1" if value else "0"


def _number(value: float) -> str:
    return f"{value:.12g}"
