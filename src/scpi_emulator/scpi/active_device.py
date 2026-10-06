"""Scenario-backed gain-compression and noise-figure applications."""

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
class GainCompressionState:
    enabled: bool = False
    compression_enabled: bool = False
    power_start: float = -30.0
    power_stop: float = 0.0
    points: int = 11
    compression_power: float = 0.0
    compression_db: float = 1.0
    reference: str = "INTernal"
    acquisition_mode: str = "SMARtsweep"
    compression_algorithm: str = "CFLG"
    backoff_level: float = 10.0
    delta_x: float = 10.0
    delta_y: float = 9.0
    interpolate: bool = False
    phase_level: float = 2.0
    phase_mode: str = "MAGNitude"
    saturation_level: float = 0.1
    eos_operation: str = "STANdard"
    mixer_reference: bool = False
    input_port: int = 1
    output_port: int = 2
    source_override: bool = False
    linear_input_aperture: float = 5.0
    linear_input_level: float = -25.0
    reverse_power_level: float = -5.0
    safe_coarse_adjustment: float = 3.0
    safe_dc_limit: float = -5.0
    safe_dc_parameter: str = ""
    safe_enabled: bool = False
    safe_fine_adjustment: float = 1.0
    safe_fine_threshold: float = 0.5
    safe_maximum_limit: float = 30.0
    smart_cdc: bool = False
    smart_maximum_iterations: int = 20
    smart_show_iterations: bool = False
    smart_settling_time: float = 0.0
    smart_tolerance: float = 0.05
    frequency_points: int = 201
    power_smoothing: bool = False
    power_smoothing_aperture: float = 25.0


@dataclass
class NoiseFigureState:
    enabled: bool = False
    source_power: float = -20.0
    bandwidth: float = 1e6
    average_count: int = 1
    temperature: float = 290.0


