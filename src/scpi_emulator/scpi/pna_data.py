"""Scenario-backed PNA measurement and receiver data access."""

from __future__ import annotations

import math

from scpi_emulator.scenario import ScenarioError, ScenarioPlayer

from .measurements import MeasurementState, PNAMeasurementSystem
from .output import DataFormat
from .registry import CommandRegistry, CommandSpec, HeaderNode, ParameterSpec, ParameterType, SCPICommandError


class PNADataSystem:
    """Adapt generic scenario trace streams to PNA data access points."""

    def __init__(
        self, measurements: PNAMeasurementSystem, data_format: DataFormat, maximum_ports: int
    ) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.maximum_ports = maximum_ports
        self.player: ScenarioPlayer | None = None
        self.bindings: dict[str, str] = {}
        self._event_error: ScenarioError | None = None
        self.applications = []

    def add_application(self, application) -> None:
        self.applications.append(application)

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player
        self._event_error = None

    def bind(self, measurement: str, stream: str) -> None:
        if self.measurements.find(measurement) is None:
            raise ValueError(f"measurement {measurement!r} does not exist")
        self.bindings[measurement] = stream

    def reset(self) -> None:
        self._event_error = None
        if self.player is not None:
            self.player.reset()

    def notify_trigger(self, _channel: int) -> None:
        if self.player is not None:
            try:
                self.player.notify_trigger()
            except ScenarioError as error:
                self._event_error = error

    def notify_complete(self, _channel: int) -> None:
        if self.player is not None:
            try:
                self.player.notify_operation_complete()
            except ScenarioError as error:
                self._event_error = error

    def values(self, channel: int, access: str):
        self._check_event_error()
        measurement = self.measurements.selected(channel)
        samples = self._application_samples(channel, self._samples(measurement), measurement.stimulus)
        if access in ("SDATa", "RDATa"):
            values = tuple(component for value in samples for component in (value.real, value.imag))
        else:
            values = _formatted(samples, measurement.format)
        return self.data_format.encode_values(values)

    def x_values(self, channel: int):
        stimulus = self.measurements.selected(channel).stimulus
        return self.data_format.encode_values(self._application_axis(channel, stimulus))

    def receiver_values(self, channel: int, receiver: str):
        self._check_event_error()
        measurement = self.measurements.selected(channel)
        samples = self._application_samples(
            channel,
            self._read_stream(receiver, len(measurement.stimulus)),
            measurement.stimulus,
        )
        return self.data_format.encode_values(
            component for value in samples for component in (value.real, value.imag)
        )

    def snp_values(self, channel: int, ports_text: str):
        self._check_event_error()
        measurement = self.measurements.selected(channel)
        try:
            ports = tuple(int(value) for value in ports_text.replace(",", " ").split())
        except ValueError as error:
            raise SCPICommandError(-224, "Illegal parameter value; SNP ports") from error
        if (
            not ports
            or len(set(ports)) != len(ports)
            or any(not 1 <= port <= self.maximum_ports for port in ports)
        ):
            raise SCPICommandError(-224, "Illegal parameter value; SNP ports")
        axis = self._application_axis(channel, measurement.stimulus)
        columns: list[float] = list(axis)
        for source in ports:
            for receiver in ports:
                parameter = f"S{receiver}{source}"
                samples = self._application_samples(
                    channel,
                    self._optional_stream(parameter, len(measurement.stimulus)),
                    measurement.stimulus,
                )
                columns.extend(value.real for value in samples)
                columns.extend(value.imag for value in samples)
        return self.data_format.encode_values(columns)

    def _application_samples(self, channel, samples, stimulus):
        axis = stimulus
        for application in self.applications:
            samples = application.samples(channel, samples, axis)
            axis = application.axis(channel, axis)
        return samples

    def _application_axis(self, channel, stimulus):
        axis = stimulus
        for application in self.applications:
            axis = application.axis(channel, axis)
        return axis

    def _samples(self, measurement: MeasurementState) -> tuple[complex, ...]:
        if self.player is None:
            return tuple(measurement.samples)
        stream = self.bindings.get(measurement.name) or self._resolve_stream(measurement)
        try:
            value = self.player.read(stream)
        except ScenarioError as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; {error}") from error
        samples = _complex_trace(value, stream)
        if len(samples) != len(measurement.stimulus):
            raise SCPICommandError(
                -230,
                f"Data corrupt or stale; stream {stream!r} has {len(samples)} points, "
                f"expected {len(measurement.stimulus)}",
            )
        measurement.samples = samples
        return samples

    def _read_stream(self, stream: str, points: int) -> tuple[complex, ...]:
        if self.player is None:
            return (0j,) * points
        names = {name.casefold(): name for name in self.player.stream_names}
        if stream.casefold() not in names:
            raise SCPICommandError(-230, f"Data corrupt or stale; no scenario stream {stream!r}")
        try:
            value = self.player.read(names[stream.casefold()])
        except ScenarioError as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; {error}") from error
        samples = _complex_trace(value, stream)
        if len(samples) != points:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r} length")
        return samples

    def _optional_stream(self, stream: str, points: int) -> tuple[complex, ...]:
        if self.player is None or stream.casefold() not in {
            name.casefold() for name in self.player.stream_names
        }:
            return (0j,) * points
        return self._read_stream(stream, points)

    def _resolve_stream(self, measurement: MeasurementState) -> str:
        assert self.player is not None
        names = {name.casefold(): name for name in self.player.stream_names}
        for candidate in (measurement.name, measurement.parameter):
            if candidate.casefold() in names:
                return names[candidate.casefold()]
        raise SCPICommandError(
            -230, f"Data corrupt or stale; no scenario stream for {measurement.name!r}"
        )

    def _check_event_error(self) -> None:
        if self._event_error is not None:
            error = self._event_error
            self._event_error = None
            raise SCPICommandError(-230, f"Data corrupt or stale; {error}")


