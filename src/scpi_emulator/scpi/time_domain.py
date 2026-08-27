"""Deterministic VNA time-domain, gating, and fixture-simulation behavior."""

from __future__ import annotations

import cmath
import hashlib
import math
from dataclasses import dataclass, field

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
class TimeDomainChannel:
    transform_enabled: bool = False
    transform_type: str = "BANDpass"
    window: str = "NORMal"
    gate_enabled: bool = False
    gate_start: float = 0.0
    gate_stop: float = math.inf
    gate_type: str = "BANDpass"
    fixture_enabled: bool = False
    fixture_ports: dict[int, str] = field(default_factory=dict)
    fixture_port_enabled: dict[int, bool] = field(default_factory=dict)
    embedding_ports: dict[int, str] = field(default_factory=dict)
    embedding_port_enabled: dict[int, bool] = field(default_factory=dict)
    topology: str = "NONE"


class VNATimeDomainSystem:
    """Apply repeatable application transforms without replacing scenario data."""

    def __init__(self, measurements: VNAMeasurementSystem, maximum_ports: int) -> None:
        self.measurements = measurements
        self.maximum_ports = maximum_ports
        self.channels: dict[int, TimeDomainChannel] = {}

    def reset(self) -> None:
        self.channels.clear()

    def channel(self, number: int) -> TimeDomainChannel:
        return self.channels.setdefault(number, TimeDomainChannel())

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        state = self.channel(channel)
        return _time_axis(stimulus) if state.transform_enabled else stimulus

    def samples(
        self,
        channel: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        state = self.channel(channel)
        adjusted = _fixture(samples, state)
        if not (state.transform_enabled or state.gate_enabled):
            return adjusted
        time_samples = _idft(_window(adjusted, state.window))
        if state.gate_enabled:
            axis = _time_axis(stimulus)
            time_samples = tuple(
                value if _gate_keeps(state, point) else 0j
                for point, value in zip(axis, time_samples)
            )
        if state.transform_type == "STEP":
            total = 0j
            integrated = []
            for value in time_samples:
                total += value
                integrated.append(total)
            time_samples = tuple(integrated)
        elif state.transform_type == "LOWPass":
            time_samples = tuple(complex(value.real, 0.0) for value in time_samples)
        return time_samples if state.transform_enabled else _dft(time_samples)

    def set_fixture_file(self, channel: int, port: int, filename: str) -> None:
        self._port(port)
        if not filename.strip():
            raise SCPICommandError(-224, "Illegal parameter value; fixture filename")
        self.channel(channel).fixture_ports[port] = filename.strip()

    def fixture_file(self, channel: int, port: int) -> str:
        self._port(port)
        return self.channel(channel).fixture_ports.get(port, "")

    def set_fixture_port(self, channel: int, port: int, enabled: bool) -> None:
        self._port(port)
        self.channel(channel).fixture_port_enabled[port] = enabled

    def fixture_port(self, channel: int, port: int) -> bool:
        self._port(port)
        return self.channel(channel).fixture_port_enabled.get(port, False)

    def set_embedding_file(self, channel: int, port: int, filename: str) -> None:
        self._port(port)
        if not filename.strip():
            raise SCPICommandError(-224, "Illegal parameter value; embedding filename")
        self.channel(channel).embedding_ports[port] = filename.strip()

    def embedding_file(self, channel: int, port: int) -> str:
        self._port(port)
        return self.channel(channel).embedding_ports.get(port, "")

    def set_embedding_port(self, channel: int, port: int, enabled: bool) -> None:
        self._port(port)
        self.channel(channel).embedding_port_enabled[port] = enabled

    def embedding_port(self, channel: int, port: int) -> bool:
        self._port(port)
        return self.channel(channel).embedding_port_enabled.get(port, False)

    def _port(self, port: int) -> None:
        if not 1 <= port <= self.maximum_ports:
            raise SCPICommandError(-222, "Data out of range; fixture port")


def register_time_domain_commands(registry: CommandRegistry, state: VNATimeDomainSystem) -> None:
    """Register profile-gated CALCulate time-domain and fixture command families."""
    calc = HeaderNode("CALCulate", index="channel", index_default=1)
    transform = (calc, HeaderNode("TRANsform"), HeaderNode("TIME"))
    gate = (calc, HeaderNode("FILTer"), HeaderNode("TIME"))
    fixture = (calc, HeaderNode("FSIMulator"))
    port = HeaderNode("PORT", index="port", index_default=1)
    boolean = ParameterSpec(ParameterType.BOOLEAN)

    def exists(inv):
        channel = state.measurements.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def licensed(*names):
        return lambda inv: bool(set(names) & inv.capabilities)

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

    time_license = licensed("time_domain", "time-domain", "enhanced_time_domain")
    fixture_license = licensed("fixture_removal", "fixture-removal")

    add(
        (*transform, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "transform_enabled", value),
        parameters=(boolean,),
        available=time_license,
    )
    add(
        (*transform, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).transform_enabled),
        query=True,
        available=time_license,
    )
    add(
        (*transform, HeaderNode("TYPE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "transform_type", value),
        parameters=(
            ParameterSpec(ParameterType.ENUM, choices=("BANDpass", "LOWPass", "IMPulse", "STEP")),
        ),
        available=time_license,
    )
    add(
        (*transform, HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).transform_type,
        query=True,
        available=time_license,
    )
    add(
        (*transform, HeaderNode("WINDow")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "window", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("MINimum", "NORMal", "MAXimum")),),
        available=time_license,
    )
    add(
        (*transform, HeaderNode("WINDow")),
        lambda inv: state.channel(inv.indices["channel"]).window,
        query=True,
        available=time_license,
    )

    add(
        (*gate, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "gate_enabled", value),
        parameters=(boolean,),
        available=time_license,
    )
    add(
        (*gate, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).gate_enabled),
        query=True,
        available=time_license,
    )
    for header, attribute in (("STARt", "gate_start"), ("STOP", "gate_stop")):
        add(
            (*gate, HeaderNode(header)),
            lambda inv, value, name=attribute: _set_gate(state, inv, name, value),
            parameters=(ParameterSpec(ParameterType.NUMBER, units=frozenset({"S"})),),
            available=time_license,
        )
        add(
            (*gate, HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True,
            available=time_license,
        )
    add(
        (*gate, HeaderNode("TYPE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "gate_type", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("BANDpass", "NOTCh")),),
        available=time_license,
    )
    add(
        (*gate, HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).gate_type,
        query=True,
        available=time_license,
    )

    add(
        (*fixture, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "fixture_enabled", value),
        parameters=(boolean,),
        available=fixture_license,
    )
    add(
        (*fixture, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).fixture_enabled),
        query=True,
        available=fixture_license,
    )
    fixture_port = (*fixture, HeaderNode("SEND"), HeaderNode("DEEMbed"), port)
    add(
        (*fixture_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv, value: (
            state.set_fixture_file(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(ParameterSpec(ParameterType.STRING),),
        available=fixture_license,
    )
    add(
        (*fixture_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv: state.fixture_file(inv.indices["channel"], inv.indices["port"]),
        query=True,
        available=fixture_license,
    )
    add(
        (*fixture_port, HeaderNode("STATe")),
        lambda inv, value: (
            state.set_fixture_port(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(boolean,),
        available=fixture_license,
    )
    add(
        (*fixture_port, HeaderNode("STATe")),
        lambda inv: _bool(state.fixture_port(inv.indices["channel"], inv.indices["port"])),
        query=True,
        available=fixture_license,
    )
    embedding_port = (*fixture, HeaderNode("SEND"), HeaderNode("EMBed"), port)
    add(
        (*embedding_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv, value: (
            state.set_embedding_file(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(ParameterSpec(ParameterType.STRING),),
        available=fixture_license,
    )
    add(
        (*embedding_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv: state.embedding_file(inv.indices["channel"], inv.indices["port"]),
        query=True,
        available=fixture_license,
    )
    add(
        (*embedding_port, HeaderNode("STATe")),
        lambda inv, value: (
            state.set_embedding_port(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(boolean,),
        available=fixture_license,
    )
    add(
        (*embedding_port, HeaderNode("STATe")),
        lambda inv: _bool(state.embedding_port(inv.indices["channel"], inv.indices["port"])),
        query=True,
        available=fixture_license,
    )
    add(
        (*fixture, HeaderNode("BALanced"), HeaderNode("TOPology")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "topology", value),
        parameters=(
            ParameterSpec(ParameterType.ENUM, choices=("NONE", "BBALanced", "SBALanced", "MIXed")),
        ),
        available=fixture_license,
    )
    add(
        (*fixture, HeaderNode("BALanced"), HeaderNode("TOPology")),
        lambda inv: state.channel(inv.indices["channel"]).topology,
        query=True,
        available=fixture_license,
    )


def _set(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _set_gate(state, invocation, name: str, value: NumericValue) -> str:
    seconds = float(value.value)
    channel = state.channel(invocation.indices["channel"])
    if name == "gate_start" and seconds > channel.gate_stop:
        raise SCPICommandError(-222, "Data out of range; gate start")
    if name == "gate_stop" and seconds < channel.gate_start:
        raise SCPICommandError(-222, "Data out of range; gate stop")
    setattr(channel, name, seconds)
    return ""


def _fixture(samples: tuple[complex, ...], state: TimeDomainChannel) -> tuple[complex, ...]:
    if not state.fixture_enabled:
        return samples
    deembed_factor = 1 + 0j
    for port, filename in sorted(state.fixture_ports.items()):
        if not state.fixture_port_enabled.get(port, False):
            continue
        deembed_factor *= _file_factor(filename)
    embed_factor = 1 + 0j
    for port, filename in sorted(state.embedding_ports.items()):
        if state.embedding_port_enabled.get(port, False):
            embed_factor *= _file_factor(filename)
    if state.topology == "BBALanced":
        embed_factor *= math.sqrt(2)
    elif state.topology == "SBALanced":
        embed_factor /= math.sqrt(2)
    elif state.topology == "MIXed":
        embed_factor *= 1j
    factor = embed_factor / deembed_factor if deembed_factor else embed_factor
    return tuple(value * factor for value in samples)


def _file_factor(filename: str) -> complex:
    digest = hashlib.sha256(filename.encode("utf-8")).digest()
    magnitude = 0.8 + digest[0] / 637.5
    phase = (digest[1] / 255.0 - 0.5) * 0.2
    return cmath.rect(magnitude, phase)


def _window(samples: tuple[complex, ...], kind: str) -> tuple[complex, ...]:
    if kind == "MINimum" or len(samples) < 2:
        return samples
    denominator = len(samples) - 1
    if kind == "MAXimum":
        weights = (
            0.42
            - 0.5 * math.cos(2 * math.pi * index / denominator)
            + 0.08 * math.cos(4 * math.pi * index / denominator)
            for index in range(len(samples))
        )
    else:
        weights = (
            0.5 - 0.5 * math.cos(2 * math.pi * index / denominator) for index in range(len(samples))
        )
    return tuple(value * weight for value, weight in zip(samples, weights))


def _gate_keeps(state: TimeDomainChannel, point: float) -> bool:
    inside = state.gate_start <= point <= state.gate_stop
    return inside if state.gate_type == "BANDpass" else not inside


def _time_axis(stimulus: tuple[float, ...]) -> tuple[float, ...]:
    if len(stimulus) < 2:
        return (0.0,) * len(stimulus)
    spacing = abs(stimulus[-1] - stimulus[0]) / (len(stimulus) - 1)
    if spacing == 0:
        return (0.0,) * len(stimulus)
    interval = 1.0 / (len(stimulus) * spacing)
    return tuple(index * interval for index in range(len(stimulus)))


def _idft(samples: tuple[complex, ...]) -> tuple[complex, ...]:
    count = len(samples)
    if not count:
        return ()
    return tuple(
        sum(
            value * cmath.exp(2j * math.pi * output * index / count)
            for index, value in enumerate(samples)
        )
        / count
        for output in range(count)
    )


def _dft(samples: tuple[complex, ...]) -> tuple[complex, ...]:
    count = len(samples)
    return tuple(
        sum(
            value * cmath.exp(-2j * math.pi * output * index / count)
            for index, value in enumerate(samples)
        )
        for output in range(count)
    )


def _bool(value: bool) -> str:
    return "1" if value else "0"
