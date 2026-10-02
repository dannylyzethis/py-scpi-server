"""Coverage contract for every advertised VNA application capability."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationContract:
    kind: str
    command_key: str


VNA_APPLICATION_CONTRACTS = {
    "active_hot_parameters": ApplicationContract("measurement", "SENSE<channel>:AHP:STATE"),
    "arbitrary_waveform_generation": ApplicationContract(
        "stimulus", "SENSE<channel>:AWGENERATION:STATE"
    ),
    "basic_pulsed_rf": ApplicationContract("stimulus", "SENSE<channel>:PULSE<pulse>:STATE"),
    "differential_iq": ApplicationContract("measurement", "SENSE<channel>:DIQ:STATE"),
    "embedded_lo": ApplicationContract("modifier", "SENSE<channel>:MIXER:ELO:STATE"),
    "enhanced_time_domain": ApplicationContract("modifier", "CALCULATE<channel>:FILTER:TIME:STATE"),
    "fast_cw": ApplicationContract("stimulus", "SENSE<channel>:FCW:STATE"),
    "fixture_removal": ApplicationContract("modifier", "CALCULATE<channel>:FSIMULATOR:STATE"),
    "frequency_converter": ApplicationContract(
        "measurement", "SENSE<channel>:MIXER:CONVERTER:TYPE"
    ),
    "frequency_offset": ApplicationContract("modifier", "SENSE<channel>:FOM:STATE"),
    "gain_compression": ApplicationContract("measurement", "SENSE<channel>:GCOMPRESSION:STATE"),
    "integrated_pulsed_rf": ApplicationContract("stimulus", "SENSE<channel>:SWEEP:PULSE:WIDEBAND"),
    "intermodulation_distortion": ApplicationContract("measurement", "SENSE<channel>:IMD:STATE"),
    "measurement_uncertainty": ApplicationContract("analysis", "SENSE<channel>:UNCERTAINTY:STATE"),
    "modulation_distortion": ApplicationContract("measurement", "SENSE<channel>:DISTORTION:STATE"),
    "noise_figure": ApplicationContract("measurement", "SENSE<channel>:NOISE:STATE"),
    "n_port": ApplicationContract("measurement", "SENSE<channel>:NPORT:STATE"),
    "performance_test": ApplicationContract("analysis", "SENSE<channel>:PERFORMANCE:STATE"),
    "phase_noise": ApplicationContract("measurement", "SENSE<channel>:PN:STATE"),
    "scalar_mixer": ApplicationContract("measurement", "SENSE<channel>:MIXER:CONVERTER:TYPE"),
    "source_phase_control": ApplicationContract("stimulus", "SOURCE<source>:PHASE:STATE"),
    "spectrum_analysis": ApplicationContract("measurement", "SENSE<channel>:SA:STATE"),
    "time_domain": ApplicationContract("modifier", "CALCULATE<channel>:TRANSFORM:TIME:STATE"),
    "true_mode_stimulus": ApplicationContract("stimulus", "SENSE<channel>:TMSTIMULUS:STATE"),
    "wideband_iq": ApplicationContract("measurement", "SENSE<channel>:IQ:STATE"),
}
