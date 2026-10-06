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
    transform_start: float | None = None
    transform_stop: float | None = None
    transform_stimulus: str = "IMPulse"
    transform_clip: bool = True
    kaiser_beta: float = 6.0
    impulse_width: float = 0.0
    step_rise_time: float = 0.0
    marker_mode: str = "AUTO"
    marker_unit: str = "METRs"
    gate_enabled: bool = False
    gate_start: float = 0.0
    gate_stop: float = math.inf
    gate_type: str = "BANDpass"
    gate_shape: str = "NORMal"
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
        return _selected_time_axis(state, stimulus) if state.transform_enabled else stimulus

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
        time_samples = _idft(_window(adjusted, state))
        if state.gate_enabled:
            axis = _selected_time_axis(state, stimulus)
            time_samples = tuple(
                value if _gate_keeps(state, point) else 0j
                for point, value in zip(axis, time_samples)
            )
        if state.transform_type in {"STEP", "LPSTep"}:
            total = 0j
            integrated = []
            for value in time_samples:
                total += value
                integrated.append(total)
            time_samples = tuple(integrated)
        elif state.transform_type in {"LOWPass", "LPASs", "LPIMpulse"}:
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
    gate_roots = (
        (calc, HeaderNode("FILTer"), HeaderNode("TIME")),
        (calc, HeaderNode("FILTer"), HeaderNode("GATE"), HeaderNode("TIME")),
    )
    fixture = (calc, HeaderNode("FSIMulator"))
    port = HeaderNode("PORT", index="port", index_default=1)
    boolean = ParameterSpec(ParameterType.BOOLEAN)

    def exists(inv):
        channel = state.measurements.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def option_enabled(*names):
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

    time_option = option_enabled("time_domain", "time-domain", "enhanced_time_domain")
    fixture_option = option_enabled("fixture_removal", "fixture-removal")

    add(
        (*transform, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "transform_enabled", value),
        parameters=(boolean,),
        available=time_option,
    )
    add(
        (*transform, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).transform_enabled),
        query=True,
        available=time_option,
    )
    add(
        (*transform, HeaderNode("TYPE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "transform_type", value),
        parameters=(
            ParameterSpec(
                ParameterType.ENUM,
                choices=(
                    "BANDpass",
                    "LOWPass",
                    "IMPulse",
                    "STEP",
                    "BPASs",
                    "LPASs",
                    "LPIMpulse",
                    "LPSTep",
                ),
            ),
        ),
        available=time_option,
    )
    add(
        (*transform, HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).transform_type,
        query=True,
        available=time_option,
    )
    add(
        transform,
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "transform_type", value),
        parameters=(
            ParameterSpec(
                ParameterType.ENUM,
                choices=(
                    "BANDpass",
                    "LOWPass",
                    "IMPulse",
                    "STEP",
                    "BPASs",
                    "LPASs",
                    "LPIMpulse",
                    "LPSTep",
                ),
            ),
        ),
        available=time_option,
    )
    add(
        transform,
        lambda inv: state.channel(inv.indices["channel"]).transform_type,
        query=True,
        available=time_option,
    )
    add(
        (*transform, HeaderNode("WINDow")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "window", value),
        parameters=(
            ParameterSpec(
                ParameterType.ENUM,
                choices=(
                    "MINimum",
                    "NORMal",
                    "MAXimum",
                    "WIDE",
                    "KAISer",
                    "RECTangle",
                    "HAMMing",
                    "HANN",
                    "BOHMan",
                ),
            ),
        ),
        available=time_option,
    )
    add(
        (*transform, HeaderNode("WINDow")),
        lambda inv: state.channel(inv.indices["channel"]).window,
        query=True,
        available=time_option,
    )

    time_units = frozenset({"S", "MS", "US", "NS"})
    time_number = ParameterSpec(ParameterType.NUMBER, units=time_units)
    positive_time = ParameterSpec(ParameterType.NUMBER, minimum=0, units=time_units)
    for header, attribute in (("STARt", "transform_start"), ("STOP", "transform_stop")):
        add(
            (*transform, HeaderNode(header)),
            lambda inv, value, name=attribute: _set_transform_bound(state, inv, name, value),
            parameters=(time_number,),
            available=time_option,
        )
        add(
            (*transform, HeaderNode(header)),
            lambda inv, name=attribute: _optional_number(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=time_option,
        )
    for header, setter, getter in (
        ("CENTer", _set_transform_center, _transform_center),
        ("SPAN", _set_transform_span, _transform_span),
    ):
        add(
            (*transform, HeaderNode(header)),
            lambda inv, value, operation=setter: operation(state, inv, value),
            parameters=((positive_time if header == "SPAN" else time_number),),
            available=time_option,
        )
        add(
            (*transform, HeaderNode(header)),
            lambda inv, operation=getter: str(operation(state.channel(inv.indices["channel"]))),
            query=True,
            available=time_option,
        )
    for path, attribute, parameter in (
        (
            (HeaderNode("STIMulus"),),
            "transform_stimulus",
            ParameterSpec(ParameterType.ENUM, choices=("STEP", "IMPulse")),
        ),
        ((HeaderNode("CLIP"),), "transform_clip", boolean),
        (
            (HeaderNode("KBESsel"),),
            "kaiser_beta",
            ParameterSpec(ParameterType.NUMBER, minimum=0, maximum=13),
        ),
        ((HeaderNode("IMPulse"), HeaderNode("WIDTh")), "impulse_width", positive_time),
        ((HeaderNode("STEP"), HeaderNode("RTIMe")), "step_rise_time", positive_time),
        (
            (HeaderNode("MARKer"), HeaderNode("MODE")),
            "marker_mode",
            ParameterSpec(ParameterType.ENUM, choices=("AUTO", "REFLection", "TRANsmission")),
        ),
        (
            (HeaderNode("MARKer"), HeaderNode("UNIT")),
            "marker_unit",
            ParameterSpec(ParameterType.ENUM, choices=("METRs", "FEET", "INCHes")),
        ),
    ):
        add(
            (*transform, *path),
            lambda inv, value, name=attribute: _set_transform_setting(state, inv, name, value),
            parameters=(parameter,),
            available=time_option,
        )
        add(
            (*transform, *path),
            lambda inv, name=attribute: _format_setting(
                getattr(state.channel(inv.indices["channel"]), name)
            ),
            query=True,
            available=time_option,
        )

    for gate in gate_roots:
        add(
            gate,
            lambda inv, value: _set(state.channel(inv.indices["channel"]), "gate_type", value),
            parameters=(ParameterSpec(ParameterType.ENUM, choices=("BANDpass", "BPASs", "NOTCh")),),
            available=time_option,
        )
        add(
            gate,
            lambda inv: state.channel(inv.indices["channel"]).gate_type,
            query=True,
            available=time_option,
        )
        add(
            (*gate, HeaderNode("STATe")),
            lambda inv, value: _set(state.channel(inv.indices["channel"]), "gate_enabled", value),
            parameters=(boolean,),
            available=time_option,
        )
        add(
            (*gate, HeaderNode("STATe")),
            lambda inv: _bool(state.channel(inv.indices["channel"]).gate_enabled),
            query=True,
            available=time_option,
        )
        for header, attribute in (("STARt", "gate_start"), ("STOP", "gate_stop")):
            add(
                (*gate, HeaderNode(header)),
                lambda inv, value, name=attribute: _set_gate(state, inv, name, value),
                parameters=(time_number,),
                available=time_option,
            )
            add(
                (*gate, HeaderNode(header)),
                lambda inv, name=attribute: str(
                    getattr(state.channel(inv.indices["channel"]), name)
                ),
                query=True,
                available=time_option,
            )
        for header, setter, getter in (
            ("CENTer", _set_gate_center, _gate_center),
            ("SPAN", _set_gate_span, _gate_span),
        ):
            add(
                (*gate, HeaderNode(header)),
                lambda inv, value, operation=setter: operation(state, inv, value),
                parameters=((positive_time if header == "SPAN" else time_number),),
                available=time_option,
            )
            add(
                (*gate, HeaderNode(header)),
                lambda inv, operation=getter: str(operation(state.channel(inv.indices["channel"]))),
                query=True,
                available=time_option,
            )
        for header, attribute, choices in (
            ("TYPE", "gate_type", ("BANDpass", "BPASs", "NOTCh")),
            ("SHAPe", "gate_shape", ("MINimum", "NORMal", "WIDE", "MAXimum")),
        ):
            add(
                (*gate, HeaderNode(header)),
                lambda inv, value, name=attribute: _set(
                    state.channel(inv.indices["channel"]), name, value
                ),
                parameters=(ParameterSpec(ParameterType.ENUM, choices=choices),),
                available=time_option,
            )
            add(
                (*gate, HeaderNode(header)),
                lambda inv, name=attribute: getattr(state.channel(inv.indices["channel"]), name),
                query=True,
                available=time_option,
            )

    add(
        (*fixture, HeaderNode("STATe")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "fixture_enabled", value),
        parameters=(boolean,),
        available=fixture_option,
    )
    add(
        (*fixture, HeaderNode("STATe")),
        lambda inv: _bool(state.channel(inv.indices["channel"]).fixture_enabled),
        query=True,
        available=fixture_option,
    )
    fixture_port = (*fixture, HeaderNode("SEND"), HeaderNode("DEEMbed"), port)
    add(
        (*fixture_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv, value: (
            state.set_fixture_file(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(ParameterSpec(ParameterType.STRING),),
        available=fixture_option,
    )
    add(
        (*fixture_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv: state.fixture_file(inv.indices["channel"], inv.indices["port"]),
        query=True,
        available=fixture_option,
    )
    add(
        (*fixture_port, HeaderNode("STATe")),
        lambda inv, value: (
            state.set_fixture_port(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(boolean,),
        available=fixture_option,
    )
    add(
        (*fixture_port, HeaderNode("STATe")),
        lambda inv: _bool(state.fixture_port(inv.indices["channel"], inv.indices["port"])),
        query=True,
        available=fixture_option,
    )
    embedding_port = (*fixture, HeaderNode("SEND"), HeaderNode("EMBed"), port)
    add(
        (*embedding_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv, value: (
            state.set_embedding_file(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(ParameterSpec(ParameterType.STRING),),
        available=fixture_option,
    )
    add(
        (*embedding_port, HeaderNode("USER"), HeaderNode("FILename")),
        lambda inv: state.embedding_file(inv.indices["channel"], inv.indices["port"]),
        query=True,
        available=fixture_option,
    )
    add(
        (*embedding_port, HeaderNode("STATe")),
        lambda inv, value: (
            state.set_embedding_port(inv.indices["channel"], inv.indices["port"], value) or ""
        ),
        parameters=(boolean,),
        available=fixture_option,
    )
    add(
        (*embedding_port, HeaderNode("STATe")),
        lambda inv: _bool(state.embedding_port(inv.indices["channel"], inv.indices["port"])),
        query=True,
        available=fixture_option,
    )
    add(
        (*fixture, HeaderNode("BALanced"), HeaderNode("TOPology")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "topology", value),
        parameters=(
            ParameterSpec(ParameterType.ENUM, choices=("NONE", "BBALanced", "SBALanced", "MIXed")),
        ),
        available=fixture_option,
    )
    add(
        (*fixture, HeaderNode("BALanced"), HeaderNode("TOPology")),
        lambda inv: state.channel(inv.indices["channel"]).topology,
        query=True,
        available=fixture_option,
    )


def _set(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _set_transform_setting(state, invocation, name: str, value) -> str:
    channel = state.channel(invocation.indices["channel"])
    if isinstance(value, NumericValue):
        stored = (
            _seconds(value) if name in {"impulse_width", "step_rise_time"} else float(value.value)
        )
    else:
        stored = value
    setattr(channel, name, stored)
    if name == "transform_stimulus":
        if value == "STEP":
            channel.transform_type = "LPSTep"
        elif channel.transform_type in {"STEP", "LPSTep"}:
            channel.transform_type = "LPIMpulse"
    return ""


def _format_setting(value) -> str:
    if isinstance(value, bool):
        return _bool(value)
    return str(value)


def _optional_number(value: float | None) -> str:
    return str(0.0 if value is None else value)


def _set_transform_bound(state, invocation, name: str, value: NumericValue) -> str:
    seconds = _seconds(value)
    channel = state.channel(invocation.indices["channel"])
    other = channel.transform_stop if name == "transform_start" else channel.transform_start
    if other is not None:
        invalid = seconds > other if name == "transform_start" else seconds < other
        if invalid:
            raise SCPICommandError(-222, f"Data out of range; {name.replace('_', ' ')}")
    setattr(channel, name, seconds)
    return ""


def _transform_center(channel: TimeDomainChannel) -> float:
    if channel.transform_start is None and channel.transform_stop is None:
        return 0.0
    start = channel.transform_start if channel.transform_start is not None else 0.0
    stop = channel.transform_stop if channel.transform_stop is not None else 0.0
    return (start + stop) / 2.0


def _transform_span(channel: TimeDomainChannel) -> float:
    if channel.transform_start is None or channel.transform_stop is None:
        return 0.0
    return channel.transform_stop - channel.transform_start


def _set_transform_center(state, invocation, value: NumericValue) -> str:
    channel = state.channel(invocation.indices["channel"])
    center = _seconds(value)
    span = _transform_span(channel) or 20e-9
    channel.transform_start = center - span / 2.0
    channel.transform_stop = center + span / 2.0
    return ""


def _set_transform_span(state, invocation, value: NumericValue) -> str:
    channel = state.channel(invocation.indices["channel"])
    span = _seconds(value)
    center = _transform_center(channel)
    channel.transform_start = center - span / 2.0
    channel.transform_stop = center + span / 2.0
    return ""


def _set_gate(state, invocation, name: str, value: NumericValue) -> str:
    seconds = _seconds(value)
    channel = state.channel(invocation.indices["channel"])
    if name == "gate_start" and seconds > channel.gate_stop:
        raise SCPICommandError(-222, "Data out of range; gate start")
    if name == "gate_stop" and seconds < channel.gate_start:
        raise SCPICommandError(-222, "Data out of range; gate stop")
    setattr(channel, name, seconds)
    return ""


def _gate_center(channel: TimeDomainChannel) -> float:
    if not math.isfinite(channel.gate_stop):
        return channel.gate_start
    return (channel.gate_start + channel.gate_stop) / 2.0


def _gate_span(channel: TimeDomainChannel) -> float:
    if not math.isfinite(channel.gate_stop):
        return math.inf
    return channel.gate_stop - channel.gate_start


def _set_gate_center(state, invocation, value: NumericValue) -> str:
    channel = state.channel(invocation.indices["channel"])
    center = _seconds(value)
    span = _gate_span(channel)
    if not math.isfinite(span):
        span = 20e-9
    channel.gate_start = center - span / 2.0
    channel.gate_stop = center + span / 2.0
    return ""


def _set_gate_span(state, invocation, value: NumericValue) -> str:
    channel = state.channel(invocation.indices["channel"])
    span = _seconds(value)
    center = _gate_center(channel)
    channel.gate_start = center - span / 2.0
    channel.gate_stop = center + span / 2.0
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


def _window(samples: tuple[complex, ...], state: TimeDomainChannel) -> tuple[complex, ...]:
    kind = state.window
    if kind in {"MINimum", "RECTangle"} or len(samples) < 2:
        return samples
    denominator = len(samples) - 1
    if kind in {"MAXimum", "BOHMan"}:
        weights = (
            0.42
            - 0.5 * math.cos(2 * math.pi * index / denominator)
            + 0.08 * math.cos(4 * math.pi * index / denominator)
            for index in range(len(samples))
        )
    elif kind in {"WIDE", "HAMMing"}:
        weights = (
            0.54 - 0.46 * math.cos(2 * math.pi * index / denominator)
            for index in range(len(samples))
        )
    elif kind == "KAISer":
        divisor = _bessel_i0(state.kaiser_beta)
        weights = (
            _bessel_i0(
                state.kaiser_beta
                * math.sqrt(max(0.0, 1.0 - (2.0 * index / denominator - 1.0) ** 2))
            )
            / divisor
            for index in range(len(samples))
        )
    else:
        weights = (
            0.5 - 0.5 * math.cos(2 * math.pi * index / denominator) for index in range(len(samples))
        )
    return tuple(value * weight for value, weight in zip(samples, weights))


def _gate_keeps(state: TimeDomainChannel, point: float) -> bool:
    inside = state.gate_start <= point <= state.gate_stop
    return inside if state.gate_type in {"BANDpass", "BPASs"} else not inside


def _selected_time_axis(state: TimeDomainChannel, stimulus: tuple[float, ...]) -> tuple[float, ...]:
    natural = _time_axis(stimulus)
    if not natural:
        return natural
    start = state.transform_start
    stop = state.transform_stop
    if start is None and stop is None:
        return natural
    first = natural[0] if start is None else start
    last = natural[-1] if stop is None else stop
    if len(natural) == 1:
        return (first,)
    step = (last - first) / (len(natural) - 1)
    return tuple(first + index * step for index in range(len(natural)))


def _bessel_i0(value: float) -> float:
    total = 1.0
    term = 1.0
    squared = value * value / 4.0
    for index in range(1, 32):
        term *= squared / (index * index)
        total += term
        if term < total * 1e-15:
            break
    return total


def _seconds(value: NumericValue) -> float:
    scales = {None: 1.0, "S": 1.0, "MS": 1e-3, "US": 1e-6, "NS": 1e-9}
    return float(value.value) * scales[value.unit]


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
