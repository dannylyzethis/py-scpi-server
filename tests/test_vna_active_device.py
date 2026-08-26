from scpi_emulator.emulator import SCPIInstrument
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
    assert numbers(instrument.process_command("CALC:GC:DATA? GAIN")) == (
        12, 12, 11.5, 10.5, 9
    )
    assert numbers(instrument.process_command("CALC:GC:DATA? IPOW")) == (
        -20, -15, -10, -5, 0
    )
    assert instrument.process_command("CALC:GC:RES:PIN?") == "-5"
    assert instrument.process_command("CALC:GC:RES:GAIN?") == "10.5"
    assert instrument.process_command("CALC:GC:STAT?") == "1"
    assert len(instrument.process_command("CALC:DATA? SDAT").split(",")) == 10


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
    assert numbers(instrument.process_command("CALC:NOIS:DATA? NF")) == (
        2.1, 2.2, 2.3, 2.4
    )
    assert numbers(instrument.process_command("CALC:NOIS:DATA? GAIN")) == (
        15, 14, 13, 12
    )
    assert numbers(instrument.process_command("CALC:NOIS:DATA? YFAC")) == (
        1.6, 1.7, 1.8, 1.9
    )
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


def test_application_commands_enforce_license_and_address_existence() -> None:
    strict = SCPIInstrument(
        "Virtual VNA 2 Port",
        "strict",
        vna_capabilities=VNACapabilities.create("vna-2-port", applications=()),
    )
    assert strict.process_command("SENS:GC:STAT?") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    licensed = active_device_vna()
    assert licensed.process_command("SENS2:GC:STAT?") == ""
    assert licensed.process_command("SYST:ERR?").startswith('-200,"Execution error')


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
    assert instrument.process_command("SYST:ERR?").startswith(
        '-230,"Data corrupt or stale'
    )
