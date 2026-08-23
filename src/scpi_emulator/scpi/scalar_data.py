"""Scenario-backed scalar/DMM measurement adapter."""

from __future__ import annotations

from dataclasses import dataclass

from scpi_emulator.scenario import AdvancePolicy, ScenarioError, ScenarioPlayer, StreamKind

from .operations import OperationManager
from .registry import CommandRegistry, CommandSpec, HeaderNode, ParameterSpec, ParameterType, SCPICommandError


FUNCTION_STREAMS = {
    "VOLTage:DC": "voltage.dc",
    "VOLTage:AC": "voltage.ac",
    "CURRent:DC": "current.dc",
    "CURRent:AC": "current.ac",
    "RESistance": "resistance",
    "FRESistance": "fresistance",
    "CAPacitance": "capacitance",
    "FREQuency": "frequency",
    "PERiod": "period",
}


@dataclass
class ScalarConfiguration:
    function: str = "VOLTage:DC"
    range: float | None = None
    resolution: float | None = None


class ScalarScenarioSystem:
    """Translate generic scalar streams into DMM acquisition commands."""

    def __init__(self, operations: OperationManager) -> None:
        self.operations = operations
        self.configuration = ScalarConfiguration()
        self.player: ScenarioPlayer | None = None
        self.bindings: dict[str, str] = {}
        self.last_value: float | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player
        self.last_value = None

    def bind(self, function: str, stream: str) -> None:
        self.bindings[_function(function)] = stream

    def reset(self) -> None:
        self.configuration = ScalarConfiguration()
        self.last_value = None
        if self.player is not None:
            self.player.reset()

    def inspect(self) -> dict[str, object]:
        """Return scalar configuration without consuming a scenario value."""
        return {
            "function": self.configuration.function,
            "range": self.configuration.range,
            "resolution": self.configuration.resolution,
            "last_value": self.last_value,
            "bindings": dict(self.bindings),
        }

    def configure(self, function: str, measurement_range=None, resolution=None) -> None:
        self.configuration = ScalarConfiguration(
            _function(function),
            _optional_number(measurement_range),
            _optional_number(resolution),
        )

    def read(self, function: str | None = None) -> str:
        if function is not None:
            self.configure(function)
        operation = self.operations.begin("scalar measurement")
        try:
            value = self._next_value()
            self._validate_range(value)
            self.last_value = value
            operation.complete()
            return _format(value)
        except Exception:
            operation.cancel()
            raise

    def measure(self, function: str, measurement_range=None, resolution=None) -> str:
        self.configure(function, measurement_range, resolution)
        return self.read()

    def fetch(self) -> str:
        if self.last_value is None:
            raise SCPICommandError(-230, "Data corrupt or stale; no completed measurement")
        return _format(self.last_value)

    def _next_value(self) -> float:
        if self.player is None:
            return 0.0
        stream = self._stream_name()
        definition = self.player.definition.stream(stream)
        if definition.kind is not StreamKind.SCALAR:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r} is not scalar")
        try:
            value = float(self.player.read(stream))
            if definition.advance is AdvancePolicy.TRIGGER:
                self.player.notify_trigger(stream)
            elif definition.advance is AdvancePolicy.OPERATION:
                self.player.notify_operation_complete(stream)
        except ScenarioError as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; {error}") from error
        return value

    def _stream_name(self) -> str:
        assert self.player is not None
        candidates = (
            self.bindings.get(self.configuration.function),
            FUNCTION_STREAMS[self.configuration.function],
            "reading",
        )
        names = {name.casefold(): name for name in self.player.stream_names}
        for candidate in candidates:
            if candidate and candidate.casefold() in names:
                return names[candidate.casefold()]
        raise SCPICommandError(
            -230, f"Data corrupt or stale; no stream for {self.configuration.function}"
        )

    def _validate_range(self, value: float) -> None:
        selected = self.configuration.range
        if selected is not None and abs(value) > selected:
            raise SCPICommandError(-222, "Data out of range; scalar reading exceeds range")


def register_scalar_commands(registry: CommandRegistry, state: ScalarScenarioSystem) -> None:
    registry.register(CommandSpec((HeaderNode("READ"),), lambda inv: state.read(), query=True))
    registry.register(CommandSpec((HeaderNode("FETCh"),), lambda inv: state.fetch(), query=True))
    for path, function in (
        ((HeaderNode("MEASure"), HeaderNode("VOLTage"), HeaderNode("DC")), "VOLTage:DC"),
        ((HeaderNode("MEASure"), HeaderNode("VOLTage"), HeaderNode("AC")), "VOLTage:AC"),
        ((HeaderNode("MEASure"), HeaderNode("CURRent"), HeaderNode("DC")), "CURRent:DC"),
        ((HeaderNode("MEASure"), HeaderNode("CURRent"), HeaderNode("AC")), "CURRent:AC"),
        ((HeaderNode("MEASure"), HeaderNode("RESistance")), "RESistance"),
        ((HeaderNode("MEASure"), HeaderNode("FRESistance")), "FRESistance"),
        ((HeaderNode("MEASure"), HeaderNode("CAPacitance")), "CAPacitance"),
        ((HeaderNode("MEASure"), HeaderNode("FREQuency")), "FREQuency"),
        ((HeaderNode("MEASure"), HeaderNode("PERiod")), "PERiod"),
    ):
        registry.register(CommandSpec(
            path,
            lambda inv, measurement_range, resolution, selected=function: state.measure(
                selected, measurement_range, resolution
            ),
            (
                ParameterSpec(ParameterType.NUMBER, required=False, default=None),
                ParameterSpec(ParameterType.NUMBER, required=False, default=None),
            ),
            query=True,
        ))
    for path, function in (
        ((HeaderNode("CONFigure"), HeaderNode("VOLTage"), HeaderNode("DC")), "VOLTage:DC"),
        ((HeaderNode("CONFigure"), HeaderNode("CURRent"), HeaderNode("DC")), "CURRent:DC"),
        ((HeaderNode("CONFigure"), HeaderNode("RESistance")), "RESistance"),
    ):
        registry.register(CommandSpec(
            path,
            lambda inv, measurement_range, resolution, selected=function: _configure(
                state, selected, measurement_range, resolution
            ),
            (
                ParameterSpec(ParameterType.NUMBER, required=False, default=None),
                ParameterSpec(ParameterType.NUMBER, required=False, default=None),
            ),
        ))


def _function(value: str) -> str:
    names = {name.upper(): name for name in FUNCTION_STREAMS}
    try:
        return names[value.upper()]
    except KeyError as error:
        raise ValueError(f"unsupported scalar function {value!r}") from error


def _optional_number(value) -> float | None:
    return None if value is None else float(value.value)


def _configure(state, function, measurement_range, resolution) -> str:
    state.configure(function, measurement_range, resolution)
    return ""


def _format(value: float) -> str:
    return f"{value:+.12E}"
