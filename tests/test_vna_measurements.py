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


def test_trace_data_function_group_delay_hold_smoothing_and_x_axis_commands() -> None:
    instrument = vna()
    measurement = instrument.vna_measurements.selected(1)
    measurement.stimulus = (1e9, 2e9, 3e9)
    measurement.samples = (1 + 0j, 2 + 0j, 4 + 0j)

    assert instrument.process_command("CALC:DATA:MFD?") == "1,0,2,0,4,0"
    assert instrument.process_command("CALC:DATA1:MSD?") == "1,2,4"
    assert instrument.process_command("CALC:MEAS1:RDAT?") == "1,0,2,0,4,0"
    assert instrument.process_command("CALC:X?") == "1000000000,2000000000,3000000000"

    instrument.process_command("CALC:FUNC:TYPE MAX")
    instrument.process_command("CALC:FUNC:DOM:USER:STAR 1.5GHz")
    instrument.process_command("CALC:FUNC:DOM:USER:STOP 3GHz")
    instrument.process_command("CALC:FUNC:DOM:USER ON")
    instrument.process_command("CALC:FUNC:STAT ON")
    instrument.process_command("CALC:FUNC:EXEC")
    assert instrument.process_command("CALC:FUNC:DATA?") == "4"
    assert instrument.process_command("CALC:FUNC:STAT?") == "1"

    instrument.process_command("CALC:GDEL:FREQ 2GHz")
    instrument.process_command("CALC:GDEL:PERC 5")
    instrument.process_command("CALC:GDEL:POIN 7")
    instrument.process_command("CALC:HOLD:TYPE MAX")
    instrument.process_command("CALC:SMO:APER 12.5")
    instrument.process_command("CALC:SMO:POIN 5")
    instrument.process_command("CALC:SMO ON")
    instrument.process_command("CALC:X:AXIS LOG")
    instrument.process_command("CALC:X:AXIS:DOM FREQ")

    assert instrument.process_command("CALC:GDEL:FREQ?") == "2000000000"
    assert instrument.process_command("CALC:GDEL:PERC?") == "5"
    assert instrument.process_command("CALC:GDEL:POIN?") == "7"
    assert instrument.process_command("CALC:HOLD:TYPE?") == "MAXimum"
    assert instrument.process_command("CALC:SMO:APER?") == "12.5"
    assert instrument.process_command("CALC:SMO:POIN?") == "5"
    assert instrument.process_command("CALC:SMO?") == "1"
    assert instrument.process_command("CALC:X:AXIS?") == "LOGarithmic"
    assert instrument.process_command("CALC:X:AXIS:DOM?") == "FREQuency"


def test_limit_segments_evaluate_current_trace_and_report_failed_points() -> None:
    instrument = vna()
    measurement = instrument.vna_measurements.selected(1)
    measurement.stimulus = (1e9, 2e9, 3e9)
    measurement.samples = (1 + 0j, 2 + 0j, 3 + 0j)

    instrument.process_command("CALC:LIM:SEGM1:STIM:STAR 1GHz")
    instrument.process_command("CALC:LIM:SEGM1:STIM:STOP 3GHz")
    instrument.process_command("CALC:LIM:SEGM1:AMPL:STAR 1.5")
    instrument.process_command("CALC:LIM:SEGM1:AMPL:STOP 1.5")
    instrument.process_command("CALC:LIM:SEGM1:TYPE UPP")
    instrument.process_command("CALC:LIM ON")
    instrument.process_command("CALC:LIM:DISP ON")
    instrument.process_command("CALC:LIM:SOUN OFF")

    assert instrument.process_command("CALC:LIM:SEGM:COUN?") == "1"
    assert instrument.process_command("CALC:LIM:FAIL?") == "1"
    assert instrument.process_command("CALC:LIM:REP:POIN?") == "2,3"
    assert instrument.process_command("CALC:LIM:REP?") == "1,2"
    assert instrument.process_command("CALC:LIM:SEGM1:TYPE?") == "UPPer"

    instrument.process_command("CALC:LIM:DATA:DEL")
    assert instrument.process_command("CALC:LIM:SEGM:COUN?") == "0"
    assert instrument.process_command("CALC:LIM:FAIL?") == "0"


