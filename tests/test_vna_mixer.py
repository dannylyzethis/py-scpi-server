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


def test_options_ranges_and_sources_report_correct_errors() -> None:
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


def test_standard_frequency_offset_tree_round_trips_and_transforms_axis() -> None:
    instrument = mixer_vna()
    baseline = tuple(
        float(value) for value in instrument.process_command("CALC:MEAS:DATA:X?").split(",")
    )
    instrument.process_command("SENS:FOM:RANG1:NAME 'output'")
    instrument.process_command("SENS:FOM:RANG1:COUP ON")
    instrument.process_command("SENS:FOM:RANG1:FREQ:MULT 2")
    instrument.process_command("SENS:FOM:RANG1:FREQ:DIV 1")
    instrument.process_command("SENS:FOM:RANG1:FREQ:OFFS 100MHz")
    instrument.process_command("SENS:FOM ON")

    assert instrument.process_command("SENS:FOM?") == "1"
    assert instrument.process_command("SENS:FOM:CAT?") == "output"
    assert instrument.process_command("SENS:FOM:COUN?") == "1"
    assert instrument.process_command("SENS:FOM:RNUM? 'OUTPUT'") == "1"
    assert instrument.process_command("SENS:FOM:RANG1:COUP?") == "1"
    assert instrument.process_command("SENS:FOM:RANG1:FREQ:MULT?") == "2"
    axis = tuple(
        float(value) for value in instrument.process_command("CALC:MEAS:DATA:X?").split(",")
    )
    assert axis[0] == baseline[0] * 2 + 100e6
    assert axis[-1] == baseline[-1] * 2 + 100e6


def test_frequency_offset_segments_drive_axis_and_round_trip_controls() -> None:
    instrument = mixer_vna()
    instrument.process_command("SENS:FOM:RANG1:SEGM1:ADD")
    instrument.process_command("SENS:FOM:RANG1:SEGM1:FREQ:STOP 2GHz")
    instrument.process_command("SENS:FOM:RANG1:SEGM1:FREQ:STAR 1GHz")
    instrument.process_command("SENS:FOM:RANG1:SEGM1:SWE:POIN 3")
    instrument.process_command("SENS:FOM:RANG1:SEGM1:POW2 -7")
    instrument.process_command("SENS:FOM:RANG1:SEGM:BWID:RES:CONT ON")
    instrument.process_command("SENS:FOM:RANG1:SEGM1 ON")
    instrument.process_command("SENS:FOM:RANG1:SWE:TYPE SEGM")
    instrument.process_command("SENS:FOM:DISP:SEL 1")
    instrument.process_command("SENS:FOM ON")

    assert instrument.process_command("SENS:FOM:RANG1:SEGM:COUN?") == "1"
    assert instrument.process_command("SENS:FOM:RANG1:SEGM1?") == "1"
    assert instrument.process_command("SENS:FOM:RANG1:SEGM1:POW2?") == "-7"
    assert instrument.process_command("SENS:FOM:RANG1:SEGM:BWID:RES:CONT?") == "1"
    assert instrument.process_command("SENS:FOM:DISP:SEL?") == "1"
    assert instrument.process_command("CALC:MEAS:DATA:X?") == (
        "1000000000.0,1500000000.0,2000000000.0"
    )


