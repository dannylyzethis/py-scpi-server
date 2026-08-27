from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scenario import (
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import VNACapabilities


def mixer_vna(*, scalar_only=False) -> SCPIInstrument:
    options = (
        ("time_domain", "frequency_offset", "scalar_mixer")
        if scalar_only
        else (
            "time_domain",
            "frequency_offset",
            "scalar_mixer",
            "frequency_converter",
            "embedded_lo",
        )
    )
    capabilities = VNACapabilities.create("vna-2-port", applications=options)
    instrument = SCPIInstrument("Virtual VNA 2 Port", "mixer", vna_capabilities=capabilities)
    instrument.process_command("SENS:SWE:POIN 4")
    stream = ScenarioStream(
        "S11",
        StreamKind.TRACE,
        (ScenarioSample((1 + 0j, 2 + 0.5j, 3 - 0.5j, 4 + 1j)),),
        end=EndPolicy.HOLD_LAST,
    )
    instrument.attach_scenario(ScenarioDefinition("converter-dut", (stream,)))
    instrument.process_command("FORM:DATA ASC")
    return instrument


def test_frequency_offset_ranges_produce_coherent_axis() -> None:
    instrument = mixer_vna()
    instrument.process_command("SENS:FOM:RANG1:FREQ:STAR 1GHz")
    instrument.process_command("SENS:FOM:RANG1:FREQ:STOP 2GHz")
    instrument.process_command("SENS:FOM:RANG1:ROLE OUTP")
    instrument.process_command("SENS:FOM:STAT ON")

    assert instrument.process_command("SENS:FOM:STAT?") == "1"
    assert instrument.process_command("SENS:FOM:RANG1:ROLE?") == "OUTPut"
    assert instrument.process_command("CALC:MEAS:DATA:X?") == (
        "1000000000.0,1333333333.3333333,1666666666.6666665,2000000000.0"
    )
    assert len(instrument.process_command("CALC:DATA? SDAT").split(",")) == 8

    instrument.process_command("SENS:FOM:RANG2:ADD")
    assert instrument.process_command("SENS:FOM:RANG:COUN?") == "2"
    instrument.process_command("SENS:FOM:RANG2:DEL")
    assert instrument.process_command("SENS:FOM:RANG:COUN?") == "1"


def test_vector_converter_translates_axis_and_complex_data() -> None:
    instrument = mixer_vna()
    baseline = instrument.process_command("CALC:DATA? SDAT")
    instrument.process_command("SENS:MIX:FREQ:FIX 2GHz")
    instrument.process_command("SENS:MIX:FREQ:LO 1.5GHz")
    instrument.process_command("SENS:MIX:MODE DOWN")
    instrument.process_command("SENS:MIX:CONV:TYPE VECT")
    instrument.process_command("SENS:MIX:STAT ON")
    instrument.process_command("SENS:MIX:RECALC")

    assert instrument.process_command("SENS:MIX:FREQ:FIX?") == "2000000000.0"
    assert instrument.process_command("SENS:MIX:FREQ:LO?") == "1500000000.0"
    assert instrument.process_command("SENS:MIX:FREQ:IF?") == "500000000.0"
    assert instrument.process_command("SENS:MIX:MODE?") == "DOWNconverter"
    assert instrument.process_command("SENS:MIX:CONV:TYPE?") == "VECTor"
    assert instrument.process_command("CALC:DATA? SDAT") != baseline
    assert len(instrument.process_command("CALC:MEAS:DATA:X?").split(",")) == 4


def test_mixer_segments_resample_data_to_segment_axis() -> None:
    instrument = mixer_vna()
    instrument.process_command("SENS:MIX:SEGM1:ADD")
    instrument.process_command("SENS:MIX:SEGM1:FREQ:STAR 1GHz")
    instrument.process_command("SENS:MIX:SEGM1:FREQ:STOP 2GHz")
    instrument.process_command("SENS:MIX:SEGM1:POW -10")
    instrument.process_command("SENS:MIX:SEGM1:SWE:POIN 3")
    instrument.process_command("SENS:MIX:SEGM1:CALC")

    assert instrument.process_command("SENS:MIX:SEGM:COUN?") == "1"
    assert instrument.process_command("SENS:MIX:SEGM1:POW?") == "-10.0"
    assert instrument.process_command("SENS:MIX:SEGM1:SWE:POIN?") == "3"
    assert len(instrument.process_command("CALC:MEAS:DATA:X?").split(",")) == 3
    assert len(instrument.process_command("CALC:DATA? SDAT").split(",")) == 6


def test_source_roles_embedded_lo_and_application_composition() -> None:
    instrument = mixer_vna()
    instrument.process_command("SENS:MIX:SOUR1:ROLE LO")
    instrument.process_command("SENS:MIX:ELO:CENT 1GHz")
    instrument.process_command("SENS:MIX:ELO:SPAN 100MHz")
    instrument.process_command("SENS:MIX:ELO:STAT ON")
    instrument.process_command("SENS:MIX:STAT ON")
    instrument.process_command("CALC:TRAN:TIME:STAT ON")

    assert instrument.process_command("SENS:MIX:SOUR1:ROLE?") == "LO"
    assert instrument.process_command("SENS:MIX:ELO:STAT?") == "1"
    assert instrument.process_command("SENS:MIX:ELO:CENT?") == "1000000000.0"
    assert instrument.process_command("SENS:MIX:ELO:SPAN?") == "100000000.0"
    assert len(instrument.process_command("CALC:DATA? SDAT").split(",")) == 8
    axis = tuple(
        float(value) for value in instrument.process_command("CALC:MEAS:DATA:X?").split(",")
    )
    assert axis[0] == 0.0


def test_correction_status_is_static_zero_and_reset_semantics_are_preserved() -> None:
    instrument = mixer_vna()
    assert instrument.process_command("SENS:MIX:CAL:STAT?") == "0"
    assert instrument.process_command("SENS:FOM:CORR:STAT?") == "0"
    instrument.process_command("SENS:MIX:STAT ON")
    instrument.process_command("SENS:FOM:STAT ON")
    instrument.process_command("*CLS")
    assert instrument.process_command("SENS:MIX:STAT?") == "1"
    assert instrument.process_command("SENS:FOM:STAT?") == "1"
    instrument.process_command("*RST")
    assert instrument.process_command("SENS:MIX:STAT?") == "0"
    assert instrument.process_command("SENS:FOM:STAT?") == "0"


def test_licenses_ranges_and_sources_report_correct_errors() -> None:
    strict = SCPIInstrument(
        "Virtual VNA 2 Port",
        "strict",
        vna_capabilities=VNACapabilities.create("vna-2-port", applications=()),
    )
    assert strict.process_command("SENS:MIX:STAT?") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    scalar = mixer_vna(scalar_only=True)
    assert scalar.process_command("SENS:MIX:CONV:TYPE VECT") == ""
    assert scalar.process_command("SYST:ERR?").startswith('-224,"Illegal parameter value')
    assert scalar.process_command("SENS:MIX:SOUR2:ROLE LO") == ""
    assert scalar.process_command("SYST:ERR?").startswith('-222,"Data out of range')
    assert scalar.process_command("SENS:MIX:SEGM2:FREQ:STAR?") == ""
    assert scalar.process_command("SYST:ERR?").startswith(
        '-200,"Execution error; addressed object does not exist'
    )
