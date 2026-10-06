"""Profile-gated VNA spectrum, distortion, phase-noise, and I/Q applications."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from .active_device import VNAActiveDeviceSystem
    from .mixer import VNAMixerSystem


CUSTOM_MEASUREMENT_CLASSES = {
    "spectrum analyzer": "spectrum",
    "swept imd": "imd",
    "intermodulation distortion": "imd",
    "modulation distortion": "distortion",
    "modulation distortion converters": "distortion",
    "phase noise": "phase_noise",
    "differential i q": "diq",
    "wideband i q": "wideband_iq",
    "gain compression": "gain_compression",
    "noise figure": "noise_figure",
    "scalar mixer": "scalar_converter",
    "scalar mixer converter": "scalar_converter",
    "vector mixer": "vector_converter",
    "vector mixer converter": "vector_converter",
    "frequency converter": "vector_converter",
    "converter": "vector_converter",
}

CUSTOM_MEASUREMENT_REQUIREMENTS = {
    "spectrum": {"spectrum_analysis", "spectrum-analysis"},
    "imd": {"intermodulation_distortion", "intermodulation-distortion"},
    "distortion": {"modulation_distortion", "modulation-distortion"},
    "phase_noise": {"phase_noise", "phase-noise"},
    "diq": {"differential_iq", "differential-iq"},
    "wideband_iq": {"wideband_iq", "wideband-iq"},
    "gain_compression": {"gain_compression", "gain-compression"},
    "noise_figure": {"noise_figure", "noise-figure"},
    "scalar_converter": {"scalar_mixer", "scalar-mixer"},
    "vector_converter": {"frequency_converter", "frequency-converter"},
}


@dataclass
class AdvancedMarker:
    enabled: bool = False
    x: float = 0.0


@dataclass
class DIQRange:
    start: float = 10.5e6
    stop: float = 26.5e9
    if_bandwidth: float = 1e3
    coupled: bool = False
    coupling_id: int = 1
    offset: float = 0.0
    upconvert: bool = True
    multiplier: int = 1
    divisor: int = 1


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
    imd_cw: float = 1e9
    imd_start: float = 10.5e6
    imd_stop: float = 26.5e9
    imd_f1: float = 1e9
    imd_f2: float = 1.01e9
    imd_normalized_mode: bool = False
    imd_input_port: int = 1
    imd_output_port: int = 2
    carrier_frequency: float = 1e9
    carrier_power: float = -20.0
    symbol_rate: float = 10e6
    distortion_filter_alpha: float = 0.35
    distortion_filter_auto: bool = True
    distortion_correlation_aperture: float = 10.0
    distortion_correlation_auto: bool = True
    distortion_modulation_source: str = ""
    distortion_input_port: int = 1
    distortion_output_port: int = 2
    distortion_nominal_gain: float = 0.0
    distortion_nominal_nf: float = 0.0
    distortion_power_start: float = -30.0
    distortion_power_stop: float = 0.0
    distortion_power_points: int = 11
    distortion_sparam_enabled: bool = False
    distortion_sparam_reuse: bool = False
    distortion_sparam_bandwidth: float = 1e3
    distortion_sparam_step: float = 1e6
    distortion_sparam_level: float = -30.0
    noise_type: str = "PNOise"
    offset_start: float = 10.0
    offset_stop: float = 10e6
    phase_noise_bandwidth_ratio: float = 10.0
    phase_noise_average_factor: int = 1
    phase_noise_receiver: str = "b2"
    sample_rate: float = 100e6
    capture_time: float = 10e-6
    diq_ranges: list[DIQRange] = field(default_factory=lambda: [DIQRange()])
    diq_parameters: dict[str, str] = field(default_factory=dict)
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

    def __init__(
        self,
        measurements: VNAMeasurementSystem,
        data_format: DataFormat,
        *,
        active_device: VNAActiveDeviceSystem | None = None,
        mixer: VNAMixerSystem | None = None,
    ) -> None:
        self.measurements = measurements
        self.data_format = data_format
        self.active_device = active_device
        self.mixer = mixer
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
        if state.active == "wideband_iq":
            return _linear(0.0, state.capture_time, points)
        if state.active == "diq":
            selected = state.diq_ranges[0]
            return _linear(selected.start, selected.stop, points)
        if state.active == "imd":
            if state.sweep_type == "FCENter":
                return _linear(state.center - state.span / 2, state.center + state.span / 2, points)
            return _linear(state.imd_start, state.imd_stop, points)
        if state.active == "distortion" and state.sweep_type == "POWer":
            return _linear(state.distortion_power_start, state.distortion_power_stop, points)
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
            return tuple(
                complex(20 * math.log10(abs(value)) if value else -200.0) for value in samples
            )
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
            return tuple(
                complex(math.degrees(math.atan2(value.imag, value.real))) for value in samples
            )
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

    def option_enabled(*names):
        return lambda inv: bool(set(names) & inv.capabilities)

    options = {
        "spectrum": option_enabled("spectrum_analysis", "spectrum-analysis"),
        "imd": option_enabled("intermodulation_distortion", "intermodulation-distortion"),
        "distortion": option_enabled("modulation_distortion", "modulation-distortion"),
        "phase_noise": option_enabled("phase_noise", "phase-noise"),
        "diq": option_enabled("differential_iq", "differential-iq"),
        "wideband_iq": option_enabled("wideband_iq", "wideband-iq"),
    }

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
        (calc, HeaderNode("CUSTom"), HeaderNode("DEFine")),
        lambda inv, name, measurement_class, parameter: _custom_define(
            state, inv, name, measurement_class, parameter
        ),
        parameters=(ParameterSpec(ParameterType.STRING),) * 3,
    )

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
        available = options[application]
        add(
            (*root, HeaderNode("STATe")),
            lambda inv, value, app=application: state.enable(inv.indices["channel"], app, value),
            parameters=(boolean,),
            available=available,
        )
        add(
            (*root, HeaderNode("STATe")),
            lambda inv, app=application: _bool(state.enabled(inv.indices["channel"], app)),
            query=True,
            available=available,
        )
        add(
            (calc, calc_node, HeaderNode("DATA")),
            lambda inv, result, app=application: state.data(inv.indices["channel"], app, result),
            parameters=(ParameterSpec(ParameterType.CHARACTER),),
            query=True,
            available=available,
        )
        add(
            (*root, HeaderNode("CALibration"), HeaderNode("STATe")),
            lambda inv: "0",
            query=True,
            available=available,
        )
        _register_markers(add, calc, calc_node, application, available, state)

    sa = (sense, HeaderNode("SA"))
    for path, attribute in (
        ((HeaderNode("BANDwidth"), HeaderNode("RESolution")), "resolution_bandwidth"),
        ((HeaderNode("BANDwidth"), HeaderNode("VIDeo")), "video_bandwidth"),
    ):
        _register_value(add, sa, path, attribute, frequency, options["spectrum"], state)
    _register_value(
        add,
        sa,
        (HeaderNode("AVERage"), HeaderNode("COUNt")),
        "average_count",
        positive_integer,
        options["spectrum"],
        state,
    )
    _register_value(
        add,
        sa,
        (HeaderNode("REFerence"), HeaderNode("LEVel")),
        "reference_level",
        number,
        options["spectrum"],
        state,
    )
    _register_value(
        add,
        sa,
        (HeaderNode("DETector"), HeaderNode("FUNCtion")),
        "detector",
        ParameterSpec(
            ParameterType.ENUM, choices=("AVERage", "SAMPle", "PEAK", "NORMal", "NEGPeak")
        ),
        options["spectrum"],
        state,
    )

    imd = (sense, HeaderNode("IMD"))
    _register_value(
        add,
        imd,
        (HeaderNode("SWEep"), HeaderNode("TYPE")),
        "sweep_type",
        ParameterSpec(
            ParameterType.ENUM, choices=("FCENter", "DFRequency", "POWer", "CW", "SEGMent")
        ),
        options["imd"],
        state,
    )
    for leaf, attribute in (("CENTer", "center"), ("SPAN", "span")):
        _register_value(
            add,
            imd,
            (HeaderNode("FREQuency"), HeaderNode("FCENter"), HeaderNode(leaf)),
            attribute,
            frequency,
            options["imd"],
            state,
        )
    _register_value(
        add,
        imd,
        (HeaderNode("FREQuency"), HeaderNode("DFRequency")),
        "delta_frequency",
        frequency,
        options["imd"],
        state,
    )
    tone = HeaderNode("F", index="tone", index_default=1)
    add(
        (*imd, HeaderNode("TPOWer"), tone),
        lambda inv, value: _set_imd_tone_power(state, inv, value),
        parameters=(number,),
        available=options["imd"],
    )
    add(
        (*imd, HeaderNode("TPOWer"), tone),
        lambda inv: str(_imd_tone_power(state, inv)),
        query=True,
        available=options["imd"],
    )
    for tone in ("MAIN", "IMTone"):
        _register_value(
            add,
            imd,
            (HeaderNode("IFBWidth"), HeaderNode(tone)),
            "imd_bandwidth",
            frequency,
            options["imd"],
            state,
        )
    add((*imd, HeaderNode("HOPRoduct")), lambda inv: "9", query=True, available=options["imd"])
    for path, attribute in (
        ((HeaderNode("FREQuency"), HeaderNode("CW")), "imd_cw"),
        ((HeaderNode("FREQuency"), HeaderNode("STARt")), "imd_start"),
        ((HeaderNode("FREQuency"), HeaderNode("STOP")), "imd_stop"),
    ):
        _register_value(add, imd, path, attribute, frequency, options["imd"], state)
    imd_frequency = HeaderNode("F", index="imd_frequency", index_default=1)
    add(
        (*imd, HeaderNode("FREQuency"), imd_frequency),
        lambda inv, value: _set_imd_frequency(state, inv, value),
        parameters=(frequency,),
        available=options["imd"],
    )
    add(
        (*imd, HeaderNode("FREQuency"), imd_frequency),
        lambda inv: str(_imd_frequency(state, inv)),
        query=True,
        available=options["imd"],
    )
    _register_value(
        add,
        imd,
        (HeaderNode("NORMalized"), HeaderNode("MODE")),
        "imd_normalized_mode",
        boolean,
        options["imd"],
        state,
    )
    for leaf, attribute in (("INPut", "imd_input_port"), ("OUTPut", "imd_output_port")):
        _register_value(
            add,
            imd,
            (HeaderNode("PMAP"), HeaderNode(leaf)),
            attribute,
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=999),
            options["imd"],
            state,
        )

    distortion = (sense, HeaderNode("DISTortion"))
    _register_value(
        add,
        distortion,
        (HeaderNode("SWEep"), HeaderNode("TYPE")),
        "sweep_type",
        ParameterSpec(ParameterType.ENUM, choices=("FIXed", "POWer")),
        options["distortion"],
        state,
    )
    for path, attribute, parameter in (
        (
            (HeaderNode("MEASure"), HeaderNode("FILTer"), HeaderNode("ALPHa")),
            "distortion_filter_alpha",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(1)),
        ),
        (
            (
                HeaderNode("MEASure"),
                HeaderNode("FILTer"),
                HeaderNode("SRATe"),
                HeaderNode("AUTO"),
            ),
            "distortion_filter_auto",
            boolean,
        ),
        (
            (HeaderNode("MEASure"), HeaderNode("CORRelation"), HeaderNode("APERture")),
            "distortion_correlation_aperture",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(100)),
        ),
        (
            (
                HeaderNode("MEASure"),
                HeaderNode("CORRelation"),
                HeaderNode("APERture"),
                HeaderNode("AUTO"),
            ),
            "distortion_correlation_auto",
            boolean,
        ),
        (
            (HeaderNode("MODulate"), HeaderNode("SOURce")),
            "distortion_modulation_source",
            ParameterSpec(ParameterType.STRING),
        ),
        (
            (HeaderNode("PATH"), HeaderNode("DUT"), HeaderNode("PMAP"), HeaderNode("INPut")),
            "distortion_input_port",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=999),
        ),
        (
            (HeaderNode("PATH"), HeaderNode("DUT"), HeaderNode("PMAP"), HeaderNode("OUTPut")),
            "distortion_output_port",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=999),
        ),
        (
            (HeaderNode("PATH"), HeaderNode("DUT"), HeaderNode("NOMinal"), HeaderNode("GAIN")),
            "distortion_nominal_gain",
            number,
        ),
        (
            (HeaderNode("PATH"), HeaderNode("DUT"), HeaderNode("NOMinal"), HeaderNode("NF")),
            "distortion_nominal_nf",
            number,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("POWer"), HeaderNode("STARt")),
            "distortion_power_start",
            number,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("POWer"), HeaderNode("STOP")),
            "distortion_power_stop",
            number,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("POWer"), HeaderNode("POINts")),
            "distortion_power_points",
            positive_integer,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("SPARam")),
            "distortion_sparam_enabled",
            boolean,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("SPARam"), HeaderNode("REUSe")),
            "distortion_sparam_reuse",
            boolean,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("SPARam"), HeaderNode("BWIDth")),
            "distortion_sparam_bandwidth",
            frequency,
        ),
        (
            (HeaderNode("SWEep"), HeaderNode("SPARam"), HeaderNode("STEP")),
            "distortion_sparam_step",
            frequency,
        ),
        (
            (
                HeaderNode("SWEep"),
                HeaderNode("POWer"),
                HeaderNode("SPARam"),
                HeaderNode("LEVel"),
            ),
            "distortion_sparam_level",
            number,
        ),
    ):
        _register_value(add, distortion, path, attribute, parameter, options["distortion"], state)
    _register_value(
        add,
        distortion,
        (HeaderNode("SWEep"), HeaderNode("CARRier"), HeaderNode("FREQuency")),
        "carrier_frequency",
        frequency,
        options["distortion"],
        state,
    )
    _register_value(
        add,
        distortion,
        (HeaderNode("SWEep"), HeaderNode("CARRier"), HeaderNode("LEVel")),
        "carrier_power",
        number,
        options["distortion"],
        state,
    )
    _register_value(
        add,
        distortion,
        (HeaderNode("MEASure"), HeaderNode("FILTer"), HeaderNode("SRATe")),
        "symbol_rate",
        frequency,
        options["distortion"],
        state,
    )

    pn = (sense, HeaderNode("PN"))
    _register_value(
        add,
        pn,
        (HeaderNode("NTYPe"),),
        "noise_type",
        ParameterSpec(ParameterType.ENUM, choices=("PNOise", "RESidual")),
        options["phase_noise"],
        state,
    )
    for path, attribute, parameter in (
        (
            (HeaderNode("BWIDth"), HeaderNode("RATio")),
            "phase_noise_bandwidth_ratio",
            ParameterSpec(ParameterType.NUMBER, minimum=Decimal(0), maximum=Decimal(100)),
        ),
        (
            (HeaderNode("FAVerage"), HeaderNode("FACTor")),
            "phase_noise_average_factor",
            ParameterSpec(ParameterType.INTEGER, minimum=1, maximum=10000),
        ),
        (
            (HeaderNode("RECeiver"),),
            "phase_noise_receiver",
            ParameterSpec(ParameterType.STRING),
        ),
    ):
        _register_value(add, pn, path, attribute, parameter, options["phase_noise"], state)
    _register_value(
        add,
        pn,
        (HeaderNode("SWEep"), HeaderNode("CARRier"), HeaderNode("FREQuency")),
        "carrier_frequency",
        frequency,
        options["phase_noise"],
        state,
    )
    for leaf, attribute in (("STARt", "offset_start"), ("STOP", "offset_stop")):
        _register_value(
            add,
            pn,
            (HeaderNode("OFFSet"), HeaderNode(leaf)),
            attribute,
            frequency,
            options["phase_noise"],
            state,
        )
    _register_value(
        add,
        pn,
        (HeaderNode("AVERage"), HeaderNode("COUNt")),
        "average_count",
        positive_integer,
        options["phase_noise"],
        state,
    )

    diq = (sense, HeaderNode("DIQ"))
    range_node = HeaderNode("RANGe", index="range", index_default=1)
    add(
        (*diq, HeaderNode("FREQuency"), HeaderNode("RANGe"), HeaderNode("ADD")),
        lambda inv: _diq_add(state, inv),
        available=options["diq"],
    )
    add(
        (*diq, HeaderNode("FREQuency"), HeaderNode("RANGe"), HeaderNode("COUNt")),
        lambda inv: str(len(state.channel(inv.indices["channel"]).diq_ranges)),
        query=True,
        available=options["diq"],
    )
    add(
        (*diq, HeaderNode("FREQuency"), range_node, HeaderNode("DELete")),
        lambda inv: _diq_delete(state, inv),
        available=options["diq"],
    )
    for leaf, offset in (("STARt", 0), ("STOP", 1), ("IFBW", 2)):
        add(
            (*diq, HeaderNode("FREQuency"), range_node, HeaderNode(leaf)),
            lambda inv, value, item=offset: _diq_set(state, inv, item, value),
            parameters=(frequency,),
            available=options["diq"],
        )
        add(
            (*diq, HeaderNode("FREQuency"), range_node, HeaderNode(leaf)),
            lambda inv, item=offset: str(_diq_range_value(state, inv, item)),
            query=True,
            available=options["diq"],
        )
    for path, attribute, parameter in (
        ((HeaderNode("COUPle"), HeaderNode("STATe")), "coupled", boolean),
        (
            (HeaderNode("COUPle"), HeaderNode("ID")),
            "coupling_id",
            ParameterSpec(ParameterType.INTEGER, minimum=1),
        ),
        ((HeaderNode("COUPle"), HeaderNode("OFFSet")), "offset", frequency),
        ((HeaderNode("COUPle"), HeaderNode("UCONvert")), "upconvert", boolean),
        (
            (HeaderNode("COUPle"), HeaderNode("MULTiplier")),
            "multiplier",
            ParameterSpec(ParameterType.INTEGER),
        ),
        (
            (HeaderNode("COUPle"), HeaderNode("DIVisor")),
            "divisor",
            ParameterSpec(ParameterType.INTEGER),
        ),
    ):
        add(
            (*diq, HeaderNode("FREQuency"), range_node, *path),
            lambda inv, value, name=attribute: _diq_set_attribute(state, inv, name, value),
            parameters=(parameter,),
            available=options["diq"],
        )
        add(
            (*diq, HeaderNode("FREQuency"), range_node, *path),
            lambda inv, name=attribute: _format_value(getattr(_diq_range(state, inv), name)),
            query=True,
            available=options["diq"],
        )
    add(
        (*diq, HeaderNode("PARameter"), HeaderNode("DEFine")),
        lambda inv, name, expression: _diq_define_parameter(state, inv, name, expression),
        parameters=(ParameterSpec(ParameterType.STRING), ParameterSpec(ParameterType.STRING)),
        available=options["diq"],
    )
    add(
        (*diq, HeaderNode("PARameter"), HeaderNode("DELete")),
        lambda inv, name: _diq_delete_parameter(state, inv, name),
        parameters=(ParameterSpec(ParameterType.STRING),),
        available=options["diq"],
    )
    add(
        (*diq, HeaderNode("PARameter"), HeaderNode("CATalog")),
        lambda inv: _diq_parameter_catalog(state, inv),
        query=True,
        available=options["diq"],
    )

    iq = (sense, HeaderNode("IQ"))
    _register_value(
        add, iq, (HeaderNode("SRATe"),), "sample_rate", frequency, options["wideband_iq"], state
    )
    _register_value(
        add,
        iq,
        (HeaderNode("CAPTure"), HeaderNode("TIME")),
        "capture_time",
        ParameterSpec(
            ParameterType.NUMBER, minimum=Decimal(0), units=frozenset({"S", "MS", "US", "NS"})
        ),
        options["wideband_iq"],
        state,
    )


def _register_markers(add, calc, calc_node, application, available, state) -> None:
    marker = HeaderNode("MARKer", index="marker", index_default=1)
    root = (calc, calc_node, marker)
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    number = ParameterSpec(ParameterType.NUMBER)
    add(
        (*root, HeaderNode("STATe")),
        lambda inv, value: _set(
            state.marker(inv.indices["channel"], application, inv.indices["marker"]),
            "enabled",
            value,
        ),
        parameters=(boolean,),
        available=available,
    )
    add(
        (*root, HeaderNode("STATe")),
        lambda inv: _bool(
            state.marker(inv.indices["channel"], application, inv.indices["marker"]).enabled
        ),
        query=True,
        available=available,
    )
    add(
        (*root, HeaderNode("X")),
        lambda inv, value: _set(
            state.marker(inv.indices["channel"], application, inv.indices["marker"]),
            "x",
            float(value.value),
        ),
        parameters=(number,),
        available=available,
    )
    add(
        (*root, HeaderNode("X")),
        lambda inv: str(state.marker(inv.indices["channel"], application, inv.indices["marker"]).x),
        query=True,
        available=available,
    )
    add(
        (*root, HeaderNode("Y")),
        lambda inv: state.marker_y(inv.indices["channel"], application, inv.indices["marker"]),
        query=True,
        available=available,
    )
    add(
        (*root, HeaderNode("MAXimum")),
        lambda inv: state.marker_search(inv.indices["channel"], application, inv.indices["marker"]),
        available=available,
    )


def _custom_define(
    state: VNAAdvancedSystem,
    invocation,
    name: str,
    measurement_class: str,
    parameter: str,
) -> str:
    normalized = " ".join(measurement_class.replace("/", " ").replace("-", " ").split()).casefold()
    application = CUSTOM_MEASUREMENT_CLASSES.get(normalized)
    if application is None:
        raise SCPICommandError(-224, "Illegal parameter value; measurement class")
    required = CUSTOM_MEASUREMENT_REQUIREMENTS[application]
    if not required.intersection(invocation.capabilities):
        raise SCPICommandError(-113, "Command unavailable for configured options")
    if application in {"gain_compression", "noise_figure"} and state.active_device is None:
        raise SCPICommandError(-113, "Command unavailable for configured options")
    if application in {"scalar_converter", "vector_converter"} and state.mixer is None:
        raise SCPICommandError(-113, "Command unavailable for configured options")
    channel = invocation.indices["channel"]
    measurement = state.measurements.define(channel, name, parameter)
    state.measurements.channel(channel).selected = measurement.name
    _deactivate_custom_measurement_engines(state, channel)
    if application == "gain_compression":
        state.active_device.gain(channel).enabled = True
        return ""
    if application == "noise_figure":
        state.active_device.noise(channel).enabled = True
        return ""
    if application in {"scalar_converter", "vector_converter"}:
        mixer = state.mixer.channel(channel)
        mixer.converter_type = "SCALar" if application == "scalar_converter" else "VECTor"
        mixer.mixer_enabled = True
        return ""
    return state.enable(channel, application, True)


def _deactivate_custom_measurement_engines(state: VNAAdvancedSystem, channel: int) -> None:
    state.channel(channel).active = None
    if state.active_device is not None:
        state.active_device.gain(channel).enabled = False
        state.active_device.noise(channel).enabled = False
    if state.mixer is not None:
        state.mixer.channel(channel).mixer_enabled = False


def _register_value(add, root, path, attribute, parameter, available, state) -> None:
    add(
        (*root, *path),
        lambda inv, value: _set_channel_value(state, inv, attribute, value),
        parameters=(parameter,),
        available=available,
    )
    add(
        (*root, *path),
        lambda inv: _format_value(getattr(state.channel(inv.indices["channel"]), attribute)),
        query=True,
        available=available,
    )


def _set_channel_value(state, invocation, attribute: str, value) -> str:
    if isinstance(value, NumericValue):
        value = _scaled(value)
    return _set(state.channel(invocation.indices["channel"]), attribute, value)


def _diq_add(state, invocation) -> str:
    state.channel(invocation.indices["channel"]).diq_ranges.append(DIQRange())
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


def _imd_frequency(state, invocation) -> float:
    tone = invocation.indices.get("imd_frequency", 1)
    if tone not in (1, 2):
        raise SCPICommandError(-222, "Data out of range; IMD tone frequency")
    target = state.channel(invocation.indices["channel"])
    return target.imd_f1 if tone == 1 else target.imd_f2


def _set_imd_frequency(state, invocation, value: NumericValue) -> str:
    tone = invocation.indices.get("imd_frequency", 1)
    _imd_frequency(state, invocation)
    attribute = "imd_f1" if tone == 1 else "imd_f2"
    return _set(state.channel(invocation.indices["channel"]), attribute, _scaled(value))


def _diq_range(state, invocation) -> DIQRange:
    ranges = state.channel(invocation.indices["channel"]).diq_ranges
    number = invocation.indices.get("range", 1)
    if not 1 <= number <= len(ranges):
        raise SCPICommandError(-222, "Data out of range; DIQ frequency range")
    return ranges[number - 1]


def _diq_set(state, invocation, offset: int, value: NumericValue) -> str:
    current = _diq_range(state, invocation)
    attributes = ("start", "stop", "if_bandwidth")
    updated = _scaled(value)
    start = updated if offset == 0 else current.start
    stop = updated if offset == 1 else current.stop
    if start > stop:
        raise SCPICommandError(-222, "Data out of range; DIQ frequency range")
    setattr(current, attributes[offset], updated)
    return ""


def _diq_range_value(state, invocation, offset: int) -> float:
    return getattr(_diq_range(state, invocation), ("start", "stop", "if_bandwidth")[offset])


def _diq_set_attribute(state, invocation, attribute: str, value) -> str:
    selected = _diq_range(state, invocation)
    if isinstance(value, NumericValue):
        value = _scaled(value)
    if attribute == "divisor" and value == 0:
        raise SCPICommandError(-222, "Data out of range; DIQ coupling divisor")
    if attribute == "coupling_id":
        ranges = state.channel(invocation.indices["channel"]).diq_ranges
        if value > len(ranges):
            raise SCPICommandError(-222, "Data out of range; DIQ coupling range")
    setattr(selected, attribute, value)
    return ""


def _diq_define_parameter(state, invocation, name: str, expression: str) -> str:
    if not name.strip() or "_" in name or not expression.strip():
        raise SCPICommandError(-224, "Illegal parameter value; DIQ parameter")
    state.channel(invocation.indices["channel"]).diq_parameters[name.strip()] = expression.strip()
    return ""


def _diq_delete_parameter(state, invocation, name: str) -> str:
    parameters = state.channel(invocation.indices["channel"]).diq_parameters
    if name not in parameters:
        raise SCPICommandError(-224, "Illegal parameter value; DIQ parameter")
    del parameters[name]
    return ""


def _diq_parameter_catalog(state, invocation) -> str:
    parameters = state.channel(invocation.indices["channel"]).diq_parameters
    return ",".join(f'"{name}:{expression}"' for name, expression in sorted(parameters.items()))


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


def _format_value(value) -> str:
    if isinstance(value, bool):
        return _bool(value)
    return str(value)


def _scaled(value: NumericValue) -> float:
    scale = {
        None: 1.0,
        "HZ": 1.0,
        "KHZ": 1e3,
        "MHZ": 1e6,
        "GHZ": 1e9,
        "S": 1.0,
        "MS": 1e-3,
        "US": 1e-6,
        "NS": 1e-9,
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
    return tuple(10**value for value in _linear(math.log10(start), math.log10(stop), points))


def _bool(value: bool) -> str:
    return "1" if value else "0"
