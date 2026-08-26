"""Profile-gated VNA spectrum, distortion, phase-noise, and I/Q applications."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
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
class AdvancedMarker:
    enabled: bool = False
    x: float = 0.0


@dataclass
class AdvancedChannel:
    active: str | None = None
    resolution_bandwidth: float = 100e3
    video_bandwidth: float = 10e3
    detector: str = "PEAK"
    average_count: int = 1
    reference_level: float = 0.0
    sweep_type: str = "FCENter"
    center: float = 13.255e9
    span: float = 26.489e9
    delta_frequency: float = 10e6
    tone1_power: float = -20.0
    tone2_power: float = -20.0
    imd_bandwidth: float = 1e3
    carrier_frequency: float = 1e9
    carrier_power: float = -20.0
    symbol_rate: float = 10e6
    noise_type: str = "PNOise"
    offset_start: float = 10.0
    offset_stop: float = 10e6
    sample_rate: float = 100e6
    capture_time: float = 10e-6
    diq_ranges: list[tuple[float, float, float]] = field(
        default_factory=lambda: [(10.5e6, 26.5e9, 1e3)]
    )
    markers: dict[tuple[str, int], AdvancedMarker] = field(default_factory=dict)


class VNAAdvancedSystem:
    """Apply advanced measurement classes over the shared DUT scenario player."""

    STREAMS = {
        "spectrum": "spectrum.trace",
        "imd": "imd.trace",
        "distortion": "modulation_distortion.trace",
        "phase_noise": "phase_noise.trace",
        "diq": "differential_iq.trace",
        "wideband_iq": "wideband_iq.trace",
    }
    PREFIXES = {
        "spectrum": "spectrum",
        "imd": "imd",
        "distortion": "modulation_distortion",
        "phase_noise": "phase_noise",
        "diq": "differential_iq",
        "wideband_iq": "wideband_iq",
    }

    def __init__(self, measurements: VNAMeasurementSystem, data_format: DataFormat) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.channels: dict[int, AdvancedChannel] = {}
        self.player: ScenarioPlayer | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        self.player = player

    def reset(self) -> None:
        self.channels.clear()

    def channel(self, number: int) -> AdvancedChannel:
        return self.channels.setdefault(number, AdvancedChannel())

    def enabled(self, channel: int, application: str) -> bool:
        return self.channel(channel).active == application

    def enable(self, channel: int, application: str, enabled: bool) -> str:
        state = self.channel(channel)
        if enabled:
            state.active = application
        elif state.active == application:
            state.active = None
        return ""

    def axis(self, channel: int, stimulus: tuple[float, ...]) -> tuple[float, ...]:
        state = self.channel(channel)
        points = len(stimulus)
        if state.active == "phase_noise":
            return _logspace(state.offset_start, state.offset_stop, points)
        if state.active in {"diq", "wideband_iq"}:
            return _linear(0.0, state.capture_time, points)
        if state.active == "imd":
            return _linear(state.center - state.span / 2, state.center + state.span / 2, points)
        return stimulus

    def samples(
        self,
        channel: int,
        samples: tuple[complex, ...],
        stimulus: tuple[float, ...],
    ) -> tuple[complex, ...]:
        application = self.channel(channel).active
        if application is None:
            return samples
        scenario = self._trace(application, len(samples), advance=True)
        return scenario if scenario is not None else self._fallback(application, samples)

    def data(self, channel: int, application: str, result: str):
        if not self.enabled(channel, application):
            raise SCPICommandError(-221, "Settings conflict; measurement class is not active")
        measurement = self.measurements.selected(channel)
        points = len(measurement.stimulus)
        result_name = result.casefold()
        trace = self._trace(application, points, result_name, advance=True)
        if trace is None:
            base = tuple(measurement.samples)
            trace = self._result_fallback(application, result_name, base)
        return self.data_format.encode_values(value.real for value in trace)

    def marker(self, channel: int, application: str, number: int) -> AdvancedMarker:
        if not 1 <= number <= 10:
            raise SCPICommandError(-222, "Data out of range; marker number")
        return self.channel(channel).markers.setdefault((application, number), AdvancedMarker())

    def marker_search(self, channel: int, application: str, number: int) -> str:
        marker = self.marker(channel, application, number)
        axis, values = self._marker_values(channel, application)
        if values:
            index = max(range(len(values)), key=lambda item: values[item].real)
            marker.x = axis[index]
        return ""

    def marker_y(self, channel: int, application: str, number: int) -> str:
        marker = self.marker(channel, application, number)
        axis, values = self._marker_values(channel, application)
        if not values:
            return "0"
        index = min(range(len(axis)), key=lambda item: abs(axis[item] - marker.x))
        return f"{values[index].real:.12g}"

    def _marker_values(
        self, channel: int, application: str
    ) -> tuple[tuple[float, ...], tuple[complex, ...]]:
        measurement = self.measurements.selected(channel)
        points = len(measurement.stimulus)
        trace = self._trace(application, points, advance=False)
        if trace is None:
            trace = self._fallback(application, tuple(measurement.samples))
        return self.axis(channel, measurement.stimulus), trace

    def _trace(
        self,
        application: str,
        points: int,
        result: str = "trace",
        *,
        advance: bool,
    ) -> tuple[complex, ...] | None:
        if self.player is None:
            return None
        requested = (
            self.STREAMS[application]
            if result == "trace"
            else f"{self.PREFIXES[application]}.{result}"
        )
        names = {name.casefold(): name for name in self.player.stream_names}
        stream = names.get(requested.casefold())
        if stream is None:
            return None
        try:
            value = self.player.read(stream) if advance else self.player.peek(stream)
            if isinstance(value, (int, float, complex)):
                values = (complex(value),) * points
            else:
                values = tuple(complex(item) for item in value)
        except (ScenarioError, TypeError, ValueError) as exc:
            raise SCPICommandError(-230, f"Data corrupt or stale; stream {stream!r}") from exc
        if len(values) != points:
            raise SCPICommandError(
                -230,
                f"Data corrupt or stale; stream {stream!r} length {len(values)}, expected {points}",
            )
        return values

    @staticmethod
    def _fallback(application: str, samples: tuple[complex, ...]) -> tuple[complex, ...]:
        if application in {"spectrum", "phase_noise", "distortion", "imd"}:
            return tuple(complex(20 * math.log10(abs(value)) if value else -200.0) for value in samples)
        return samples

    @staticmethod
    def _result_fallback(
        application: str, result: str, samples: tuple[complex, ...]
    ) -> tuple[complex, ...]:
        magnitudes = tuple(20 * math.log10(abs(value)) if value else -200.0 for value in samples)
        if application == "distortion" and result == "evm":
            return tuple(complex(min(100.0, abs(value) * 0.1)) for value in magnitudes)
        if application == "imd" and result in {"im3", "im5", "im7", "im9"}:
            order = int(result[-1])
            return tuple(complex(value - 10 * (order - 1)) for value in magnitudes)
        if application in {"diq", "wideband_iq"} and result == "phase":
            return tuple(complex(math.degrees(math.atan2(value.imag, value.real))) for value in samples)
        return tuple(complex(value) for value in magnitudes)


def register_advanced_commands(registry: CommandRegistry, state: VNAAdvancedSystem) -> None:
    sense = HeaderNode("SENSe", index="channel", index_default=1)
    calc = HeaderNode("CALCulate", index="channel", index_default=1)
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    frequency = ParameterSpec(
        ParameterType.NUMBER,
        minimum=Decimal(0),
        units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
    )
    number = ParameterSpec(ParameterType.NUMBER)
    positive_integer = ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=100000)

    def exists(inv):
        channel = state.measurements.channels.get(inv.indices.get("channel", 1))
        return channel is not None and channel.selected in channel.measurements

    def licensed(*names):
        return lambda inv: bool(set(names) & inv.capabilities)

    licenses = {
        "spectrum": licensed("spectrum_analysis", "spectrum-analysis"),
        "imd": licensed("intermodulation_distortion", "intermodulation-distortion"),
        "distortion": licensed(
            "modulation_distortion", "modulation-distortion"
        ),
        "phase_noise": licensed("phase_noise", "phase-noise"),
        "diq": licensed("differential_iq", "differential-iq"),
        "wideband_iq": licensed("wideband_iq", "wideband-iq"),
    }

    def add(path, handler, *, query=False, parameters=(), available=None):
        registry.register(CommandSpec(
            tuple(path), handler, tuple(parameters), query=query,
            available=available, exists=exists,
        ))

    add((calc, HeaderNode("CUSTom"), HeaderNode("DEFine")),
        lambda inv, name, measurement_class, parameter: _custom_define(
            state, inv, name, measurement_class, parameter
        ),
        parameters=(ParameterSpec(ParameterType.STRING),) * 3)

    families = {
        "spectrum": (HeaderNode("SA"), HeaderNode("SA")),
        "imd": (HeaderNode("IMD"), HeaderNode("IMD")),
        "distortion": (HeaderNode("DISTortion"), HeaderNode("DISTortion")),
        "phase_noise": (HeaderNode("PN"), HeaderNode("PN")),
        "diq": (HeaderNode("DIQ"), HeaderNode("DIQ")),
        "wideband_iq": (HeaderNode("IQ"), HeaderNode("IQ")),
    }
    for application, (sense_node, calc_node) in families.items():
        root = (sense, sense_node)
        available = licenses[application]
        add((*root, HeaderNode("STATe")),
            lambda inv, value, app=application: state.enable(inv.indices["channel"], app, value),
            parameters=(boolean,), available=available)
        add((*root, HeaderNode("STATe")),
            lambda inv, app=application: _bool(state.enabled(inv.indices["channel"], app)),
            query=True, available=available)
        add((calc, calc_node, HeaderNode("DATA")),
            lambda inv, result, app=application: state.data(inv.indices["channel"], app, result),
            parameters=(ParameterSpec(ParameterType.CHARACTER),), query=True, available=available)
        add((*root, HeaderNode("CALibration"), HeaderNode("STATe")),
            lambda inv: "0", query=True, available=available)
        _register_markers(add, calc, calc_node, application, available, state)

    sa = (sense, HeaderNode("SA"))
    for path, attribute in (
        ((HeaderNode("BANDwidth"), HeaderNode("RESolution")), "resolution_bandwidth"),
        ((HeaderNode("BANDwidth"), HeaderNode("VIDeo")), "video_bandwidth"),
    ):
        _register_value(add, sa, path, attribute, frequency, licenses["spectrum"], state)
    _register_value(add, sa, (HeaderNode("AVERage"), HeaderNode("COUNt")),
                    "average_count", positive_integer, licenses["spectrum"], state)
    _register_value(add, sa, (HeaderNode("REFerence"), HeaderNode("LEVel")),
                    "reference_level", number, licenses["spectrum"], state)
    _register_value(add, sa, (HeaderNode("DETector"), HeaderNode("FUNCtion")), "detector",
                    ParameterSpec(ParameterType.ENUM,
                                  choices=("AVERage", "SAMPle", "PEAK", "NORMal", "NEGPeak")),
                    licenses["spectrum"], state)

    imd = (sense, HeaderNode("IMD"))
    _register_value(add, imd, (HeaderNode("SWEep"), HeaderNode("TYPE")), "sweep_type",
                    ParameterSpec(ParameterType.ENUM,
                                  choices=("FCENter", "DFRequency", "POWer", "CW", "SEGMent")),
                    licenses["imd"], state)
    for leaf, attribute in (("CENTer", "center"), ("SPAN", "span")):
        _register_value(add, imd,
                        (HeaderNode("FREQuency"), HeaderNode("FCENter"), HeaderNode(leaf)),
                        attribute, frequency, licenses["imd"], state)
    _register_value(add, imd, (HeaderNode("FREQuency"), HeaderNode("DFRequency")),
                    "delta_frequency", frequency, licenses["imd"], state)
    tone = HeaderNode("F", index="tone", index_default=1)
    add((*imd, HeaderNode("TPOWer"), tone),
        lambda inv, value: _set_imd_tone_power(state, inv, value),
        parameters=(number,), available=licenses["imd"])
    add((*imd, HeaderNode("TPOWer"), tone),
        lambda inv: str(_imd_tone_power(state, inv)),
        query=True, available=licenses["imd"])
    for tone in ("MAIN", "IMTone"):
        _register_value(add, imd, (HeaderNode("IFBWidth"), HeaderNode(tone)),
                        "imd_bandwidth", frequency, licenses["imd"], state)
    add((*imd, HeaderNode("HOPRoduct")), lambda inv: "9", query=True,
        available=licenses["imd"])

    distortion = (sense, HeaderNode("DISTortion"))
    _register_value(add, distortion, (HeaderNode("SWEep"), HeaderNode("TYPE")), "sweep_type",
                    ParameterSpec(ParameterType.ENUM, choices=("FIXed", "POWer")),
                    licenses["distortion"], state)
    _register_value(add, distortion,
                    (HeaderNode("SWEep"), HeaderNode("CARRier"), HeaderNode("FREQuency")),
                    "carrier_frequency", frequency, licenses["distortion"], state)
    _register_value(add, distortion,
                    (HeaderNode("SWEep"), HeaderNode("CARRier"), HeaderNode("LEVel")),
                    "carrier_power", number, licenses["distortion"], state)
    _register_value(add, distortion,
                    (HeaderNode("MEASure"), HeaderNode("FILTer"), HeaderNode("SRATe")),
                    "symbol_rate", frequency, licenses["distortion"], state)

    pn = (sense, HeaderNode("PN"))
    _register_value(add, pn, (HeaderNode("NTYPe"),), "noise_type",
                    ParameterSpec(ParameterType.ENUM, choices=("PNOise", "RESidual")),
                    licenses["phase_noise"], state)
    _register_value(add, pn,
                    (HeaderNode("SWEep"), HeaderNode("CARRier"), HeaderNode("FREQuency")),
                    "carrier_frequency", frequency, licenses["phase_noise"], state)
    for leaf, attribute in (("STARt", "offset_start"), ("STOP", "offset_stop")):
        _register_value(add, pn, (HeaderNode("OFFSet"), HeaderNode(leaf)), attribute,
                        frequency, licenses["phase_noise"], state)
    _register_value(add, pn, (HeaderNode("AVERage"), HeaderNode("COUNt")),
                    "average_count", positive_integer, licenses["phase_noise"], state)

    diq = (sense, HeaderNode("DIQ"))
    range_node = HeaderNode("RANGe", index="range", index_default=1)
    add((*diq, HeaderNode("FREQuency"), HeaderNode("RANGe"), HeaderNode("ADD")),
        lambda inv: _diq_add(state, inv), available=licenses["diq"])
    add((*diq, HeaderNode("FREQuency"), HeaderNode("RANGe"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).diq_ranges)),
        query=True, available=licenses["diq"])
    add((*diq, HeaderNode("FREQuency"), range_node, HeaderNode("DELete")),
        lambda inv: _diq_delete(state, inv), available=licenses["diq"])
    for leaf, offset in (("STARt", 0), ("STOP", 1), ("IFBW", 2)):
        add((*diq, HeaderNode("FREQuency"), range_node, HeaderNode(leaf)),
            lambda inv, value, item=offset: _diq_set(state, inv, item, value),
            parameters=(frequency,), available=licenses["diq"])
        add((*diq, HeaderNode("FREQuency"), range_node, HeaderNode(leaf)),
            lambda inv, item=offset: str(_diq_range(state, inv)[item]),
            query=True, available=licenses["diq"])

    iq = (sense, HeaderNode("IQ"))
    _register_value(add, iq, (HeaderNode("SRATe"),), "sample_rate", frequency,
                    licenses["wideband_iq"], state)
    _register_value(add, iq, (HeaderNode("CAPTure"), HeaderNode("TIME")), "capture_time",
                    ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0),
                                  units=frozenset({"S", "MS", "US", "NS"})),
                    licenses["wideband_iq"], state)


def _register_markers(add, calc, calc_node, application, available, state) -> None:
    marker = HeaderNode("MARKer", index="marker", index_default=1)
    root = (calc, calc_node, marker)
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    number = ParameterSpec(ParameterType.NUMBER)
    add((*root, HeaderNode("STATe")),
        lambda inv, value: _set(state.marker(inv.indices["channel"], application,
                                             inv.indices["marker"]), "enabled", value),
        parameters=(boolean,), available=available)
    add((*root, HeaderNode("STATe")),
        lambda inv: _bool(state.marker(inv.indices["channel"], application,
                                       inv.indices["marker"]).enabled),
        query=True, available=available)
    add((*root, HeaderNode("X")),
        lambda inv, value: _set(state.marker(inv.indices["channel"], application,
                                inv.indices["marker"]), "x", float(value.value)),
        parameters=(number,), available=available)
    add((*root, HeaderNode("X")),
        lambda inv: str(state.marker(inv.indices["channel"], application,
                                     inv.indices["marker"]).x),
        query=True, available=available)
    add((*root, HeaderNode("Y")),
        lambda inv: state.marker_y(inv.indices["channel"], application, inv.indices["marker"]),
        query=True, available=available)
    add((*root, HeaderNode("MAXimum")),
        lambda inv: state.marker_search(inv.indices["channel"], application,
                                        inv.indices["marker"]),
        available=available)


def _custom_define(state, invocation, name: str, measurement_class: str, parameter: str) -> str:
    normalized = " ".join(measurement_class.replace("/", " ").replace("-", " ").split()).casefold()
    classes = {
        "spectrum analyzer": "spectrum",
        "swept imd": "imd",
        "intermodulation distortion": "imd",
        "modulation distortion": "distortion",
        "modulation distortion converters": "distortion",
        "phase noise": "phase_noise",
        "differential i q": "diq",
        "wideband i q": "wideband_iq",
    }
    application = classes.get(normalized)
    if application is None:
        raise SCPICommandError(-224, "Illegal parameter value; measurement class")
    required = {
        "spectrum": {"spectrum_analysis", "spectrum-analysis"},
        "imd": {"intermodulation_distortion", "intermodulation-distortion"},
        "distortion": {
            "modulation_distortion", "modulation-distortion"
        },
        "phase_noise": {"phase_noise", "phase-noise"},
        "diq": {"differential_iq", "differential-iq"},
        "wideband_iq": {"wideband_iq", "wideband-iq"},
    }[application]
    if not required.intersection(invocation.capabilities):
        raise SCPICommandError(-113, "Command unavailable for configured options")
    channel = invocation.indices["channel"]
    measurement = state.measurements.define(channel, name, parameter)
    state.measurements.channel(channel).selected = measurement.name
    return state.enable(channel, application, True)


def _register_value(add, root, path, attribute, parameter, available, state) -> None:
    add((*root, *path),
        lambda inv, value: _set_channel_value(state, inv, attribute, value),
        parameters=(parameter,), available=available)
    add((*root, *path),
        lambda inv: str(getattr(state.channel(inv.indices["channel"]), attribute)),
        query=True, available=available)


def _set_channel_value(state, invocation, attribute: str, value) -> str:
    if isinstance(value, NumericValue):
        value = _scaled(value)
    return _set(state.channel(invocation.indices["channel"]), attribute, value)


def _diq_add(state, invocation) -> str:
    state.channel(invocation.indices["channel"]).diq_ranges.append((10.5e6, 26.5e9, 1e3))
    return ""


def _imd_tone_power(state, invocation) -> float:
    tone = invocation.indices.get("tone", 1)
    if tone not in (1, 2):
        raise SCPICommandError(-222, "Data out of range; IMD tone")
    target = state.channel(invocation.indices["channel"])
    return target.tone1_power if tone == 1 else target.tone2_power


def _set_imd_tone_power(state, invocation, value: NumericValue) -> str:
    tone = invocation.indices.get("tone", 1)
    _imd_tone_power(state, invocation)
    attribute = "tone1_power" if tone == 1 else "tone2_power"
    return _set(state.channel(invocation.indices["channel"]), attribute, float(value.value))


def _diq_range(state, invocation) -> tuple[float, float, float]:
    ranges = state.channel(invocation.indices["channel"]).diq_ranges
    number = invocation.indices.get("range", 1)
    if not 1 <= number <= len(ranges):
        raise SCPICommandError(-222, "Data out of range; DIQ frequency range")
    return ranges[number - 1]


def _diq_set(state, invocation, offset: int, value: NumericValue) -> str:
    ranges = state.channel(invocation.indices["channel"]).diq_ranges
    number = invocation.indices.get("range", 1)
    current = list(_diq_range(state, invocation))
    current[offset] = _scaled(value)
    if current[0] > current[1]:
        raise SCPICommandError(-222, "Data out of range; DIQ frequency range")
    ranges[number - 1] = tuple(current)
    return ""


def _diq_delete(state, invocation) -> str:
    ranges = state.channel(invocation.indices["channel"]).diq_ranges
    number = invocation.indices.get("range", 1)
    _diq_range(state, invocation)
    if len(ranges) == 1:
        raise SCPICommandError(-221, "Settings conflict; one DIQ range is required")
    ranges.pop(number - 1)
    return ""


def _set(target, name: str, value) -> str:
    setattr(target, name, value)
    return ""


def _scaled(value: NumericValue) -> float:
    scale = {
        None: 1.0, "HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9,
        "S": 1.0, "MS": 1e-3, "US": 1e-6, "NS": 1e-9,
    }
    return float(value.value) * scale[value.unit]


def _linear(start: float, stop: float, points: int) -> tuple[float, ...]:
    if points <= 1:
        return (start,) if points else ()
    step = (stop - start) / (points - 1)
    return tuple(start + index * step for index in range(points))


def _logspace(start: float, stop: float, points: int) -> tuple[float, ...]:
    if start <= 0 or stop <= 0:
        raise SCPICommandError(-222, "Data out of range; phase-noise offset")
    return tuple(10 ** value for value in _linear(math.log10(start), math.log10(stop), points))


def _bool(value: bool) -> str:
    return "1" if value else "0"
