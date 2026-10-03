"""Measurement-uncertainty and performance-test VNA applications."""

from __future__ import annotations

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
class AnalysisApplicationChannel:
    uncertainty_enabled: bool = False
    confidence_percent: float = 95.0
    uncertainty_floor: float = 0.01
    performance_enabled: bool = False
    lower_limit: float = -200.0
    upper_limit: float = 200.0


class VNAAnalysisApplicationSystem:
    """Produce deterministic analysis results from selected measurements or scenarios."""

    def __init__(self, measurements: VNAMeasurementSystem, data_format: DataFormat) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.channels: dict[int, AnalysisApplicationChannel] = {}
        self.player: ScenarioPlayer | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player

    def reset(self) -> None:
        self.channels.clear()

    def channel(self, number: int) -> AnalysisApplicationChannel:
        return self.channels.setdefault(number, AnalysisApplicationChannel())

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        return stimulus

    def samples(self, channel: int, samples: tuple[complex, ...], stimulus) -> tuple[complex, ...]:
        return samples

    def uncertainty_data(self, channel: int):
        state = self.channel(channel)
        if not state.uncertainty_enabled:
            raise SCPICommandError(-221, "Settings conflict; uncertainty analysis is disabled")
        measurement = self.measurements.selected(channel)
        values = self._read("measurement_uncertainty.trace", len(measurement.samples))
        if values is None:
            factor = state.confidence_percent / 95.0
            values = tuple(
                state.uncertainty_floor + factor * 0.01 * abs(sample)
                for sample in measurement.samples
            )
        return self.data_format.encode_values(values)

    def performance_data(self, channel: int):
        state = self.channel(channel)
        if not state.performance_enabled:
            raise SCPICommandError(-221, "Settings conflict; performance test is disabled")
        measurement = self.measurements.selected(channel)
        values = self._read("performance_test.trace", len(measurement.samples))
        if values is None:
            values = tuple(
                20.0 * math.log10(abs(sample)) if sample else -200.0
                for sample in measurement.samples
            )
        return self.data_format.encode_values(values)

    def performance_pass(self, channel: int) -> str:
        state = self.channel(channel)
        values = _decode_ascii(self.performance_data(channel))
        return (
            "1" if all(state.lower_limit <= value <= state.upper_limit for value in values) else "0"
        )

    def _read(self, requested: str, points: int) -> tuple[float, ...] | None:
        if self.player is None:
            return None
        names = {name.casefold(): name for name in self.player.stream_names}
        if requested.casefold() not in names:
            return None
        stream = names[requested.casefold()]
        try:
            raw = self.player.read(stream)
            values = (
                (float(raw.real if isinstance(raw, complex) else raw),) * points
                if isinstance(raw, (int, float, complex))
                else tuple(
                    float(value.real if isinstance(value, complex) else value) for value in raw
                )
            )
        except (ScenarioError, TypeError, ValueError) as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r}") from error
        if len(values) != points:
            raise SCPICommandError(
                -230,
                f"Data corrupt or stale; stream {stream!r} length {len(values)}, expected {points}",
            )
        return values


def register_analysis_application_commands(
    registry: CommandRegistry, state: VNAAnalysisApplicationSystem
) -> None:
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    calculate = HeaderNode("CALCulate", index="channel", index_default=1)

    def exists(invocation) -> bool:
        channel = state.measurements.channels.get(invocation.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def option_enabled(name: str):
        names = {name, name.replace("_", "-")}
        return lambda invocation: bool(names & invocation.capabilities)

    def add(path, handler, *, query=False, parameters=(), available=None):
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

    boolean = ParameterSpec(ParameterType.BOOLEAN)
    uncertainty = (sense, HeaderNode("UNCertainty"))
    uncertainty_option = option_enabled("measurement_uncertainty")
    for header, attribute, parameter in (
        (
            "CONFidence",
            "confidence_percent",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(1), maximum=Decimal(100)),
        ),
        ("FLOOr", "uncertainty_floor", ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0))),
    ):
        path = (*uncertainty, HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_number(
                state.channel(inv.indices["channel"]), name, value
            ),
            parameters=(parameter,),
            available=uncertainty_option,
        )
        add(
            path,
            lambda inv, name=attribute: _number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=uncertainty_option,
        )
    add(
        (*uncertainty, HeaderNode("STATe")),
        lambda inv, value: _set(
            state.channel(inv.indices["channel"]), "uncertainty_enabled", value
        ),
        parameters=(boolean,),
        available=uncertainty_option,
    )
    add(
        (*uncertainty, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).uncertainty_enabled),
        query=True,
        available=uncertainty_option,
    )
    add(
        (calculate, HeaderNode("UNCertainty"), HeaderNode("DATA")),
        lambda inv: state.uncertainty_data(inv.indices["channel"]),
        query=True,
        available=uncertainty_option,
    )

    performance = (sense, HeaderNode("PERFormance"))
    performance_option = option_enabled("performance_test")
    add(
        (*performance, HeaderNode("STATe")),
        lambda inv, value: _set(
            state.channel(inv.indices["channel"]), "performance_enabled", value
        ),
        parameters=(boolean,),
        available=performance_option,
    )
    add(
        (*performance, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).performance_enabled),
        query=True,
        available=performance_option,
    )
    for header, attribute in (("LOWer", "lower_limit"), ("UPPer", "upper_limit")):
        path = (*performance, HeaderNode("LIMit"), HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_limit(state, inv, name, value),
            parameters=(ParameterSpec(ParameterType.NUMBER),),
            available=performance_option,
        )
        add(
            path,
            lambda inv, name=attribute: _number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=performance_option,
        )
    calc_performance = (calculate, HeaderNode("PERFormance"))
    add(
        (*calc_performance, HeaderNode("DATA")),
        lambda inv: state.performance_data(inv.indices["channel"]),
        query=True,
        available=performance_option,
    )
    add(
        (*calc_performance, HeaderNode("PASS")),
        lambda inv: state.performance_pass(inv.indices["channel"]),
        query=True,
        available=performance_option,
    )


def _set(target, attribute: str, value) -> str:
    setattr(target, attribute, value)
    return ""


def _set_number(target, attribute: str, value: NumericValue) -> str:
    return _set(target, attribute, float(value.value))


def _set_limit(state, invocation, attribute: str, value: NumericValue) -> str:
    target = state.channel(invocation.indices["channel"])
    number = float(value.value)
    if attribute == "lower_limit" and number > target.upper_limit:
        raise SCPICommandError(-222, "Data out of range; lower performance limit")
    if attribute == "upper_limit" and number < target.lower_limit:
        raise SCPICommandError(-222, "Data out of range; upper performance limit")
    return _set(target, attribute, number)


def _decode_ascii(value) -> tuple[float, ...]:
    if not isinstance(value, str):
        raise SCPICommandError(-221, "Settings conflict; ASCII format required for pass result")
    return tuple(float(item) for item in value.split(","))


def _boolean(value: bool) -> str:
    return "1" if value else "0"


def _number(value: float) -> str:
    return f"{value:.12g}"
