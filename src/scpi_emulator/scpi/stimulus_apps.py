"""Fast-CW and arbitrary-waveform stimulus applications."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scpi_emulator.scenario import ScenarioError, ScenarioPlayer

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
class StimulusApplicationChannel:
    fast_cw_enabled: bool = False
    cw_frequency: float = 1e9
    dwell_seconds: float = 0.001
    waveform_enabled: bool = False
    waveform_scale: float = 1.0
    waveform_offset: float = 0.0


class VNAStimulusApplicationSystem:
    """Apply repeatable CW axes and scenario waveform envelopes to VNA traces."""

    def __init__(self, measurements: VNAMeasurementSystem, minimum: float, maximum: float) -> None:
        self.measurements = measurements
        self.minimum = minimum
        self.maximum = maximum
        self.channels: dict[int, StimulusApplicationChannel] = {}
        self.player: ScenarioPlayer | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player

    def reset(self) -> None:
        self.channels.clear()

    def channel(self, number: int) -> StimulusApplicationChannel:
        return self.channels.setdefault(number, StimulusApplicationChannel())

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        state = self.channel(channel)
        if not state.fast_cw_enabled:
            return stimulus
        return (state.cw_frequency,) * len(stimulus)

    def samples(
        self,
        channel: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        state = self.channel(channel)
        if not state.waveform_enabled:
            return samples
        envelope = self._waveform(len(samples)) or ((1 + 0j,) * len(samples))
        return tuple(
            sample * (state.waveform_offset + state.waveform_scale * value)
            for sample, value in zip(samples, envelope)
        )

    def _waveform(self, points: int) -> tuple[complex, ...] | None:
        if self.player is None:
            return None
        names = {name.casefold(): name for name in self.player.stream_names}
        key = "arbitrary_waveform.envelope"
        if key not in names:
            return None
        stream = names[key]
        try:
            raw = self.player.read(stream)
            values = (
                (complex(raw),) * points
                if isinstance(raw, (int, float, complex))
                else tuple(complex(value) for value in raw)
            )
        except (ScenarioError, TypeError, ValueError) as error:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r}") from error
        if len(values) != points:
            raise SCPICommandError(
                -230,
                f"Data corrupt or stale; stream {stream!r} length {len(values)}, expected {points}",
            )
        return values


def register_stimulus_application_commands(
    registry: CommandRegistry, state: VNAStimulusApplicationSystem
) -> None:
    sense = HeaderNode("SENSe", index="channel", index_default=1)

    def exists(invocation) -> bool:
        channel = state.measurements.channels.get(invocation.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def licensed(name: str):
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
    fast_cw = (sense, HeaderNode("FCW"))
    fast_license = licensed("fast_cw")
    add(
        (*fast_cw, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "fast_cw_enabled", value),
        parameters=(boolean,),
        available=fast_license,
    )
    add(
        (*fast_cw, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).fast_cw_enabled),
        query=True,
        available=fast_license,
    )
    for header, attribute, minimum, maximum in (
        ("FREQuency", "cw_frequency", state.minimum, state.maximum),
        ("DWELl", "dwell_seconds", 0.0, 3600.0),
    ):
        path = (*fast_cw, HeaderNode(header))
        parameter = (
            ParameterSpec(
                ParameterType.NUMBER,
                minimum=Decimal(0),
                units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
            )
            if attribute == "cw_frequency"
            else ParameterSpec(
                ParameterType.NUMBER,
                minimum=Decimal(str(minimum)),
                maximum=Decimal(str(maximum)),
            )
        )
        add(
            path,
            lambda inv, value, name=attribute: _set_channel_number(state, inv, name, value),
            parameters=(parameter,),
            available=fast_license,
        )
        add(
            path,
            lambda inv, name=attribute: _number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=fast_license,
        )

    waveform = (sense, HeaderNode("AWGeneration"))
    waveform_license = licensed("arbitrary_waveform_generation")
    add(
        (*waveform, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "waveform_enabled", value),
        parameters=(boolean,),
        available=waveform_license,
    )
    add(
        (*waveform, HeaderNode("STATe")),
        lambda inv: _boolean(state.channel(inv.indices["channel"]).waveform_enabled),
        query=True,
        available=waveform_license,
    )
    for header, attribute in (("SCALe", "waveform_scale"), ("OFFSet", "waveform_offset")):
        path = (*waveform, HeaderNode(header))
        add(
            path,
            lambda inv, value, name=attribute: _set_number(
                state.channel(inv.indices["channel"]), name, value
            ),
            parameters=(ParameterSpec(ParameterType.NUMBER),),
            available=waveform_license,
        )
        add(
            path,
            lambda inv, name=attribute: _number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=waveform_license,
        )


def _set(target, attribute: str, value) -> str:
    setattr(target, attribute, value)
    return ""


def _set_number(target, attribute: str, value: NumericValue) -> str:
    scale = {None: 1.0, "HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[value.unit]
    return _set(target, attribute, float(value.value) * scale)


def _set_channel_number(state, invocation, attribute: str, value: NumericValue) -> str:
    scale = {None: 1.0, "HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[value.unit]
    number = float(value.value) * scale
    if attribute == "cw_frequency" and not state.minimum <= number <= state.maximum:
        raise SCPICommandError(-222, "Data out of range; fast-CW frequency")
    return _set(state.channel(invocation.indices["channel"]), attribute, number)


def _boolean(value: bool) -> str:
    return "1" if value else "0"


def _number(value: float) -> str:
    return f"{value:.12g}"