def test_standard_mixer_setup_applies_ports_stages_and_selected_axis() -> None:
    instrument = mixer_vna()
    instrument.process_command("SENS:MIX:AVO ON")
    instrument.process_command("SENS:MIX:PMAP 1,2")
    instrument.process_command("SENS:MIX:STAG 2")
    instrument.process_command("SENS:MIX:LO2:FREQ:FIX 2.25GHz")
    instrument.process_command("SENS:MIX:LO2:FREQ:ILTI OFF")
    instrument.process_command("SENS:MIX:LO2:NAME 'internal-2'")
    instrument.process_command("SENS:MIX:LO2:POW -3")
    instrument.process_command("SENS:MIX:INP:POW -12")
    instrument.process_command("SENS:MIX:OUTP:FREQ:STOP 2.5GHz")
    instrument.process_command("SENS:MIX:OUTP:FREQ:STAR 1.5GHz")
    instrument.process_command("SENS:MIX:OUTP:FREQ:MODE SWEPT")
    instrument.process_command("SENS:MIX:XAX OUTP")
    instrument.process_command("SENS:MIX:PHAS ON")
    instrument.process_command("SENS:MIX:PHAS:ABS ON")
    instrument.process_command("SENS:MIX:APPL")

    assert instrument.process_command("SENS:MIX:AVO?") == "1"
    assert instrument.process_command("SENS:MIX:PMAP:INP?") == "1"
    assert instrument.process_command("SENS:MIX:PMAP:OUTP?") == "2"
    assert instrument.process_command("SENS:MIX:LO2:FREQ:FIX?") == "2250000000"
    assert instrument.process_command("SENS:MIX:LO2:FREQ:ILTI?") == "0"
    assert instrument.process_command("SENS:MIX:LO2:NAME?") == "internal-2"
    assert instrument.process_command("SENS:MIX:LO2:POW?") == "-3"
    assert instrument.process_command("SENS:MIX:INP:POW?") == "-12"
    assert instrument.process_command("SENS:MIX:STAT?") == "1"
    assert instrument.process_command("SENS:MIX:XAX?") == "OUTPut"
    axis = tuple(
        float(value) for value in instrument.process_command("CALC:MEAS:DATA:X?").split(",")
    )
    assert axis == (1.5e9, 1.8333333333333333e9, 2.1666666666666665e9, 2.5e9)

    assert instrument.process_command("SENS:MIX:PMAP 1,1") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-224,"Illegal parameter value')


def test_standard_embedded_lo_settings_reset_and_affect_data() -> None:
    instrument = mixer_vna()
    instrument.process_command("SENS:MIX:STAT ON")
    baseline = instrument.process_command("CALC:DATA? SDAT")
    instrument.process_command("SENS:MIX:ELO:LO:DELTA 2MHz")
    instrument.process_command("SENS:MIX:ELO:NORM:POIN 2")
    instrument.process_command("SENS:MIX:ELO:TUN:IFBW 10kHz")
    instrument.process_command("SENS:MIX:ELO:TUN:MODE PREC")
    instrument.process_command("SENS:MIX:ELO:STAT ON")

    assert instrument.process_command("SENS:MIX:ELO:LO:DELTA?") == "2000000"
    assert instrument.process_command("SENS:MIX:ELO:NORM:POIN?") == "2"
    assert instrument.process_command("SENS:MIX:ELO:TUN:IFBW?") == "10000"
    assert instrument.process_command("SENS:MIX:ELO:TUN:MODE?") == "PRECise"
    assert instrument.process_command("CALC:DATA? SDAT") != baseline

    instrument.process_command("SENS:MIX:ELO:LO:RES")
    instrument.process_command("SENS:MIX:ELO:TUN:RES")
    assert instrument.process_command("SENS:MIX:ELO:LO:DELTA?") == "0"
    assert instrument.process_command("SENS:MIX:ELO:TUN:IFBW?") == "30000"
    assert instrument.process_command("SENS:MIX:ELO:TUN:MODE?") == "BROadband"


def test_standard_mixer_state_is_preserved_by_cls_and_reset_by_rst() -> None:
    instrument = mixer_vna()
    instrument.process_command("SENS:MIX:STAG 2")
    instrument.process_command("SENS:MIX:LO2:FREQ:FIX 3GHz")
    instrument.process_command("SENS:MIX:APPL")
    instrument.process_command("*CLS")
    assert instrument.process_command("SENS:MIX:LO2:FREQ:FIX?") == "3000000000"
    instrument.process_command("*RST")
    assert instrument.process_command("SENS:MIX:STAT?") == "0"
    assert instrument.process_command("SENS:MIX:STAG?") == "1"
