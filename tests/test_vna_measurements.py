import pytest

from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scpi import SCPICommandError, VNACapabilities


def vna() -> SCPIInstrument:
    return SCPIInstrument(
        "Virtual VNA 2 Port",
        "vna",
        vna_capabilities=VNACapabilities.create("vna-2-port"),
    )


def test_preset_state_has_coherent_channel_measurement_trace_and_selection() -> None:
    instrument = vna()

    assert instrument.process_command("CALC:PAR:CAT:EXT?") == '"CH1_S11_1,S11"'
    assert instrument.process_command("CALC:PAR:MNUM?") == "1"
    assert instrument.process_command("CALC:PAR:WNUM?") == "1"
    assert instrument.process_command("CALC:PAR:TNUM?") == "1"
    assert instrument.process_command("DISP:CAT?") == "1"
    assert instrument.process_command("DISP:WIND:CAT?") == "1"
    assert instrument.process_command("SYST:ACT:CHAN?") == "1"
    assert instrument.process_command("SYST:ACT:MEAS?") == "CH1_S11_1"


def test_indexed_abbreviated_define_feed_select_modify_and_delete_workflow() -> None:
    instrument = vna()

    assert instrument.process_command('CALC2:PAR:DEF:EXT "InputGain","S21"') == ""
    assert instrument.process_command('CALC2:PAR:DEF:EXT "Receiver","A/R1,3"') == ""
    assert instrument.process_command("DISP:WIND2:STAT ON") == ""
    assert instrument.process_command('DISP:WIND2:TRAC3:FEED "InputGain"') == ""
    assert instrument.process_command('DISP:WIND2:TRAC4:FEED "Receiver"') == ""

    assert instrument.process_command("CALC2:PAR:CAT:EXT?") == ('"InputGain,S21,Receiver,A/R1,3"')
    assert instrument.process_command("DISP:WIND2:TRAC3:FEED?") == "InputGain"
    assert instrument.process_command("SYST:ACT:CHAN?") == "2"
    assert instrument.process_command("SYST:ACT:MEAS?") == "Receiver"

    instrument.process_command('CALC2:PAR:SEL "InputGain"')
    assert instrument.process_command("CALC2:PAR:MNUM?") == "1"
    instrument.process_command('CALC2:PAR:MOD:EXT "S12"')
    assert instrument.process_command("CALC2:PAR:CAT?") == '"InputGain,S12,Receiver,A/R1,3"'

    instrument.process_command('CALC2:PAR:DEL "InputGain"')
    assert instrument.process_command("DISP:WIND2:CAT?") == "4"
    instrument.process_command("CALC2:PAR:DEL:ALL")
    assert instrument.process_command("CALC2:PAR:CAT?") == "EMPTY"


def test_legacy_define_generates_unique_names_and_format_enums_accept_abbreviations() -> None:
    instrument = vna()

    instrument.process_command("CALC2:PAR:DEF S21")
    instrument.process_command("CALC2:PAR:DEF S21")
    assert instrument.process_command("CALC2:PAR:CAT?") == ('"CH2_S21_1,S21,CH2_S21_2,S21"')
    instrument.process_command('CALC2:PAR:SEL "CH2_S21_2"')
    instrument.process_command("CALC2:FORM MLOG")
    assert instrument.process_command("CALC2:FORM?") == "MLOGarithmic"


def test_marker_position_format_search_and_y_data_follow_selected_measurement() -> None:
    instrument = vna()
    measurement = instrument.vna_measurements.selected(1)
    measurement.stimulus = (1e9, 2e9, 3e9)
    measurement.samples = (0.1 + 0j, 0.5 + 0.25j, 0.2 - 0.1j)

    instrument.process_command("CALC:MARK2:STAT ON")
    instrument.process_command("CALC:MARK2:X 2GHz")
    instrument.process_command("CALC:MARK2:FORM POL")
    assert instrument.process_command("CALC:MARK2?") == "1"
    assert instrument.process_command("CALC:MARK2:X?") == "2000000000.0"
    assert instrument.process_command("CALC:MARK2:Y?") == "0.5,0.25"

    instrument.process_command("CALC:MARK2:FUNC:EXEC MAX")
    assert instrument.process_command("CALC:MARK2:BUCK?") == "1"
    instrument.process_command("CALC:MARK:AOFF")
    assert instrument.process_command("CALC:MARK2:STAT?") == "0"