class VNAActiveDeviceSystem:
    """Provide trace and scalar application results from shared DUT scenarios."""

    def __init__(
        self, measurements: VNAMeasurementSystem, data_format: DataFormat, port_count: int = 2
    ) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.gain_channels: dict[int, GainCompressionState] = {}
        self.noise_channels: dict[int, NoiseFigureState] = {}
        self.player: ScenarioPlayer | None = None
        self.bindings: dict[tuple[str, str], str] = {}
        self.port_count = port_count

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player

    def reset(self) -> None:
        self.gain_channels.clear()
        self.noise_channels.clear()

    def gain(self, channel: int) -> GainCompressionState:
        return self.gain_channels.setdefault(channel, GainCompressionState())

    def noise(self, channel: int) -> NoiseFigureState:
        return self.noise_channels.setdefault(channel, NoiseFigureState())

    def bind_result(self, application: str, result: str, stream: str) -> None:
        self.bindings[(application.casefold(), result.casefold())] = stream

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        state = self.gain(channel)
        if not state.enabled:
            return stimulus
        return _linear(state.power_start, state.power_stop, state.points)

    def samples(
        self,
        channel: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        state = self.gain(channel)
        if not state.enabled:
            return samples
        powers = self.axis(channel, stimulus)
        source = _resample(samples, len(powers))
        gains = self._peek_trace("gain_compression", "gain", len(powers))
        if gains is None:
            gains = tuple(20 * math.log10(abs(value)) if value else -200.0 for value in source)
        output = []
        for value, power, gain in zip(source, powers, gains):
            compression = (
                max(0.0, power - state.compression_power) if state.compression_enabled else 0
            )
            magnitude = 10 ** ((float(gain) - compression) / 20)
            phase = math.atan2(value.imag, value.real)
            output.append(complex(magnitude * math.cos(phase), magnitude * math.sin(phase)))
        return tuple(output)

    def gain_data(self, channel: int, result: str):
        state = self.gain(channel)
        powers = _linear(state.power_start, state.power_stop, state.points)
        normalized = result.upper()
        if normalized == "IPOW":
            values = powers
        else:
            gain = self._read_trace("gain_compression", "gain", len(powers))
            if gain is None:
                gain = tuple(12.0 - max(0.0, power - state.compression_power) for power in powers)
            compression = tuple(max(0.0, gain[0] - value) for value in gain)
            if normalized == "GAIN":
                values = gain
            elif normalized == "COMP":
                values = compression
            else:
                output = self._read_trace("gain_compression", "output_power", len(powers))
                values = (
                    output
                    if output is not None
                    else tuple(power + value for power, value in zip(powers, gain))
                )
        return self.data_format.encode_values(values)

    def gain_scalar(self, channel: int, result: str) -> str:
        state = self.gain(channel)
        powers = _linear(state.power_start, state.power_stop, state.points)
        gain = self._read_trace("gain_compression", "gain", len(powers))
        if gain is None:
            gain = tuple(12.0 - max(0.0, power - state.compression_power) for power in powers)
        values = _gain_result_values(state, powers, gain)
        return f"{values[result.upper()]:.12g}"

    def gain_status(self, channel: int) -> str:
        return (
            "1"
            if float(self.gain_scalar(channel, "COMP")) >= self.gain(channel).compression_db
            else "0"
        )

    def noise_data(self, channel: int, result: str):
        measurement = self.measurements.selected(channel)
        points = len(measurement.stimulus)
        normalized = result.upper()
        values = self._read_trace("noise_figure", normalized.casefold(), points)
        if values is None:
            gain = tuple(
                20 * math.log10(abs(value)) if value else -200.0 for value in measurement.samples
            )
            if normalized == "GAIN":
                values = gain
            elif normalized == "NF":
                values = tuple(max(0.1, 3.0 - value * 0.02) for value in gain)
            elif normalized in ("YFAC", "YFACTOR"):
                values = tuple(10 ** (max(0.1, 3.0 - value * 0.02) / 10) for value in gain)
            else:
                temperature = self.noise(channel).temperature
                values = (temperature,) * points
        return self.data_format.encode_values(values)

    def noise_scalar(self, channel: int, result: str) -> str:
        measurement = self.measurements.selected(channel)
        points = len(measurement.stimulus)
        values = self._read_trace("noise_figure", result.casefold(), points)
        if values is None:
            gain = tuple(
                20 * math.log10(abs(value)) if value else -200.0 for value in measurement.samples
            )
            values = (
                gain
                if result.upper() == "GAIN"
                else tuple(max(0.1, 3.0 - value * 0.02) for value in gain)
            )
        return f"{sum(values) / len(values):.12g}" if values else "0"

    def _stream(self, application: str, result: str) -> str | None:
        if self.player is None:
            return None
        requested = self.bindings.get((application.casefold(), result.casefold()))
        candidates = (requested, f"{application}.{result}", result)
        names = {name.casefold(): name for name in self.player.stream_names}
        return next(
            (
                names[value.casefold()]
                for value in candidates
                if value and value.casefold() in names
            ),
            None,
        )

    def _read_trace(self, application: str, result: str, points: int):
        stream = self._stream(application, result)
        if stream is None:
            return None
        try:
            value = self.player.read(stream)
        except ScenarioError as exc:
            raise SCPICommandError(-230, f"Data corrupt or stale; {exc}") from exc
        return _numeric_trace(value, stream, points)

    def _peek_trace(self, application: str, result: str, points: int):
        stream = self._stream(application, result)
        if stream is None:
            return None
        try:
            value = self.player.peek(stream)
        except ScenarioError as exc:
            raise SCPICommandError(-230, f"Data corrupt or stale; {exc}") from exc
        return _numeric_trace(value, stream, points)


def register_active_device_commands(
    registry: CommandRegistry, state: VNAActiveDeviceSystem
) -> None:
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    calc = HeaderNode("CALCulate", index="channel", index_default=1)
    gc = (sense, HeaderNode("GCompression"))
    gcs = (sense, HeaderNode("GCSetup"))
    calc_gc = (calc, HeaderNode("GCompression"))
    noise = (sense, HeaderNode("NOISe"))
    calc_noise = (calc, HeaderNode("NOISe"))
    boolean = ParameterSpec(ParameterType.BOOLEAN)

    def exists(inv):
        channel = state.measurements.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def option_enabled(*names):
        return lambda inv: bool(set(names) & inv.capabilities)

    gain_option = option_enabled("gain_compression", "gain-compression")
    noise_option = option_enabled("noise_figure", "noise-figure")

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

    add(
        (*gc, HeaderNode("STATe")),
        lambda inv, value: _set(state.gain(inv.indices["channel"]), "enabled", value),
        parameters=(boolean,),
        available=gain_option,
    )
    add(
        (*gc, HeaderNode("STATe")),
        lambda inv: _bool(state.gain(inv.indices["channel"]).enabled),
        query=True,
        available=gain_option,
    )
    compression = (*gc, HeaderNode("COMPression"))
    add(
        (*compression, HeaderNode("STATe")),
        lambda inv, value: _set(state.gain(inv.indices["channel"]), "compression_enabled", value),
        parameters=(boolean,),
        available=gain_option,
    )
    add(
        (*compression, HeaderNode("STATe")),
        lambda inv: _bool(state.gain(inv.indices["channel"]).compression_enabled),
        query=True,
        available=gain_option,
    )
    add(
        (*compression, HeaderNode("POWer")),
        lambda inv, value: _set(
            state.gain(inv.indices["channel"]), "compression_power", float(value.value)
        ),
        parameters=(
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
        ),
        available=gain_option,
    )
    add(
        (*compression, HeaderNode("POWer")),
        lambda inv: str(state.gain(inv.indices["channel"]).compression_power),
        query=True,
        available=gain_option,
    )
    add(
        (*compression, HeaderNode("DB")),
        lambda inv, value: _set(
            state.gain(inv.indices["channel"]), "compression_db", float(value.value)
        ),
        parameters=(ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(100)),),
        available=gain_option,
    )
    add(
        (*compression, HeaderNode("DB")),
        lambda inv: str(state.gain(inv.indices["channel"]).compression_db),
        query=True,
        available=gain_option,
    )
    add(
        (*compression, HeaderNode("REFerence")),
        lambda inv, value: _set(state.gain(inv.indices["channel"]), "reference", value),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("INTernal", "EXTernal")),),
        available=gain_option,
    )
    add(
        (*compression, HeaderNode("REFerence")),
        lambda inv: state.gain(inv.indices["channel"]).reference,
        query=True,
        available=gain_option,
    )
    power = (*gc, HeaderNode("POWer"))
    for header, attribute in (("STARt", "power_start"), ("STOP", "power_stop")):
        add(
            (*power, HeaderNode(header)),
            lambda inv, value, name=attribute: _set_power(state, inv, name, value),
            parameters=(
                ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
            ),
            available=gain_option,
        )
        add(
            (*power, HeaderNode(header)),
            lambda inv, name=attribute: str(getattr(state.gain(inv.indices["channel"]), name)),
            query=True,
            available=gain_option,
        )
    add(
        (*gc, HeaderNode("SWEep"), HeaderNode("POINts")),
        lambda inv, value: _set(state.gain(inv.indices["channel"]), "points", value),
        parameters=(ParameterSpec(ParameterType.INTEGER, minimum=2, maximum=100001),),
        available=gain_option,
    )
    add(
        (*gc, HeaderNode("SWEep"), HeaderNode("POINts")),
        lambda inv: str(state.gain(inv.indices["channel"]).points),
        query=True,
        available=gain_option,
    )
    add(
        (*calc_gc, HeaderNode("DATA")),
        lambda inv, result: state.gain_data(inv.indices["channel"], result),
        parameters=(ParameterSpec(ParameterType.ENUM, choices=("IPOW", "OPOW", "GAIN", "COMP")),),
        query=True,
        available=gain_option,
    )
    for header, result in (("PIN", "PIN"), ("POUT", "POUT"), ("GAIN", "GAIN"), ("COMP", "COMP")):
        add(
            (*calc_gc, HeaderNode("RESult"), HeaderNode(header)),
            lambda inv, name=result: state.gain_scalar(inv.indices["channel"], name),
            query=True,
            available=gain_option,
        )
    add(
        (*calc_gc, HeaderNode("STATus")),
        lambda inv: state.gain_status(inv.indices["channel"]),
        query=True,
        available=gain_option,
    )
    add(
        (*gc, HeaderNode("CALibration"), HeaderNode("STATe")),
        lambda inv: "0",
        query=True,
        available=gain_option,
    )

    _register_gcsetup_commands(add, gcs, state, gain_option)

    add(
        (*noise, HeaderNode("STATe")),
        lambda inv, value: _set(state.noise(inv.indices["channel"]), "enabled", value),
        parameters=(boolean,),
        available=noise_option,
    )
    add(
        (*noise, HeaderNode("STATe")),
        lambda inv: _bool(state.noise(inv.indices["channel"]).enabled),
        query=True,
        available=noise_option,
    )
    for path, attribute, parameter in (
        (
            (HeaderNode("POWer"),),
            "source_power",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(-120), maximum=Decimal(50)),
        ),
        (
            (HeaderNode("BANDwidth"),),
            "bandwidth",
            ParameterSpec(
                ParameterType.NUMBER,
                minimum=Decimal(1),
                units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
            ),
        ),
        (
            (HeaderNode("AVERage"), HeaderNode("COUNt")),
            "average_count",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100000),
        ),
        (
            (HeaderNode("TEMPerature"),),
            "temperature",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(10000)),
        ),
    ):
        add(
            (*noise, *path),
            lambda inv, value, name=attribute: _set_noise(state, inv, name, value),
            parameters=(parameter,),
            available=noise_option,
        )
        add(
            (*noise, *path),
            lambda inv, name=attribute: str(getattr(state.noise(inv.indices["channel"]), name)),
            query=True,
            available=noise_option,
        )
    add(
        (*calc_noise, HeaderNode("DATA")),
        lambda inv, result: state.noise_data(inv.indices["channel"], result),
        parameters=(
            ParameterSpec(ParameterType.ENUM, choices=("NF", "GAIN", "YFACtor", "TEFFective")),
        ),
        query=True,
        available=noise_option,
    )
    for header, result in (("NF", "NF"), ("GAIN", "GAIN")):
        add(
            (*calc_noise, HeaderNode("RESult"), HeaderNode(header)),
            lambda inv, name=result: state.noise_scalar(inv.indices["channel"], name),
            query=True,
            available=noise_option,
        )
    add(
        (*noise, HeaderNode("CALibration"), HeaderNode("STATe")),
        lambda inv: "0",
        query=True,
        available=noise_option,
    )