def test_extended_marker_state_and_target_search_use_current_trace() -> None:
    instrument = vna()
    measurement = instrument.vna_measurements.selected(1)
    measurement.stimulus = (1e9, 2e9, 3e9)
    measurement.samples = (1 + 0j, 5 + 0j, 2 + 0j)

    instrument.process_command("CALC:MARK2:COMP:LEV 1")
    instrument.process_command("CALC:MARK2:COUP ON")
    instrument.process_command("CALC:MARK2:COUP:METH TRAC")
    instrument.process_command("CALC:MARK2:DELT ON")
    instrument.process_command("CALC:MARK2:DISC ON")
    instrument.process_command("CALC:MARK2:FUNC MAX")
    instrument.process_command("CALC:MARK2:FUNC:TRAC ON")
    instrument.process_command("CALC:MARK2:TARG 4.5")
    instrument.process_command("CALC:MARK2:SET")

    assert instrument.process_command("CALC:MARK2:X?") == "2000000000.0"
    assert instrument.process_command("CALC:MARK2:COMP:PIN?") == "2000000000"
    assert instrument.process_command("CALC:MARK2:COMP:POUT?") == "5"
    assert instrument.process_command("CALC:MARK2:COUP?") == "1"
    assert instrument.process_command("CALC:MARK2:COUP:METH?") == "TRACe"
    assert instrument.process_command("CALC:MARK2:DELT?") == "1"
    assert instrument.process_command("CALC:MARK2:DISC?") == "1"
    assert instrument.process_command("CALC:MARK2:FUNC?") == "MAXimum"
    assert instrument.process_command("CALC:MARK2:FUNC:TRAC?") == "1"
    assert instrument.process_command("CALC:MARK2:TARG?") == "4.5"


def test_parameter_aliases_count_and_selection_are_stateful() -> None:
    instrument = vna()
    instrument.process_command('CALC:PAR:EXT "Second","S21"')
    assert instrument.process_command("CALC:PAR:COUN?") == "2"
    assert instrument.process_command("CALC:PAR:TAG:NEXT?") == "3"
    instrument.process_command("CALC:PAR:MNUM:SEL 2")
    assert instrument.process_command("CALC:PAR:SEL?") == "Second"
    instrument.process_command('CALC:PAR:DEL:NAME "Second"')
    assert instrument.process_command("CALC:PAR:COUN?") == "1"


def test_display_measurement_metadata_and_scale_follow_live_composition() -> None:
    instrument = vna()
    instrument.process_command('CALC:PAR:DEF:EXT "Second","S21"')
    instrument.process_command('DISP:MEAS2:FEED "Second"')
    instrument.process_command("DISP:MEAS2 ON")
    instrument.process_command("DISP:MEAS2:MEM ON")
    instrument.process_command('DISP:MEAS2:TITL:DATA "Gain"')
    instrument.process_command("DISP:MEAS2:TITL ON")
    instrument.process_command("DISP:MEAS2:Y:PDIV 2.5")
    instrument.process_command("DISP:MEAS2:Y:RLEV 10")
    instrument.process_command("DISP:MEAS2:Y:RPOS 7")

    assert instrument.process_command("DISP:MEAS2?") == "1"
    assert instrument.process_command("DISP:MEAS2:MEM?") == "1"
    assert instrument.process_command("DISP:MEAS2:TITL?") == "1"
    assert instrument.process_command("DISP:MEAS2:Y:SCAL:PDIV?") == "2.5"
    assert instrument.process_command("DISP:MEAS2:Y:SCAL:RLEV?") == "10"
    assert instrument.process_command("DISP:MEAS2:Y:SCAL:RPOS?") == "7"
    assert instrument.process_command("DISP:WIND:TRAC2:FEED?") == "Second"

    instrument.process_command("DISP:MEAS2:SEL")
    assert instrument.process_command("SYST:ACT:MEAS?") == "Second"
    instrument.process_command("DISP:MEAS2:MOVE 3")
    assert instrument.process_command("DISP:WIND:TRAC3:FEED?") == "Second"
    instrument.process_command("DISP:MEAS2:DEL")
    assert instrument.process_command("DISP:WIND:CAT?") == "1"
    assert instrument.process_command("CALC:PAR:COUN?") == "2"


