"""Licensed PNA pulse generators and Integrated Pulse measurements."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from scpi_emulator.scenario import ScenarioError, ScenarioPlayer

from .measurements import PNAMeasurementSystem
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
class PulseGenerator:
    enabled: bool = False
    delay: float = 0.0
    width: float = 100e-6
    delay_increment: float = 0.0
    inverted: bool = False
    subpoint_trigger: bool = False


@dataclass
class PulseChannel:
    period: float = 1e-3
    generators: dict[int, PulseGenerator] = field(default_factory=dict)
    trigger_polarity: str = "POSitive"
    trigger_type: str = "LEVel"
    pulse4_adc: bool = False
    mode: str = "OFF"
    master_frequency: float = 1e3
    master_width: float = 100e-6
    profile_start: float = 0.0
    profile_stop: float = 200e-6
    cw_time_auto: bool = True
    detect_auto: bool = True
    drive_auto: bool = True
    if_gain_auto: bool = True
    prf_auto: bool = True
    timing_auto: bool = True
    software_gate: bool = True
    wideband: bool = False
    if_filter_auto: bool = True
    if_capture_mode: bool = False
    if_frequency_auto: bool = True
    if_frequency: float = 9e6
    if_filter_type: str = "TUKey"
    if_parameters: dict[str, float] = field(default_factory=dict)
    path_elements: dict[str, str] = field(default_factory=dict)


class PNAPulseSystem:
    """Process point-in-pulse and profile data over the shared scenario player."""

    def __init__(self, measurements: PNAMeasurementSystem) -> None:
        self.measurements = measurements
        self.channels: dict[int, PulseChannel] = {}
        self.player: ScenarioPlayer | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player

    def reset(self) -> None:
        self.channels.clear()

    def channel(self, number: int) -> PulseChannel:
        return self.channels.setdefault(number, PulseChannel())

    def generator(self, channel: int, number: int) -> PulseGenerator:
        if not 0 <= number <= 4:
            raise SCPICommandError(-222, "Data out of range; pulse generator")
        return self.channel(channel).generators.setdefault(number, PulseGenerator())

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        state = self.channel(channel)
        if state.mode != "PROFile":
            return stimulus
        return _linear(state.profile_start, state.profile_stop, len(stimulus))

    def samples(
        self,
        channel: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        state = self.channel(channel)
        if state.mode == "OFF":
            return samples
        result = "profile" if state.mode == "PROFile" else "point"
        scenario = self._read_trace(f"pulse.{result}", len(samples))
        if scenario is not None:
            return scenario
        if state.mode != "PROFile":
            return samples
        axis = self.axis(channel, stimulus)
        source = self.generator(channel, 1)
        if not source.enabled:
            return samples
        return tuple(
            value if _pulse_on(source, point, state.period) else 0j
            for point, value in zip(axis, samples)
        )

    def _read_trace(self, requested: str, points: int) -> tuple[complex, ...] | None:
        if self.player is None:
            return None
        names = {name.casefold(): name for name in self.player.stream_names}
        stream = names.get(requested.casefold())
        if stream is None:
            return None
        try:
            value = self.player.read(stream)
            values = tuple(complex(item) for item in value)
        except (ScenarioError, TypeError, ValueError) as exc:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r}") from exc
        if len(values) != points:
            raise SCPICommandError(
                -230,
                f"Data corrupt or stale; stream {stream!r} length {len(values)}, expected {points}",
            )
        return values


def register_pulse_commands(registry: CommandRegistry, state: PNAPulseSystem) -> None:
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    pulse_node = HeaderNode("PULSe", index="pulse", index_default=0)
    pulse = (sense, pulse_node)
    integrated = (sense, HeaderNode("SWEep"), HeaderNode("PULSe"))
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    time_value = ParameterSpec(
        ParameterType.NUMBER,
        minimum=Decimal(0),
        maximum=Decimal("7e10"),
        units=frozenset({"S", "MS", "US", "NS"}),
    )
    frequency = ParameterSpec(
        ParameterType.NUMBER,
        minimum=Decimal("-38e9"),
        maximum=Decimal("1e12"),
        units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
    )

    def exists(inv):
        channel = state.measurements.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def licensed(*names):
        return lambda inv: bool(set(names) & inv.capabilities)

    basic_license = licensed(
        "basic_pulsed_rf", "basic-pulsed-rf", "integrated_pulsed_rf", "integrated-pulsed-rf"
    )
    integrated_license = licensed("integrated_pulsed_rf", "integrated-pulsed-rf")

    def add(path, handler, *, query=False, parameters=(), available=None):
        registry.register(CommandSpec(
            tuple(path), handler, tuple(parameters), query=query,
            available=available, exists=exists,
        ))

    add((sense, HeaderNode("PULSe"), HeaderNode("CATalog")),
        lambda inv: "Pulse0,Pulse1,Pulse2,Pulse3,Pulse4",
        query=True, available=basic_license)

    for path in (pulse, (*pulse, HeaderNode("STATe"))):
        add(path, lambda inv, value: _set_generator(state, inv, "enabled", value),
            parameters=(boolean,), available=basic_license)
        add(path, lambda inv: _bool(_generator(state, inv).enabled),
            query=True, available=basic_license)

    for header, attribute, setter in (
        ("DELay", "delay", _set_generator_time),
        ("WIDTh", "width", _set_generator_time),
        ("DINCrement", "delay_increment", _set_generator_time),
    ):
        add((*pulse, HeaderNode(header)),
            lambda inv, value, name=attribute, fn=setter: fn(state, inv, name, value),
            parameters=(time_value,), available=basic_license)
        add((*pulse, HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(_generator(state, inv), name)),
            query=True, available=basic_license)

    add((*pulse, HeaderNode("INVert")),
        lambda inv, value: _set_generator(state, inv, "inverted", value),
        parameters=(boolean,), available=basic_license)
    add((*pulse, HeaderNode("INVert")),
        lambda inv: _bool(_generator(state, inv).inverted),
        query=True, available=basic_license)
    add((*pulse, HeaderNode("SUBPointtrig")),
        lambda inv, value: _set_subpoint(state, inv, value),
        parameters=(boolean,), available=basic_license)
    add((*pulse, HeaderNode("SUBPointtrig")),
        lambda inv: _bool(_subpoint(state, inv)), query=True, available=basic_license)

    add((sense, HeaderNode("PULSe"), HeaderNode("PERiod")),
        lambda inv, value: _set_period(state, inv, value),
        parameters=(time_value,), available=basic_license)
    add((sense, HeaderNode("PULSe"), HeaderNode("PERiod")),
        lambda inv: str(state.channel(inv.indices["channel"]).period),
        query=True, available=basic_license)
    for header, attribute, choices in (
        ("TPOLarity", "trigger_polarity", ("POSitive", "NEGative")),
        ("TTYPe", "trigger_type", ("EDGE", "LEVel")),
    ):
        add((sense, HeaderNode("PULSe"), HeaderNode(header)),
            lambda inv, value, name=attribute: _set(state.channel(inv.indices["channel"]), name, value),
            parameters=(ParameterSpec(ParameterType.ENUM, choices=choices),),
            available=basic_license)
        add((sense, HeaderNode("PULSe"), HeaderNode(header)),
            lambda inv, name=attribute: getattr(state.channel(inv.indices["channel"]), name),
            query=True, available=basic_license)
    pulse4 = (sense, HeaderNode("PULSe", index="pulse", index_default=4), HeaderNode("OPTion"))
    add(pulse4, lambda inv, value: _set(state.channel(inv.indices["channel"]), "pulse4_adc", value),
        parameters=(boolean,), available=basic_license)
    add(pulse4, lambda inv: _bool(state.channel(inv.indices["channel"]).pulse4_adc),
        query=True, available=basic_license)

    add((*integrated, HeaderNode("MODE")),
        lambda inv, value: _set(state.channel(inv.indices["channel"]), "mode", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("OFF", "STD", "PROFile")),),
        available=integrated_license)
    add((*integrated, HeaderNode("MODE")),
        lambda inv: state.channel(inv.indices["channel"]).mode,
        query=True, available=integrated_license)

    for header, attribute in (
        ("CWTime", "cw_time_auto"), ("DETectmode", "detect_auto"),
        ("DRIVe", "drive_auto"), ("IFGain", "if_gain_auto"),
        ("PRF", "prf_auto"), ("TIMing", "timing_auto"),
        ("SWGate", "software_gate"), ("WIDeband", "wideband"),
    ):
        add((*integrated, HeaderNode(header)),
            lambda inv, value, name=attribute: _set(state.channel(inv.indices["channel"]), name, value),
            parameters=(boolean,), available=integrated_license)
        add((*integrated, HeaderNode(header)),
            lambda inv, name=attribute: _bool(getattr(state.channel(inv.indices["channel"]), name)),
            query=True, available=integrated_license)

    master = (*integrated, HeaderNode("MASTer"))
    add((*master, HeaderNode("FREQuency")),
        lambda inv, value: _set_master_frequency(state, inv, value),
        parameters=(frequency,), available=integrated_license)
    add((*master, HeaderNode("FREQuency")),
        lambda inv: str(state.channel(inv.indices["channel"]).master_frequency),
        query=True, available=integrated_license)
    add((*master, HeaderNode("PERiod")),
        lambda inv, value: _set_master_period(state, inv, value),
        parameters=(time_value,), available=integrated_license)
    add((*master, HeaderNode("PERiod")),
        lambda inv: str(1 / state.channel(inv.indices["channel"]).master_frequency),
        query=True, available=integrated_license)
    add((*master, HeaderNode("WIDTh")),
        lambda inv, value: _set_master_width(state, inv, value),
        parameters=(time_value,), available=integrated_license)
    add((*master, HeaderNode("WIDTh")),
        lambda inv: str(state.channel(inv.indices["channel"]).master_width),
        query=True, available=integrated_license)

    profile = (*integrated, HeaderNode("PROFile"))
    for header, attribute in (("STARt", "profile_start"), ("STOP", "profile_stop")):
        add((*profile, HeaderNode(header)),
            lambda inv, value, name=attribute: _set_profile(state, inv, name, value),
            parameters=(time_value,), available=integrated_license)
        add((*profile, HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(state.channel(inv.indices["channel"]), name)),
            query=True, available=integrated_license)

    if_path = (sense, HeaderNode("IF"))
    for path, attribute in (
        ((HeaderNode("FILTer"), HeaderNode("AUTO")), "if_filter_auto"),
        ((HeaderNode("FILTer"), HeaderNode("CMODe")), "if_capture_mode"),
        ((HeaderNode("FREQuency"), HeaderNode("AUTO")), "if_frequency_auto"),
    ):
        add((*if_path, *path),
            lambda inv, value, name=attribute: _set(state.channel(inv.indices["channel"]), name, value),
            parameters=(boolean,), available=integrated_license)
        add((*if_path, *path),
            lambda inv, name=attribute: _bool(getattr(state.channel(inv.indices["channel"]), name)),
            query=True, available=integrated_license)
    add((*if_path, HeaderNode("FREQuency")),
        lambda inv, value: _set_if_frequency(state, inv, value),
        parameters=(frequency,), available=integrated_license)
    add((*if_path, HeaderNode("FREQuency")),
        lambda inv: str(state.channel(inv.indices["channel"]).if_frequency),
        query=True, available=integrated_license)
    stage3 = (*if_path, HeaderNode("FILTer"), HeaderNode("STAGe", index="stage", index_default=3))
    add((*stage3, HeaderNode("TYPE")),
        lambda inv, value: _set_stage3_type(state, inv, value),
        parameters=(ParameterSpec(ParameterType.ENUM,
                                  choices=("RECTangular", "TUKey", "PWINdow", "COEFficient")),),
        available=integrated_license)
    add((*stage3, HeaderNode("TYPE")),
        lambda inv: state.channel(inv.indices["channel"]).if_filter_type,
        query=True, available=integrated_license)
    parameter = (*stage3, HeaderNode("PARameter"))
    add(parameter, lambda inv, name, value: _set_if_parameter(state, inv, name, value),
        parameters=(ParameterSpec(ParameterType.STRING), time_value),
        available=integrated_license)
    add(parameter, lambda inv, name: _query_if_parameter(state, inv, name),
        parameters=(ParameterSpec(ParameterType.STRING),), query=True,
        available=integrated_license)

    path_element = (sense, HeaderNode("PATH"), HeaderNode("CONFig"), HeaderNode("ELEMent"))
    add(path_element, lambda inv, element, value: _set_path_element(
        state, inv, element, value
    ),
        parameters=(ParameterSpec(ParameterType.STRING), ParameterSpec(ParameterType.STRING)),
        available=integrated_license)
    add(path_element, lambda inv, element: _query_path_element(state, inv, element),
        parameters=(ParameterSpec(ParameterType.STRING),), query=True,
        available=integrated_license)
    add((*integrated, HeaderNode("CALibration"), HeaderNode("STATe")),
        lambda inv: "0", query=True, available=integrated_license)


def _generator(state, invocation) -> PulseGenerator:
    return state.generator(invocation.indices["channel"], invocation.indices.get("pulse", 0))


def _set(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _set_generator(state, invocation, name: str, value) -> str:
    return _set(_generator(state, invocation), name, value)


def _set_generator_time(state, invocation, name: str, value: NumericValue) -> str:
    generator = _generator(state, invocation)
    number = _scaled(value)
    if number > 70:
        raise SCPICommandError(-222, "Data out of range; pulse timing")
    return _set(generator, name, number)


def _set_period(state, invocation, value: NumericValue) -> str:
    channel = state.channel(invocation.indices["channel"])
    period = _scaled(value)
    if period <= 0 or period > 70:
        raise SCPICommandError(-222, "Data out of range; pulse period")
    channel.period = period
    return ""


def _set_subpoint(state, invocation, value) -> str:
    _subpoint(state, invocation)
    return _set(_generator(state, invocation), "subpoint_trigger", value)


def _subpoint(state, invocation) -> bool:
    if invocation.indices.get("pulse", 0) != 0:
        raise SCPICommandError(-222, "Data out of range; subpoint trigger requires pulse 0")
    return _generator(state, invocation).subpoint_trigger


def _set_master_frequency(state, invocation, value: NumericValue) -> str:
    target = state.channel(invocation.indices["channel"])
    frequency = _scaled(value)
    if frequency <= 0:
        raise SCPICommandError(-222, "Data out of range; master pulse frequency")
    period = 1 / frequency
    if target.master_width > period:
        raise SCPICommandError(-222, "Data out of range; master pulse frequency")
    target.master_frequency = frequency
    target.period = period
    return ""


def _set_master_period(state, invocation, value: NumericValue) -> str:
    target = state.channel(invocation.indices["channel"])
    period = _scaled(value)
    if period <= 0 or period > 70 or target.master_width > period:
        raise SCPICommandError(-222, "Data out of range; master pulse period")
    target.master_frequency = 1 / period
    target.period = period
    return ""


def _set_master_width(state, invocation, value: NumericValue) -> str:
    target = state.channel(invocation.indices["channel"])
    width = _scaled(value)
    if width > 70 or width > 1 / target.master_frequency:
        raise SCPICommandError(-222, "Data out of range; master pulse width")
    target.master_width = width
    state.generator(invocation.indices["channel"], 1).width = width
    return ""


def _set_profile(state, invocation, name: str, value: NumericValue) -> str:
    target = state.channel(invocation.indices["channel"])
    number = _scaled(value)
    if number > 70:
        raise SCPICommandError(-222, "Data out of range; pulse profile time")
    if name == "profile_start" and number > target.profile_stop:
        raise SCPICommandError(-222, "Data out of range; pulse profile start")
    if name == "profile_stop" and number < target.profile_start:
        raise SCPICommandError(-222, "Data out of range; pulse profile stop")
    return _set(target, name, number)


def _set_if_frequency(state, invocation, value: NumericValue) -> str:
    frequency = _scaled(value)
    if abs(frequency) > 38e6:
        raise SCPICommandError(-222, "Data out of range; IF frequency")
    return _set(state.channel(invocation.indices["channel"]), "if_frequency", frequency)


def _set_stage3_type(state, invocation, value: str) -> str:
    if invocation.indices.get("stage", 3) != 3:
        raise SCPICommandError(-222, "Data out of range; pulse filter stage")
    return _set(state.channel(invocation.indices["channel"]), "if_filter_type", value)


def _set_if_parameter(state, invocation, name: str, value: NumericValue) -> str:
    if invocation.indices.get("stage", 3) != 3:
        raise SCPICommandError(-222, "Data out of range; pulse filter stage")
    normalized = name.strip().upper()
    if normalized not in {"C", "P", "D", "W", "R", "M"}:
        raise SCPICommandError(-224, "Illegal parameter value; IF filter parameter")
    state.channel(invocation.indices["channel"]).if_parameters[normalized] = _scaled(value)
    return ""


def _query_if_parameter(state, invocation, name: str) -> str:
    if invocation.indices.get("stage", 3) != 3:
        raise SCPICommandError(-222, "Data out of range; pulse filter stage")
    normalized = name.strip().upper()
    return str(state.channel(invocation.indices["channel"]).if_parameters.get(normalized, 0.0))


def _set_path_element(state, invocation, element: str, value: str) -> str:
    element = element.strip()
    value = value.strip()
    if not element or not value:
        raise SCPICommandError(-224, "Illegal parameter value; path element")
    state.channel(invocation.indices["channel"]).path_elements[element] = value
    return ""


def _query_path_element(state, invocation, element: str) -> str:
    return state.channel(invocation.indices["channel"]).path_elements.get(element.strip(), "")


def _scaled(value: NumericValue) -> float:
    scale = {
        None: 1.0, "S": 1.0, "MS": 1e-3, "US": 1e-6, "NS": 1e-9,
        "HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9,
    }
    return float(value.value) * scale[value.unit]


def _linear(start: float, stop: float, points: int) -> tuple[float, ...]:
    if points <= 1:
        return (start,) * points
    step = (stop - start) / (points - 1)
    return tuple(start + index * step for index in range(points))


def _pulse_on(generator: PulseGenerator, point: float, period: float) -> bool:
    offset = point % period
    active = generator.delay <= offset <= generator.delay + generator.width
    return not active if generator.inverted else active


def _bool(value: bool) -> str:
    return "1" if value else "0"