def _register_gcsetup_commands(add, root, system, available) -> None:
    """Register the standard gain-compression setup tree over shared state."""

    def number(low, high):
        return ParameterSpec(
            ParameterType.NUMBER, minimum=Decimal(str(low)), maximum=Decimal(str(high))
        )

    def integer(low, high):
        return ParameterSpec(ParameterType.INTEGER, minimum=low, maximum=high)

    boolean = ParameterSpec(ParameterType.BOOLEAN)

    def state(inv):
        return system.gain(inv.indices["channel"])

    def value_pair(path, attribute, parameter, transform=lambda value: value):
        add(
            (*root, *path),
            lambda inv, value, name=attribute, convert=transform: _set(
                state(inv), name, convert(value)
            ),
            parameters=(parameter,),
            available=available,
        )
        add(
            (*root, *path),
            lambda inv, name=attribute: _render(getattr(state(inv), name)),
            query=True,
            available=available,
        )

    value_pair(
        (HeaderNode("AMODe"),),
        "acquisition_mode",
        ParameterSpec(ParameterType.ENUM, choices=("PFREQuency", "FPOWer", "SMARtsweep")),
    )
    compression = (HeaderNode("COMPression"),)
    value_pair(
        (*compression, HeaderNode("ALGorithm")),
        "compression_algorithm",
        ParameterSpec(ParameterType.ENUM, choices=("CFLG", "CFMG", "BACKoff", "XYCOM", "SAT")),
    )
    value_pair(
        (*compression, HeaderNode("BACKoff"), HeaderNode("LEVel")),
        "backoff_level",
        number(1, 99),
        _numeric,
    )
    value_pair(
        (*compression, HeaderNode("DELTa"), HeaderNode("X")),
        "delta_x",
        number(0.01, 10),
        _numeric,
    )
    value_pair(
        (*compression, HeaderNode("DELTa"), HeaderNode("Y")),
        "delta_y",
        number(0.01, 10),
        _numeric,
    )
    value_pair((*compression, HeaderNode("INTerpolate")), "interpolate", boolean)
    value_pair(
        (*compression, HeaderNode("LEVel")),
        "compression_db",
        number(0.01, 100),
        _numeric,
    )
    value_pair(
        (*compression, HeaderNode("PHASe"), HeaderNode("LEVel")),
        "phase_level",
        number(0.01, 360),
        _numeric,
    )
    value_pair(
        (*compression, HeaderNode("PHASe"), HeaderNode("MODE")),
        "phase_mode",
        ParameterSpec(ParameterType.ENUM, choices=("MAGNitude", "PHASe", "BOTH")),
    )
    value_pair(
        (*compression, HeaderNode("SATuration"), HeaderNode("LEVel")),
        "saturation_level",
        number(0.01, 10),
        _numeric,
    )
    value_pair(
        (HeaderNode("EOSoperation"),),
        "eos_operation",
        ParameterSpec(ParameterType.ENUM, choices=("STANdard", "POFF", "PSTArt", "PSTOp")),
    )
    value_pair(
        (HeaderNode("MIXer"), HeaderNode("REFerence")),
        "mixer_reference",
        boolean,
    )

    port = ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=system.port_count)
    add(
        (*root, HeaderNode("PMAP")),
        lambda inv, input_port, output_port: _set_port_map(state(inv), input_port, output_port),
        parameters=(port, port),
        available=available,
    )
    add(
        (*root, HeaderNode("PMAP"), HeaderNode("INPut")),
        lambda inv: str(state(inv).input_port),
        query=True,
        available=available,
    )
    add(
        (*root, HeaderNode("PMAP"), HeaderNode("OUTPut")),
        lambda inv: str(state(inv).output_port),
        query=True,
        available=available,
    )
    value_pair(
        (HeaderNode("PMAP"), HeaderNode("SOURce"), HeaderNode("OVERride")),
        "source_override",
        boolean,
    )

    power = (HeaderNode("POWer"),)
    value_pair(
        (
            *power,
            HeaderNode("LINear"),
            HeaderNode("INPut"),
            HeaderNode("COMPute"),
            HeaderNode("APERture"),
        ),
        "linear_input_aperture",
        number(0, 25),
        _numeric,
    )
    value_pair(
        (*power, HeaderNode("LINear"), HeaderNode("INPut"), HeaderNode("LEVel")),
        "linear_input_level",
        number(-30, 30),
        _numeric,
    )
    value_pair(
        (*power, HeaderNode("REVerse"), HeaderNode("LEVel")),
        "reverse_power_level",
        number(-30, 30),
        _numeric,
    )
    for header, attribute in (("STARt", "power_start"), ("STOP", "power_stop")):
        add(
            (*root, *power, HeaderNode(header), HeaderNode("LEVel")),
            lambda inv, value, name=attribute: _set_power(system, inv, name, value),
            parameters=(number(-30, 30),),
            available=available,
        )
        add(
            (*root, *power, HeaderNode(header), HeaderNode("LEVel")),
            lambda inv, name=attribute: _render(getattr(state(inv), name)),
            query=True,
            available=available,
        )

    safe = (HeaderNode("SAFE"),)
    for path, attribute, parameter in (
        ((HeaderNode("CPADjustment"),), "safe_coarse_adjustment", number(0, 6)),
        ((HeaderNode("DC"), HeaderNode("MLimit")), "safe_dc_limit", number(-1000, 1000)),
        (
            (HeaderNode("DC"), HeaderNode("PARameter")),
            "safe_dc_parameter",
            ParameterSpec(ParameterType.STRING),
        ),
        ((HeaderNode("ENABle"),), "safe_enabled", boolean),
        ((HeaderNode("FPADjustment"),), "safe_fine_adjustment", number(0, 3)),
        ((HeaderNode("FTHReshold"),), "safe_fine_threshold", number(0, 3)),
        ((HeaderNode("MLimit"),), "safe_maximum_limit", number(-100, 100)),
    ):
        value_pair(
            (*safe, *path),
            attribute,
            parameter,
            _numeric if parameter.type is ParameterType.NUMBER else (lambda value: value),
        )

    add(
        (*root, HeaderNode("SFAilures")),
        lambda inv: "",
        query=True,
        available=available,
    )
    smart = (HeaderNode("SMARt"),)
    for path, attribute, parameter, transform in (
        ((HeaderNode("CDC"),), "smart_cdc", boolean, lambda value: value),
        (
            (HeaderNode("MITerations"),),
            "smart_maximum_iterations",
            integer(1, 500),
            lambda value: value,
        ),
        ((HeaderNode("SITerations"),), "smart_show_iterations", boolean, lambda value: value),
        ((HeaderNode("STIMe"),), "smart_settling_time", number(0, 1e9), _numeric),
        ((HeaderNode("TOLerance"),), "smart_tolerance", number(0.01, 10), _numeric),
    ):
        value_pair((*smart, *path), attribute, parameter, transform)

    sweep = (HeaderNode("SWEep"),)
    value_pair(
        (*sweep, HeaderNode("FREQuency"), HeaderNode("POINts")),
        "frequency_points",
        integer(2, 100001),
    )
    add(
        (*root, *sweep, HeaderNode("POWer"), HeaderNode("POINts")),
        lambda inv, value: _set(state(inv), "points", value),
        parameters=(integer(2, 100001),),
        available=available,
    )
    add(
        (*root, *sweep, HeaderNode("POWer"), HeaderNode("POINts")),
        lambda inv: str(state(inv).points),
        query=True,
        available=available,
    )
    value_pair(
        (*sweep, HeaderNode("POWer"), HeaderNode("SMOoth")),
        "power_smoothing",
        boolean,
    )
    value_pair(
        (*sweep, HeaderNode("POWer"), HeaderNode("SMOoth"), HeaderNode("APERture")),
        "power_smoothing_aperture",
        number(0, 100),
        _numeric,
    )


