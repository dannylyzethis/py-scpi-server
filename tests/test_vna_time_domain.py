import pytest

from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scenario import (
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import VNACapabilities


def option_enabled_vna() -> SCPIInstrument:
    capabilities = VNACapabilities.create(
        "vna-2-port", applications=("time_domain", "fixture_removal")
    )
    instrument = SCPIInstrument("Virtual VNA 2 Port", "time-domain", vna_capabilities=capabilities)
    instrument.process_command("SENS:SWE:POIN 4")
    stream = ScenarioStream(
        "S11",
        StreamKind.TRACE,
        (ScenarioSample((1 + 0j, 2 + 1j, 3 - 1j, 4 + 0.5j)),),
        end=EndPolicy.HOLD_LAST,
    )
    instrument.attach_scenario(ScenarioDefinition("dut", (stream,)))
    instrument.process_command("FORM:DATA ASC")
    return instrument


def test_time_transform_round_trips_and_changes_data_and_axis() -> None:
    instrument = option_enabled_vna()
    frequency_data = instrument.process_command("CALC:DATA? SDAT")
    frequency_axis = instrument.process_command("CALC:MEAS:DATA:X?")

    assert instrument.process_command("CALC:TRAN:TIME:TYPE IMP") == ""
    assert instrument.process_command("CALC:TRAN:TIME:WIND MIN") == ""
    assert instrument.process_command("CALC:TRAN:TIME:STAT ON") == ""
    assert instrument.process_command("CALC:TRAN:TIME:TYPE?") == "IMPulse"
    assert instrument.process_command("CALC:TRAN:TIME:WIND?") == "MINimum"
    assert instrument.process_command("CALC:TRAN:TIME:STAT?") == "1"
    assert instrument.process_command("CALC:DATA? SDAT") != frequency_data
    time_axis = tuple(
        float(value) for value in instrument.process_command("CALC:MEAS:DATA:X?").split(",")
    )
    assert time_axis[0] == 0
    assert time_axis[-1] > 0
    assert ",".join(str(value) for value in time_axis) != frequency_axis


def test_time_gate_alters_same_scenario_trace_and_round_trips() -> None:
    instrument = option_enabled_vna()
    instrument.process_command("CALC:TRAN:TIME:WIND MIN")
    instrument.process_command("CALC:TRAN:TIME:STAT ON")
    ungated = instrument.process_command("CALC:DATA? SDAT")

    instrument.process_command("CALC:FILT:TIME:STAR 0")
    instrument.process_command("CALC:FILT:TIME:STOP 0")
    instrument.process_command("CALC:FILT:TIME:TYPE BAND")
    instrument.process_command("CALC:FILT:TIME:STAT ON")
    assert instrument.process_command("CALC:FILT:TIME:STAR?") == "0.0"
    assert instrument.process_command("CALC:FILT:TIME:STOP?") == "0.0"
    assert instrument.process_command("CALC:FILT:TIME:TYPE?") == "BANDpass"
    assert instrument.process_command("CALC:FILT:TIME:STAT?") == "1"
    assert instrument.process_command("CALC:DATA? SDAT") != ungated


def test_fixture_file_port_and_balanced_topology_change_results() -> None:
    instrument = option_enabled_vna()
    original = instrument.process_command("CALC:DATA? SDAT")

    instrument.process_command('CALC:FSIM:SEND:DEEM:PORT1:USER:FIL "fixture-port-1.s2p"')
    instrument.process_command("CALC:FSIM:SEND:DEEM:PORT1:STAT ON")
    instrument.process_command("CALC:FSIM:BAL:TOP BBAL")
    instrument.process_command("CALC:FSIM:STAT ON")
    assert instrument.process_command("CALC:FSIM:SEND:DEEM:PORT1:USER:FIL?") == "fixture-port-1.s2p"
    assert instrument.process_command("CALC:FSIM:SEND:DEEM:PORT1:STAT?") == "1"
    assert instrument.process_command("CALC:FSIM:BAL:TOP?") == "BBALanced"
    assert instrument.process_command("CALC:FSIM:STAT?") == "1"
    assert instrument.process_command("CALC:DATA? SDAT") != original

    instrument.process_command('CALC:FSIM:SEND:EMB:PORT2:USER:FIL "embedding-port-2.s2p"')
    instrument.process_command("CALC:FSIM:SEND:EMB:PORT2:STAT ON")
    instrument.process_command("CALC:FSIM:BAL:TOP MIX")
    assert (
        instrument.process_command("CALC:FSIM:SEND:EMB:PORT2:USER:FIL?") == "embedding-port-2.s2p"
    )
    assert instrument.process_command("CALC:FSIM:SEND:EMB:PORT2:STAT?") == "1"
    assert instrument.process_command("CALC:FSIM:BAL:TOP?") == "MIXed"


def test_application_state_survives_cls_and_resets_with_rst() -> None:
    instrument = option_enabled_vna()
    instrument.process_command("CALC:TRAN:TIME:STAT ON")
    instrument.process_command("CALC:FSIM:STAT ON")
    instrument.process_command("*CLS")
    assert instrument.process_command("CALC:TRAN:TIME:STAT?") == "1"
    assert instrument.process_command("CALC:FSIM:STAT?") == "1"

    instrument.process_command("*RST")
    assert instrument.process_command("CALC:TRAN:TIME:STAT?") == "0"
    assert instrument.process_command("CALC:FSIM:STAT?") == "0"


def test_option_disabled_and_nonexistent_application_commands_are_rejected() -> None:
    strict = SCPIInstrument(
        "Virtual VNA 2 Port",
        "strict",
        vna_capabilities=VNACapabilities.create("vna-2-port", applications=()),
    )
    assert strict.process_command("CALC:TRAN:TIME:STAT?") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    option_enabled = option_enabled_vna()
    assert option_enabled.process_command("CALC2:TRAN:TIME:STAT?") == ""
    assert option_enabled.process_command("SYST:ERR?").startswith(
        '-200,"Execution error; addressed object does not exist'
    )


def test_transform_variants_windows_and_frequency_domain_notch_are_deterministic() -> None:
    instrument = option_enabled_vna()
    baseline = instrument.process_command("CALC:DATA? SDAT")

    instrument.process_command("CALC:TRAN:TIME:STAT ON")
    instrument.process_command("CALC:TRAN:TIME:TYPE LOWP")
    low_pass = instrument.process_command("CALC:DATA? SDAT")
    instrument.process_command("CALC:TRAN:TIME:TYPE STEP")
    step = instrument.process_command("CALC:DATA? SDAT")
    instrument.process_command("CALC:TRAN:TIME:WIND MAX")
    maximum_window = instrument.process_command("CALC:DATA? SDAT")
    assert len({baseline, low_pass, step, maximum_window}) == 4

    instrument.process_command("CALC:TRAN:TIME:STAT OFF")
    instrument.process_command("CALC:FILT:TIME:STAR 0")
    instrument.process_command("CALC:FILT:TIME:STOP 0")
    instrument.process_command("CALC:FILT:TIME:TYPE NOTC")
    instrument.process_command("CALC:FILT:TIME:STAT ON")
    assert instrument.process_command("CALC:DATA? SDAT") != baseline


def test_invalid_gate_and_fixture_inputs_report_scpi_errors() -> None:
    instrument = option_enabled_vna()
    instrument.process_command("CALC:FILT:TIME:STAR 1S")
    assert instrument.process_command("CALC:FILT:TIME:STOP 0S") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')

    assert instrument.process_command('CALC:FSIM:SEND:DEEM:PORT1:USER:FIL ""') == ""
    assert instrument.process_command("SYST:ERR?").startswith('-224,"Illegal parameter value')
    assert instrument.process_command("CALC:FSIM:SEND:DEEM:PORT3:STAT ON") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')


def test_transform_range_stimulus_and_marker_controls_round_trip() -> None:
    instrument = option_enabled_vna()

    instrument.process_command("CALC:TRAN:TIME:CENT 5NS")
    instrument.process_command("CALC:TRAN:TIME:SPAN 2NS")
    instrument.process_command("CALC:TRAN:TIME:STIM STEP")
    instrument.process_command("CALC:TRAN:TIME:CLIP OFF")
    instrument.process_command("CALC:TRAN:TIME:MARK:MODE TRAN")
    instrument.process_command("CALC:TRAN:TIME:MARK:UNIT FEET")
    instrument.process_command("CALC:TRAN:TIME:STAT ON")

    assert float(instrument.process_command("CALC:TRAN:TIME:STAR?")) == pytest.approx(4e-9)
    assert float(instrument.process_command("CALC:TRAN:TIME:STOP?")) == pytest.approx(6e-9)
    assert float(instrument.process_command("CALC:TRAN:TIME:CENT?")) == pytest.approx(5e-9)
    assert float(instrument.process_command("CALC:TRAN:TIME:SPAN?")) == pytest.approx(2e-9)
    assert instrument.process_command("CALC:TRAN:TIME:STIM?") == "STEP"
    assert instrument.process_command("CALC:TRAN:TIME:TYPE?") == "LPSTep"
    assert instrument.process_command("CALC:TRAN:TIME?") == "LPSTep"
    assert instrument.process_command("CALC:TRAN:TIME:CLIP?") == "0"
    assert instrument.process_command("CALC:TRAN:TIME:MARK:MODE?") == "TRANsmission"
    assert instrument.process_command("CALC:TRAN:TIME:MARK:UNIT?") == "FEET"
    axis = tuple(
        float(value) for value in instrument.process_command("CALC:MEAS:DATA:X?").split(",")
    )
    assert axis[0] == pytest.approx(4e-9)
    assert axis[-1] == pytest.approx(6e-9)


def test_enhanced_windows_and_parameters_are_deterministic() -> None:
    instrument = option_enabled_vna()
    instrument.process_command("CALC:TRAN:TIME:STAT ON")
    instrument.process_command("CALC:TRAN:TIME:WIND KAIS")
    instrument.process_command("CALC:TRAN:TIME:KBES 4")
    beta_four = instrument.process_command("CALC:DATA? SDAT")
    instrument.process_command("CALC:TRAN:TIME:KBES 10")
    beta_ten = instrument.process_command("CALC:DATA? SDAT")
    instrument.process_command("CALC:TRAN:TIME:IMP:WIDT 1E-9S")
    instrument.process_command("CALC:TRAN:TIME:STEP:RTIM 2E-9S")

    assert beta_four != beta_ten
    assert instrument.process_command("CALC:TRAN:TIME:WIND?") == "KAISer"
    assert instrument.process_command("CALC:TRAN:TIME:KBES?") == "10.0"
    assert instrument.process_command("CALC:TRAN:TIME:IMP:WIDT?") == "1e-09"
    assert instrument.process_command("CALC:TRAN:TIME:STEP:RTIM?") == "2e-09"


def test_explicit_gate_path_center_span_and_shape_aliases_round_trip() -> None:
    instrument = option_enabled_vna()

    instrument.process_command("CALC:FILT:GATE:TIME:CENT 4NS")
    instrument.process_command("CALC:FILT:GATE:TIME:SPAN 2NS")
    instrument.process_command("CALC:FILT:GATE:TIME:SHAP WIDE")
    instrument.process_command("CALC:FILT:GATE:TIME BPAS")
    instrument.process_command("CALC:FILT:GATE:TIME:STAT ON")

    assert float(instrument.process_command("CALC:FILT:GATE:TIME:STAR?")) == pytest.approx(3e-9)
    assert float(instrument.process_command("CALC:FILT:GATE:TIME:STOP?")) == pytest.approx(5e-9)
    assert float(instrument.process_command("CALC:FILT:GATE:TIME:CENT?")) == pytest.approx(4e-9)
    assert float(instrument.process_command("CALC:FILT:GATE:TIME:SPAN?")) == pytest.approx(2e-9)
    assert instrument.process_command("CALC:FILT:GATE:TIME:SHAP?") == "WIDE"
    assert instrument.process_command("CALC:FILT:GATE:TIME:TYPE?") == "BPASs"
    assert instrument.process_command("CALC:FILT:GATE:TIME?") == "BPASs"
    assert instrument.process_command("CALC:FILT:GATE:TIME:STAT?") == "1"


def test_negative_transform_or_gate_span_reports_range_error() -> None:
    instrument = option_enabled_vna()

    assert instrument.process_command("CALC:TRAN:TIME:SPAN -1S") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')
    assert instrument.process_command("CALC:FILT:GATE:TIME:SPAN -1S") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')