def register_pna_data_commands(registry: CommandRegistry, state: PNADataSystem) -> None:
    calc = HeaderNode("CALCulate", index="channel", index_default=1)
    data = HeaderNode("DATA")
    access = ParameterSpec(ParameterType.ENUM, choices=("SDATa", "FDATa", "RDATa"))
    def exists(inv):
        channel = state.measurements.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements
    registry.register(CommandSpec(
        (calc, data), lambda inv, kind: state.values(inv.indices["channel"], kind),
        (access,), query=True, exists=exists,
    ))
    for kind in ("SDATa", "FDATa", "RDATa"):
        registry.register(CommandSpec(
            (calc, data, HeaderNode(kind)),
            lambda inv, selected=kind: state.values(inv.indices["channel"], selected),
            query=True, exists=exists,
        ))
    registry.register(CommandSpec(
        (calc, HeaderNode("MEASure"), data, HeaderNode("X")),
        lambda inv: state.x_values(inv.indices["channel"]), query=True, exists=exists,
    ))
    registry.register(CommandSpec(
        (calc, HeaderNode("RDATA")),
        lambda inv, receiver: state.receiver_values(inv.indices["channel"], receiver),
        (ParameterSpec(ParameterType.CHARACTER),), query=True, exists=exists,
    ))
    registry.register(CommandSpec(
        (calc, data, HeaderNode("SNP"), HeaderNode("PORTs")),
        lambda inv, ports: state.snp_values(inv.indices["channel"], ports),
        (ParameterSpec(ParameterType.STRING),), query=True, exists=exists,
    ))


def _formatted(samples: tuple[complex, ...], display_format: str) -> tuple[float, ...]:
    if display_format in ("POLar", "SLINear", "SCOMplex", "SMITh", "SADMittance"):
        return tuple(component for value in samples for component in (value.real, value.imag))
    if display_format == "PHASe":
        return tuple(math.degrees(math.atan2(value.imag, value.real)) for value in samples)
    if display_format == "REAL":
        return tuple(value.real for value in samples)
    if display_format == "IMAGinary":
        return tuple(value.imag for value in samples)
    if display_format in ("MLOGarithmic", "SLOGarithmic"):
        return tuple(20 * math.log10(abs(value)) if value else -math.inf for value in samples)
    return tuple(abs(value) for value in samples)


def _complex_trace(value, stream: str) -> tuple[complex, ...]:
    try:
        return tuple(complex(item) for item in value)
    except (TypeError, ValueError) as error:
        raise SCPICommandError(
            -230, f"Data corrupt or stale; stream {stream!r} is not a complex trace"
        ) from error