def _numeric(value: NumericValue) -> float:
    return float(value.value)


def _render(value) -> str:
    if isinstance(value, bool):
        return _bool(value)
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _set_port_map(target: GainCompressionState, input_port: int, output_port: int) -> str:
    if input_port == output_port:
        raise SCPICommandError(-224, "Illegal parameter value; input and output ports must differ")
    target.input_port = input_port
    target.output_port = output_port
    return ""


def _set(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _set_power(state, invocation, name: str, value: NumericValue) -> str:
    target = state.gain(invocation.indices["channel"])
    number = float(value.value)
    if name == "power_start" and number > target.power_stop:
        raise SCPICommandError(-222, "Data out of range; compression power start")
    if name == "power_stop" and number < target.power_start:
        raise SCPICommandError(-222, "Data out of range; compression power stop")
    setattr(target, name, number)
    return ""


def _set_noise(state, invocation, name: str, value) -> str:
    if isinstance(value, NumericValue):
        scale = {None: 1, "HZ": 1, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[value.unit]
        value = float(value.value) * scale
    return _set(state.noise(invocation.indices["channel"]), name, value)


def _gain_result_values(
    state: GainCompressionState,
    powers: tuple[float, ...],
    gains: tuple[float, ...],
) -> dict[str, float]:
    """Select a deterministic result point using the configured compression method."""
    outputs = tuple(power + gain for power, gain in zip(powers, gains))
    reference_gain = max(gains) if state.compression_algorithm == "CFMG" else gains[0]
    compression = tuple(max(0.0, reference_gain - gain) for gain in gains)

    if state.compression_algorithm in {"CFLG", "CFMG"}:
        start = gains.index(max(gains)) if state.compression_algorithm == "CFMG" else 0
        index = _first_at_least(compression, state.compression_db, start=start)
        if state.interpolate and index > start and compression[index] > compression[index - 1]:
            fraction = (state.compression_db - compression[index - 1]) / (
                compression[index] - compression[index - 1]
            )
            pin = _lerp(powers[index - 1], powers[index], fraction)
            gain = _lerp(gains[index - 1], gains[index], fraction)
            return {
                "PIN": pin,
                "POUT": pin + gain,
                "GAIN": gain,
                "COMP": state.compression_db,
            }
    elif state.compression_algorithm == "BACKoff":
        index = _first_at_least(outputs, max(outputs) - state.backoff_level)
    elif state.compression_algorithm == "SAT":
        index = _first_at_least(outputs, max(outputs) - state.saturation_level)
    else:  # XYCOM
        index = next(
            (
                position
                for position in range(1, len(powers))
                if powers[position] - powers[0] >= state.delta_x
                and outputs[position] - outputs[0] <= state.delta_y
            ),
            len(powers) - 1,
        )

    return {
        "PIN": powers[index],
        "POUT": outputs[index],
        "GAIN": gains[index],
        "COMP": compression[index],
    }


def _first_at_least(values: tuple[float, ...], threshold: float, *, start: int = 0) -> int:
    return next(
        (position for position in range(start, len(values)) if values[position] >= threshold),
        len(values) - 1,
    )


def _lerp(start: float, stop: float, fraction: float) -> float:
    return start + (stop - start) * fraction


def _numeric_trace(value, stream: str, points: int) -> tuple[float, ...]:
    if isinstance(value, (int, float, complex)):
        values = (float(value.real if isinstance(value, complex) else value),) * points
    else:
        try:
            values = tuple(
                float(item.real if isinstance(item, complex) else item) for item in value
            )
        except (TypeError, ValueError) as exc:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r}") from exc
    if len(values) != points:
        raise SCPICommandError(
            -230,
            f"Data corrupt or stale; stream {stream!r} length {len(values)}, expected {points}",
        )
    return values


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


def _bool(value: bool) -> str:
    return "1" if value else "0"
