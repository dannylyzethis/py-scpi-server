from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scenario import (
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import VNACapabilities


def licensed_vna() -> SCPIInstrument:
    capabilities = VNACapabilities.create(
        "vna-2-port", applications=("time_domain", "fixture_removal")
    )
    instrument = SCPIInstrument(
        "Virtual VNA 2 Port", "time-domain", vna_capabilities=capabilities
    )
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
    instrument = licensed_vna()
    frequency_data = instrument.process_command("CALC:DATA? SDAT")
    frequency_axis = instrument.process_command("CALC:MEAS:DATA:X?")

    assert instrument.process_command("CALC:TRAN:TIME:TYPE IMP") == ""
    assert instrument.process_command("CALC:TRAN:TIME:WIND MIN") == ""
    assert instrument.process_command("CALC:TRAN:TIME:STAT ON") == ""
    assert instrument.process_command("CALC:TRAN:TIME:TYPE?") == "IMPulse"
    assert instrument.process_command("CALC:TRAN:TIME:WIND?") == "MINimum"
    assert instrument.process_command("CALC:TRAN:TIME:STAT?") == "1"
    assert instrument.process_command("CALC:DATA? SDAT") != frequency_data
    time_axis = tuple(float(value) for value in instrument.process_command(
        "CALC:MEAS:DATA:X?"
    ).split(","))
    assert time_axis[0] == 0
    assert time_axis[-1] > 0
    assert ",".join(str(value) for value in time_axis) != frequency_axis


def test_time_gate_alters_same_scenario_trace_and_round_trips() -> None:
    instrument = licensed_vna()
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
    instrument = licensed_vna()
    original = instrument.process_command("CALC:DATA? SDAT")

    instrument.process_command(
        'CALC:FSIM:SEND:DEEM:PORT1:USER:FIL "fixture-port-1.s2p"'
    )
    instrument.process_command("CALC:FSIM:SEND:DEEM:PORT1:STAT ON")
    instrument.process_command("CALC:FSIM:BAL:TOP BBAL")
    instrument.process_command("CALC:FSIM:STAT ON")
    assert instrument.process_command(
        "CALC:FSIM:SEND:DEEM:PORT1:USER:FIL?"
    ) == "fixture-port-1.s2p"
    assert instrument.process_command("CALC:FSIM:SEND:DEEM:PORT1:STAT?") == "1"
    assert instrument.process_command("CALC:FSIM:BAL:TOP?") == "BBALanced"
    assert instrument.process_command("CALC:FSIM:STAT?") == "1"
    assert instrument.process_command("CALC:DATA? SDAT") != original

    instrument.process_command(
        'CALC:FSIM:SEND:EMB:PORT2:USER:FIL "embedding-port-2.s2p"'
    )
    instrument.process_command("CALC:FSIM:SEND:EMB:PORT2:STAT ON")
    instrument.process_command("CALC:FSIM:BAL:TOP MIX")
    assert instrument.process_command(
        "CALC:FSIM:SEND:EMB:PORT2:USER:FIL?"
    ) == "embedding-port-2.s2p"
    assert instrument.process_command("CALC:FSIM:SEND:EMB:PORT2:STAT?") == "1"
    assert instrument.process_command("CALC:FSIM:BAL:TOP?") == "MIXed"


def test_application_state_survives_cls_and_resets_with_rst() -> None:
    instrument = licensed_vna()
    instrument.process_command("CALC:TRAN:TIME:STAT ON")
    instrument.process_command("CALC:FSIM:STAT ON")
    instrument.process_command("*CLS")
    assert instrument.process_command("CALC:TRAN:TIME:STAT?") == "1"
    assert instrument.process_command("CALC:FSIM:STAT?") == "1"

    instrument.process_command("*RST")
    assert instrument.process_command("CALC:TRAN:TIME:STAT?") == "0"
    assert instrument.process_command("CALC:FSIM:STAT?") == "0"


def test_unlicensed_and_nonexistent_application_commands_are_rejected() -> None:
    strict = SCPIInstrument(
        "Virtual VNA 2 Port",
        "strict",
        vna_capabilities=VNACapabilities.create("vna-2-port", applications=()),
    )
    assert strict.process_command("CALC:TRAN:TIME:STAT?") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    licensed = licensed_vna()
    assert licensed.process_command("CALC2:TRAN:TIME:STAT?") == ""
    assert licensed.process_command("SYST:ERR?").startswith(
        '-200,"Execution error; addressed object does not exist'
    )


def test_transform_variants_windows_and_frequency_domain_notch_are_deterministic() -> None:
    instrument = licensed_vna()
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
    instrument = licensed_vna()
    instrument.process_command("CALC:FILT:TIME:STAR 1S")
    assert instrument.process_command("CALC:FILT:TIME:STOP 0S") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')

    assert instrument.process_command(
        'CALC:FSIM:SEND:DEEM:PORT1:USER:FIL ""'
    ) == ""
    assert instrument.process_command("SYST:ERR?").startswith('-224,"Illegal parameter value')
    assert instrument.process_command("CALC:FSIM:SEND:DEEM:PORT3:STAT ON") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')
