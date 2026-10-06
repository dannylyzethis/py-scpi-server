"""Generic port-count-aware VNA measurement application."""

from __future__ import annotations

from dataclasses import dataclass

from scpi_emulator.scenario import ScenarioError, ScenarioPlayer

from .measurements import VNAMeasurementSystem
from .output import DataFormat
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)


@dataclass
class NPortChannel:
    enabled: bool = False


class VNANPortSystem:
    """Create and read S-parameter measurements for the configured port count."""

    def __init__(
        self,
        measurements: VNAMeasurementSystem,
        data_format: DataFormat,
        maximum_ports: int,
    ) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.maximum_ports = maximum_ports
        self.channels: dict[int, NPortChannel] = {}
        self.player: ScenarioPlayer | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player

    def reset(self) -> None:
        self.channels.clear()

    def channel(self, number: int) -> NPortChannel:
        return self.channels.setdefault(number, NPortChannel())

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        return stimulus

    def samples(self, channel: int, samples: tuple[complex, ...], stimulus) -> tuple[complex, ...]:
        return samples

    def define(self, channel: int, name: str, receiver: int, source: int) -> str:
        self._require_enabled(channel)
        parameter = self._parameter(receiver, source)
        self.measurements.define(channel, name, parameter)
        self.measurements.select(channel, name)
        return ""

    def data(self, channel: int, receiver: int, source: int):
        self._require_enabled(channel)
        parameter = self._parameter(receiver, source)
        measurement = self.measurements.selected(channel)
        points = len(measurement.stimulus)
        values = self._scenario(parameter, points)
        if values is None:
            values = (
                tuple(measurement.samples) if measurement.parameter == parameter else (0j,) * points
            )
        return self.data_format.encode_values(
            component for value in values for component in (value.real, value.imag)
        )

    def catalog(self, channel: int) -> str:
        self._require_enabled(channel)
        return ",".join(
            self._parameter(receiver, source)
            for receiver in range(1, self.maximum_ports + 1)
            for source in range(1, self.maximum_ports + 1)
        )

    def parameter(self, channel: int, receiver: int, source: int) -> str:
        self._require_enabled(channel)
        return self._parameter(receiver, source)

    def delete(self, channel: int, name: str) -> str:
        self._require_enabled(channel)
        self.measurements.delete(channel, name)
        return ""

    def _parameter(self, receiver: int, source: int) -> str:
        if not 1 <= receiver <= self.maximum_ports or not 1 <= source <= self.maximum_ports:
            raise SCPICommandError(-222, "Data out of range; N-port address")
        return f"S{receiver}{source}"

    def _require_enabled(self, channel: int) -> None:
        if not self.channel(channel).enabled:
            raise SCPICommandError(-221, "Settings conflict; N-port application is disabled")

    def _scenario(self, stream: str, points: int) -> tuple[complex, ...] | None:
        if self.player is None:
            return None
        names = {name.casefold(): name for name in self.player.stream_names}
        if stream.casefold() not in names:
            return None
        resolved = names[stream.casefold()]
        try:
            raw = self.player.read(resolved)
            values = (
                (complex(raw),) * points
                if isinstance(raw, (int, float, complex))
                else tuple(complex(value) for value in raw)
            )
        except (ScenarioError, TypeError, ValueError) as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {resolved!r}") from error
        if len(values) != points:
            raise SCPICommandError(
                -230,
                f"Data corrupt or stale; stream {resolved!r} length {len(values)}, expected {points}",
            )
        return values


def register_nport_commands(registry: CommandRegistry, state: VNANPortSystem) -> None:
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    calculate = HeaderNode("CALCulate", index="channel", index_default=1)

    def available(invocation) -> bool:
        return bool({"n_port", "n-port"} & invocation.capabilities)

    def exists(invocation) -> bool:
        channel = state.measurements.channels.get(invocation.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    boolean = ParameterSpec(ParameterType.BOOLEAN)
    integer = ParameterSpec(ParameterType.INTEGER, minimum=1)
    string = ParameterSpec(ParameterType.STRING)
    registry.register(
        CommandSpec(
            (sense, HeaderNode("NPORT"), HeaderNode("STATe")),
            lambda inv, value: _set(state.channel(inv.indices["channel"]), "enabled", value),
            (boolean,),
            available=available,
            exists=exists,
        )
    )
    registry.register(
        CommandSpec(
            (calculate, HeaderNode("NPORT"), HeaderNode("CATalog")),
            lambda inv: state.catalog(inv.indices["channel"]),
            query=True,
            available=available,
            exists=exists,
        )
    )
    registry.register(
        CommandSpec(
            (calculate, HeaderNode("NPORT"), HeaderNode("PARameter")),
            lambda inv, receiver, source: state.parameter(inv.indices["channel"], receiver, source),
            (integer, integer),
            query=True,
            available=available,
            exists=exists,
        )
    )
    registry.register(
        CommandSpec(
            (calculate, HeaderNode("NPORT"), HeaderNode("DELete")),
            lambda inv, name: state.delete(inv.indices["channel"], name),
            (string,),
            available=available,
            exists=exists,
        )
    )
    registry.register(
        CommandSpec(
            (sense, HeaderNode("NPORT"), HeaderNode("STATe")),
            lambda inv: "1" if state.channel(inv.indices["channel"]).enabled else "0",
            query=True,
            available=available,
            exists=exists,
        )
    )
    registry.register(
        CommandSpec(
            (calculate, HeaderNode("NPORT"), HeaderNode("DEFine")),
            lambda inv, name, receiver, source: state.define(
                inv.indices["channel"], name, receiver, source
            ),
            (string, integer, integer),
            available=available,
            exists=exists,
        )
    )
    registry.register(
        CommandSpec(
            (calculate, HeaderNode("NPORT"), HeaderNode("DATA")),
            lambda inv, receiver, source: state.data(inv.indices["channel"], receiver, source),
            (integer, integer),
            query=True,
            available=available,
            exists=exists,
        )
    )


def _set(target, attribute: str, value) -> str:
    setattr(target, attribute, value)
    return ""