def test_math_memory_limit_and_equation_state_are_measurement_scoped() -> None:
    instrument = vna()
    measurement = instrument.vna_measurements.selected(1)
    measurement.samples = (1 + 2j, 3 + 4j)

    instrument.process_command("CALC:MATH:MEM")
    measurement.samples = (2 + 3j, 4 + 5j)
    instrument.process_command("CALC:MATH:FUNC SUBT")
    instrument.process_command("CALC:MATH:INT ON")
    instrument.process_command("CALC:LIM:STAT ON")
    instrument.process_command('CALC:EQU:TEXT "S21/S11"')
    instrument.process_command("CALC:EQU:STAT ON")

    assert measurement.memory == (1 + 2j, 3 + 4j)
    assert instrument.process_command("CALC:MATH:FUNC?") == "SUBTract"
    assert instrument.process_command("CALC:MATH:INT?") == "1"
    assert instrument.process_command("CALC:LIM:STAT?") == "1"
    assert instrument.process_command("CALC:LIM:FAIL?") == "0"
    assert instrument.process_command("CALC:EQU:TEXT?") == "S21/S11"
    assert instrument.process_command("CALC:EQU:STAT?") == "1"


def test_channel_window_and_trace_lifecycles_do_not_delete_measurements_accidentally() -> None:
    instrument = vna()
    instrument.process_command('CALC3:PAR:DEF:EXT "S33","S33"')
    instrument.process_command('DISP:WIND3:TRAC2:FEED "S33"')

    instrument.process_command("DISP:WIND3:TRAC2:STAT OFF")
    assert instrument.process_command("DISP:WIND3:TRAC2:STAT?") == "0"
    assert instrument.process_command("CALC3:PAR:CAT?") == '"S33,S33"'
    instrument.process_command("DISP:WIND3:STAT OFF")
    assert instrument.process_command("DISP:WIND3:STAT?") == "0"
    assert instrument.process_command("CALC3:PAR:CAT?") == '"S33,S33"'

    instrument.process_command("DISP:CHAN3:STAT OFF")
    assert instrument.process_command("DISP:CHAN3:STAT?") == "0"
    assert instrument.process_command("CALC3:PAR:CAT?") == ""
    assert instrument.error_queue.pop().code == -200


def test_clear_and_device_clear_preserve_composition_but_reset_restores_preset() -> None:
    instrument = vna()
    instrument.process_command('CALC2:PAR:DEF:EXT "DUT","S21"')
    instrument.process_command('DISP:WIND2:TRAC1:FEED "DUT"')

    instrument.process_command("*CLS")
    assert instrument.process_command("CALC2:PAR:CAT?") == '"DUT,S21"'
    instrument.visa_device_clear()
    assert instrument.process_command("DISP:WIND2:TRAC1:FEED?") == "DUT"

    instrument.process_command("*RST")
    assert instrument.process_command("CALC:PAR:CAT?") == '"CH1_S11_1,S11"'
    assert instrument.process_command("DISP:WIND2:STAT?") == "0"


def test_duplicate_missing_and_out_of_range_addresses_return_scpi_errors() -> None:
    state = vna().vna_measurements
    with pytest.raises(SCPICommandError) as duplicate:
        state.define(1, "CH1_S11_1", "S21")
    assert duplicate.value.code == -200

    instrument = vna()
    for command, code in (
        ('CALC:PAR:SEL "missing"', -200),
        ('DISP:WIND2:TRAC1:FEED "missing"', -200),
        ("CALC201:FORM?", -113),
        ("CALC:MARK16:X?", -113),
    ):
        assert instrument.process_command(command) == ""
        assert instrument.error_queue.pop().code == code