def test_display_window_trace_and_global_metadata_round_trip() -> None:
    instrument = vna()
    instrument.process_command("DISP:ENAB OFF")
    instrument.process_command("DISP:VIS OFF")
    instrument.process_command("DISP:ANN:FREQ OFF")
    instrument.process_command("DISP:FSIG 8")
    instrument.process_command("DISP:GUI:POW:SPIN:RES 0.25")
    instrument.process_command("DISP:WIND:TABL ON")
    instrument.process_command('DISP:WIND:TITL:DATA "Main"')
    instrument.process_command("DISP:WIND:TITL ON")
    instrument.process_command("DISP:WIND:Y:DIV 12")
    instrument.process_command("DISP:WIND:TRAC1:MEM ON")
    instrument.process_command("DISP:WIND:TRAC1:TITL ON")
    instrument.process_command("DISP:WIND:TRAC1:Y:SCAL:PDIV 5")
    instrument.process_command("DISP:WIND:TRAC1:Y:SCAL:RPOS 6")
    instrument.process_command("DISP:WIND:TRAC:Y:SCAL:COUP ON")
    instrument.process_command("DISP:WIND:TRAC:Y:SCAL:COUP:METH UNIT")

    assert instrument.process_command("DISP:ENAB?") == "0"
    assert instrument.process_command("DISP:VIS?") == "0"
    assert instrument.process_command("DISP:ANN:FREQ?") == "0"
    assert instrument.process_command("DISP:FSIG?") == "8"
    assert instrument.process_command("DISP:GUI:POW:SPIN:RES?") == "0.25"
    assert instrument.process_command("DISP:WIND:TABL?") == "1"
    assert instrument.process_command("DISP:WIND:TITL:DATA?") == "Main"
    assert instrument.process_command("DISP:WIND:TITL?") == "1"
    assert instrument.process_command("DISP:WIND:Y:SCAL:DIV?") == "12"
    assert instrument.process_command("DISP:WIND:TRAC1:MEM?") == "1"
    assert instrument.process_command("DISP:WIND:TRAC1:TITL?") == "1"
    assert instrument.process_command("DISP:WIND:TRAC1:Y:SCAL:PDIV?") == "5"
    assert instrument.process_command("DISP:WIND:TRAC1:Y:SCAL:RPOS?") == "6"
    assert instrument.process_command("DISP:WIND:TRAC:Y:COUP?") == "1"
    assert instrument.process_command("DISP:WIND:TRAC:Y:COUP:METH?") == "UNITs"


def test_system_discovery_catalogs_and_channel_metadata_follow_composition() -> None:
    instrument = vna()
    instrument.process_command('CALC2:PAR:DEF:EXT "Gain","S21"')
    instrument.process_command('DISP:WIND2:TRAC3:FEED "Gain"')

    assert instrument.process_command("SYST:CHAN:CAT?") == "1,2"
    assert instrument.process_command("SYST:WIND:CAT?") == "1,2"
    assert instrument.process_command("SYST:SHE:CAT?") == "1,2"
    assert instrument.process_command("SYST:MEAS:CAT?") == '"CH1_S11_1,Gain"'
    assert instrument.process_command("SYST:MEAS2:NAME?") == "Gain"
    assert instrument.process_command("SYST:MEAS2:TRAC?") == "3"
    assert instrument.process_command("SYST:MEAS2:WIND?") == "2"
    assert instrument.process_command("SYST:ACT:MEAS:NUMB?") == "1"
    assert instrument.process_command("SYST:ACT:SHE?") == "2"

    instrument.process_command("SYST:CHAN:COUP ON")
    instrument.process_command("SYST:CHAN:COUP:GRO 2")
    instrument.process_command("SYST:CHAN:COUP:PAR ON")
    instrument.process_command("SYST:CHAN:NOIS:PAR ON")
    instrument.process_command("SYST:CHAN:NOIS:PAR:GRO 2")
    instrument.process_command('SYST:CHAN:NOIS:PAR:GRO:LIST "1,2"')
    assert instrument.process_command("SYST:CHAN:COUP?") == "1"
    assert instrument.process_command("SYST:CHAN:COUP:GRO?") == "2"
    assert instrument.process_command("SYST:CHAN:COUP:PAR:STAT?") == "1"
    assert instrument.process_command("SYST:CHAN:NOIS:PAR:STAT?") == "1"
    assert instrument.process_command("SYST:CHAN:NOIS:PAR:GRO?") == "2"
    assert instrument.process_command("SYST:CHAN:NOIS:PAR:GRO:LIST?") == "1,2"

    assert instrument.process_command("SYST:CONF:BIT?") == "64"
    assert instrument.process_command("SYST:CONF:REV:CPU?") == "E.1.0"
    assert instrument.process_command("SYST:MCL:CAT?") == '"VNA"'
    assert len(instrument.process_command("SYST:DATE?").split(",")) == 3
    assert len(instrument.process_command("SYST:TIME?").split(",")) == 3

    instrument.process_command("SYST:CHAN:DEL 2")
    assert instrument.process_command("SYST:CHAN:CAT?") == "1"
