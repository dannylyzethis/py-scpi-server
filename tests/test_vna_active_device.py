from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scenario import (
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import VNACapabilities


def active_device_vna(*streams: ScenarioStream) -> SCPIInstrument:
    capabilities = VNACapabilities.create("vna-4-port")
    instrument = SCPIInstrument(
        "Virtual VNA 4 Port", "active-device", vna_capabilities=capabilities
    )
    instrument.process_command("SENS:SWE:POIN 4")
    base = ScenarioStream(
        "S11",
        StreamKind.TRACE,
        (ScenarioSample((1 + 0j, 2 + 0j, 3 + 0j, 4 + 0j)),),
        end=EndPolicy.HOLD_LAST,
    )
    instrument.attach_scenario(ScenarioDefinition("active-dut", (base, *streams)))
    instrument.process_command("FORM:DATA ASC")
    return instrument


def trace(name: str, *values, advance=AdvancePolicy.READ) -> ScenarioStream:
    return ScenarioStream(
        name,
        StreamKind.TRACE,
        tuple(ScenarioSample(value) for value in values),
        advance=advance,
        end=EndPolicy.HOLD_LAST,
    )


def numbers(response: str) -> tuple[float, ...]:
    return tuple(float(value) for value in response.split(","))


def test_gain_compression_configuration_and_scenario_results() -> None:
    instrument = active_device_vna(
        trace("gain_compression.gain", (12, 12, 11.5, 10.5, 9)),
        trace("gain_compression.output_power", (-8, -3, 1.5, 5.5, 9)),
    )
    for command in (
        "SENS:GC:POW:STAR -20",
        "SENS:GC:POW:STOP 0",
        "SENS:GC:SWE:POIN 5",
        "SENS:GC:COMP:POW -10",
        "SENS:GC:COMP:DB 1",
        "SENS:GC:COMP:REF EXT",
        "SENS:GC:COMP:STAT ON",
        "SENS:GC:STAT ON",
    ):
        assert instrument.process_command(command) == ""

    assert instrument.process_command("SENS:GC:COMP:REF?") == "EXTernal"
    assert instrument.process_command("SENS:GC:SWE:POIN?") == "5"
    assert numbers(instrument.process_command("CALC:GC:DATA? GAIN")) == (12, 12, 11.5, 10.5, 9)
    assert numbers(instrument.process_command("CALC:GC:DATA? IPOW")) == (-20, -15, -10, -5, 0)
    assert instrument.process_command("CALC:GC:RES:PIN?") == "-5"
    assert instrument.process_command("CALC:GC:RES:GAIN?") == "10.5"
    assert instrument.process_command("CALC:GC:STAT?") == "1"
    assert len(instrument.process_command("CALC:DATA? SDAT").split(",")) == 10


def test_standard_gcsetup_tree_round_trips_and_drives_shared_results() -> None:
    instrument = active_device_vna(trace("gain_compression.gain", (12, 12, 11.5, 10.5, 9)))
    commands = (
        "SENS:GCS:AMOD PFREQ",
        "SENS:GCS:COMP:ALG CFMG",
        "SENS:GCS:COMP:BACK:LEV 8",
        "SENS:GCS:COMP:DELT:X 2",
        "SENS:GCS:COMP:DELT:Y 3",
        "SENS:GCS:COMP:INT ON",
        "SENS:GCS:COMP:LEV 1",
        "SENS:GCS:COMP:PHAS:LEV 4",
        "SENS:GCS:COMP:PHAS:MODE BOTH",
        "SENS:GCS:COMP:SAT:LEV .2",
        "SENS:GCS:EOS POFF",
        "SENS:GCS:MIX:REF ON",
        "SENS:GCS:PMAP 2,3",
        "SENS:GCS:PMAP:SOUR:OVER ON",
        "SENS:GCS:POW:LIN:INP:COMP:APER 10",
        "SENS:GCS:POW:LIN:INP:LEV -20",
        "SENS:GCS:POW:REV:LEV -10",
        "SENS:GCS:POW:STAR:LEV -20",
        "SENS:GCS:POW:STOP:LEV 0",
        "SENS:GCS:SAFE:CPAD 2",
        'SENS:GCS:SAFE:DC:PAR "supply"',
        "SENS:GCS:SAFE:ENAB ON",
        "SENS:GCS:SAFE:FPAD .5",
        "SENS:GCS:SAFE:FTHR .25",
        "SENS:GCS:SAFE:MLIM 20",
        "SENS:GCS:SMAR:CDC ON",
        "SENS:GCS:SMAR:MIT 25",
        "SENS:GCS:SMAR:SIT ON",
        "SENS:GCS:SMAR:STIM .1",
        "SENS:GCS:SMAR:TOL .1",
        "SENS:GCS:SWE:FREQ:POIN 101",
        "SENS:GCS:SWE:POW:POIN 5",
        "SENS:GCS:SWE:POW:SMO ON",
        "SENS:GCS:SWE:POW:SMO:APER 20",
    )
    for command in commands:
        assert instrument.process_command(command) == "", command

    assert instrument.process_command("SENS:GCS:COMP:LEV?") == "1"
    assert instrument.process_command("SENS:GCS:PMAP:INP?") == "2"
    assert instrument.process_command("SENS:GCS:PMAP:OUTP?") == "3"
    assert instrument.process_command("SENS:GCS:SAFE:DC:PAR?") == "supply"
    assert instrument.process_command("SENS:GCS:SWE:POW:POIN?") == "5"
    assert numbers(instrument.process_command("CALC:GC:DATA? IPOW")) == (-20, -15, -10, -5, 0)
    assert instrument.process_command("CALC:GC:RES:GAIN?") == "11"


def test_gcsetup_result_algorithms_change_result_selection() -> None:
    values = (10, 12, 11.5, 10.5, 9)

    first_gain = active_device_vna(trace("gain_compression.gain", values))
    first_gain.process_command("SENS:GCS:SWE:POW:POIN 5")
    first_gain.process_command("SENS:GCS:COMP:ALG CFLG")
    first_gain.process_command("SENS:GCS:COMP:LEV 1")
    assert first_gain.process_command("CALC:GC:RES:PIN?") == "0"

    maximum_gain = active_device_vna(trace("gain_compression.gain", values))
    maximum_gain.process_command("SENS:GCS:SWE:POW:POIN 5")
    maximum_gain.process_command("SENS:GCS:COMP:ALG CFMG")
    maximum_gain.process_command("SENS:GCS:COMP:LEV 1")
    assert maximum_gain.process_command("CALC:GC:RES:PIN?") == "-7.5"

    backoff = active_device_vna(trace("gain_compression.gain", values))
    backoff.process_command("SENS:GCS:SWE:POW:POIN 5")
    backoff.process_command("SENS:GCS:COMP:ALG BACK")
    backoff.process_command("SENS:GCS:COMP:BACK:LEV 8")
    assert backoff.process_command("CALC:GC:RES:PIN?") == "-7.5"


def test_gcsetup_validation_gating_reset_and_nonstandard_hierarchy() -> None:
    instrument = active_device_vna()
    assert instrument.process_command("SENS:GCS:PMAP 1,1") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-224,"Illegal parameter value')
    assert instrument.process_command("SENS:GCS:COMP:LEV 0") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')
    assert instrument.process_command("SENS:GAIN:GCS:COMP:LEV 1") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-113,"Undefined header')

    instrument.process_command("SENS:GCS:COMP:LEV 3")
    instrument.process_command("*CLS")
    assert instrument.process_command("SENS:GCS:COMP:LEV?") == "3"
    instrument.process_command("*RST")
    assert instrument.process_command("SENS:GCS:COMP:LEV?") == "1"


def test_noise_figure_configuration_and_scenario_results() -> None:
    instrument = active_device_vna(
        trace("noise_figure.nf", (2.1, 2.2, 2.3, 2.4)),
        trace("noise_figure.gain", (15, 14, 13, 12)),
        trace("noise_figure.yfactor", (1.6, 1.7, 1.8, 1.9)),
    )
    for command in (
        "SENS:NOIS:STAT ON",
        "SENS:NOIS:POW -25",
        "SENS:NOIS:BAND 10MHz",
        "SENS:NOIS:AVER:COUN 8",
        "SENS:NOIS:TEMP 300",
    ):
        assert instrument.process_command(command) == ""

    assert instrument.process_command("SENS:NOIS:BAND?") == "10000000.0"
    assert numbers(instrument.process_command("CALC:NOIS:DATA? NF")) == (2.1, 2.2, 2.3, 2.4)
    assert numbers(instrument.process_command("CALC:NOIS:DATA? GAIN")) == (15, 14, 13, 12)
    assert numbers(instrument.process_command("CALC:NOIS:DATA? YFAC")) == (1.6, 1.7, 1.8, 1.9)
    assert instrument.process_command("CALC:NOIS:RES:NF?") == "2.25"
    assert instrument.process_command("SENS:NOIS:CAL:STAT?") == "0"


def test_active_device_state_survives_cls_and_resets_with_rst() -> None:
    instrument = active_device_vna()
    instrument.process_command("SENS:GC:STAT ON")
    instrument.process_command("SENS:NOIS:STAT ON")

    instrument.process_command("*CLS")
    assert instrument.process_command("SENS:GC:STAT?") == "1"
    assert instrument.process_command("SENS:NOIS:STAT?") == "1"

    instrument.process_command("*RST")
    assert instrument.process_command("SENS:GC:STAT?") == "0"
    assert instrument.process_command("SENS:NOIS:STAT?") == "0"
    assert instrument.process_command("SENS:GC:CAL:STAT?") == "0"


def test_application_commands_enforce_option_and_address_existence() -> None:
    strict = SCPIInstrument(
        "Virtual VNA 2 Port",
        "strict",
        vna_capabilities=VNACapabilities.create("vna-2-port", applications=()),
    )
    assert strict.process_command("SENS:GC:STAT?") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    option_enabled = active_device_vna()
    assert option_enabled.process_command("SENS2:GC:STAT?") == ""
    assert option_enabled.process_command("SYST:ERR?").startswith('-200,"Execution error')


def test_trigger_policy_advances_shared_gain_result_stream() -> None:
    instrument = active_device_vna(
        trace(
            "gain_compression.gain",
            (12, 12, 12),
            (10, 10, 10),
            advance=AdvancePolicy.TRIGGER,
        )
    )
    instrument.process_command("SENS:GC:SWE:POIN 3")
    assert numbers(instrument.process_command("CALC:GC:DATA? GAIN")) == (12, 12, 12)
    instrument.process_command("INIT:IMM")
    assert numbers(instrument.process_command("CALC:GC:DATA? GAIN")) == (10, 10, 10)


def test_bad_active_device_trace_length_reports_data_error() -> None:
    instrument = active_device_vna(trace("noise_figure.nf", (1, 2)))
    assert instrument.process_command("CALC:NOIS:DATA? NF") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-230,"Data corrupt or stale')
