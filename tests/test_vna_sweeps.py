import pytest

from scpi_emulator.instrument import SCPIInstrument


def vna() -> SCPIInstrument:
    return SCPIInstrument("Virtual VNA 2 Port", "vna-2-port")


def test_frequency_forms_generate_one_coherent_measurement_axis() -> None:
    instrument = vna()
    assert len(instrument.vna_measurements.selected(1).stimulus) == 201
    instrument.process_command("SENS:FREQ:STAR 1GHz")
    instrument.process_command("SENS:FREQ:STOP 3GHz")
    instrument.process_command("SENS:SWE:POIN 3")

    assert instrument.process_command("SENS:FREQ:CENT?") == "2000000000"
    assert instrument.process_command("SENS:FREQ:SPAN?") == "2000000000"
    assert instrument.vna_measurements.selected(1).stimulus == (1e9, 2e9, 3e9)

    instrument.process_command("SENS:FREQ:CENT 4GHz")
    assert instrument.process_command("SENS:FREQ:STAR?") == "3000000000"
    assert instrument.process_command("SENS:FREQ:STOP?") == "5000000000"

    instrument.process_command('CALC:PAR:DEF:EXT "LateTrace","S21"')
    instrument.process_command('CALC:PAR:SEL "LateTrace"')
    assert instrument.vna_measurements.selected(1).stimulus == (3e9, 4e9, 5e9)


def test_log_cw_and_power_sweeps_generate_the_expected_x_values() -> None:
    instrument = vna()
    instrument.process_command("SENS:FREQ:STAR 1GHz")
    instrument.process_command("SENS:FREQ:STOP 10GHz")
    instrument.process_command("SENS:SWE:POIN 3")
    instrument.process_command("SENS:SWE:TYPE LOG")
    assert instrument.vna_measurements.selected(1).stimulus == pytest.approx((1e9, 10**9.5, 1e10))

    instrument.process_command("SENS:FREQ:CW 2.4GHz")
    instrument.process_command("SENS:SWE:TYPE CW")
    assert instrument.vna_measurements.selected(1).stimulus == (2.4e9,) * 3

    instrument.process_command("SOUR:POW:STAR -20")
    instrument.process_command("SOUR:POW:STOP 0")
    instrument.process_command("SENS:SWE:TYPE POW")
    assert instrument.vna_measurements.selected(1).stimulus == (-20.0, -10.0, 0.0)


def test_if_bandwidth_and_points_drive_acquisition_duration_and_opc_operation() -> None:
    instrument = vna()
    instrument.process_command('CALC2:PAR:DEF:EXT "CH2_S21","S21"')
    instrument.process_command("SENS2:SWE:POIN 5")
    instrument.process_command("SENS2:BAND 10kHz")
    assert instrument.acquisition.channel(2).sweep_time == pytest.approx(0.0005)

    instrument.process_command("INIT2")
    assert instrument.operation_manager.pending_count == 1


def test_frequency_bandwidth_coupling_and_class_aliases_round_trip() -> None:
    instrument = vna()
    instrument.process_command("SENS:FREQ 2.5GHz")
    instrument.process_command("SENS:BAND:RES 2kHz")
    instrument.process_command("SENS:COUP:STAT ON")
    instrument.process_command("SENS:COUP:PAR POW")
    instrument.process_command("SENS:COUP:PAR:STAT OFF")

    assert instrument.process_command("SENS:FREQ?") == "2500000000"
    assert instrument.process_command("SENS:BAND:RES?") == "2000.0"
    assert instrument.process_command("SENS:COUP?") == "0"
    assert instrument.process_command("SENS:COUP:PAR?") == "POWer"
    assert instrument.process_command("SENS:CLAS:NAME?") == '"VNA"'


def test_source_power_is_scoped_by_channel_and_port() -> None:
    instrument = vna()
    instrument.process_command('CALC2:PAR:DEF:EXT "CH2_S21","S21"')
    instrument.process_command("SOUR2:POW2 -17.5")
    assert instrument.process_command("SOUR2:POW2?") == "-17.5"
    assert instrument.process_command("SOUR2:POW?") == "-10.0"


def test_model_frequency_and_port_limits_report_scpi_errors() -> None:
    instrument = vna()
    for command in ("SENS:FREQ:STOP 51GHz", "SOUR:POW3 -10", "SENS:BAND 0"):
        assert instrument.process_command(command) == ""
        assert instrument.error_queue.pop().code == -222


def test_segment_lifecycle_builds_axis_and_segment_specific_timing() -> None:
    instrument = vna()
    instrument.process_command("SENS:SEGM1:ADD")
    instrument.process_command("SENS:SEGM2:ADD")
    instrument.process_command("SENS:SEGM1:FREQ:STAR 1GHz")
    instrument.process_command("SENS:SEGM1:FREQ:STOP 2GHz")
    instrument.process_command("SENS:SEGM1:SWE:POIN 2")
    instrument.process_command("SENS:SEGM1:BWID 1kHz")
    instrument.process_command("SENS:SEGM2:FREQ:STAR 3GHz")
    instrument.process_command("SENS:SEGM2:FREQ:STOP 5GHz")
    instrument.process_command("SENS:SEGM2:SWE:POIN 3")
    instrument.process_command("SENS:SEGM2:BWID 2kHz")
    instrument.process_command("SENS:SWE:TYPE SEGM")

    assert instrument.process_command("SENS:SEGM:COUN?") == "2"
    assert instrument.vna_measurements.selected(1).stimulus == (1e9, 2e9, 3e9, 4e9, 5e9)
    assert instrument.acquisition.channel(1).sweep_time == pytest.approx(0.0035)

    instrument.process_command("SENS:SEGM1:DEL")
    assert instrument.process_command("SENS:SEGM:COUN?") == "1"
    instrument.process_command("SENS:SEGM:DEL:ALL")
    assert instrument.process_command("SENS:SEGM:COUN?") == "0"


def test_receiver_attenuation_dwell_and_generation_are_channel_scoped() -> None:
    instrument = vna()
    instrument.process_command('CALC2:PAR:DEF:EXT "CH2_S21","S21"')
    instrument.process_command("SENS2:POW:ATT AREC,20")
    instrument.process_command("SENS2:SWE:GEN STEP")
    instrument.process_command("SENS2:SWE:DWEL 0.001S")

    assert instrument.process_command("SENS2:POW:ATT? AREC") == "20.0"
    assert instrument.process_command("SENS2:SWE:GEN?") == "STEPped"
    assert instrument.process_command("SENS2:SWE:DWEL?") == "0.001"


def test_clear_preserves_sweep_configuration_but_reset_restores_preset() -> None:
    instrument = vna()
    instrument.process_command("SENS:FREQ:STAR 1GHz")
    instrument.process_command("SENS:SWE:POIN 11")
    instrument.process_command("*CLS")
    assert instrument.process_command("SENS:FREQ:STAR?") == "1000000000"
    assert instrument.process_command("SENS:SWE:POIN?") == "11"

    instrument.process_command("*RST")
    assert instrument.process_command("SENS:FREQ:STAR?") == "10000000"
    assert instrument.process_command("SENS:SWE:POIN?") == "201"

    instrument.process_command("SENS:FREQ:STAR 2GHz")
    instrument.process_command("SYST:PRES")
    assert instrument.process_command("SENS:FREQ:STAR?") == "10000000"


def test_output_source_and_sweep_controls_round_trip_and_reset() -> None:
    instrument = vna()
    instrument.process_command("OUTP:STAT OFF")
    instrument.process_command("OUTP:MAN:NOIS ON")
    instrument.process_command("SOUR:POW2:LEV:IMM:AMPL -12")
    instrument.process_command("SOUR:POW2:ALC OPEN")
    instrument.process_command("SOUR:POW2:ATT 10")
    instrument.process_command("SOUR:POW2:ATT:REC:REF 20")
    instrument.process_command("SOUR:POW2:ATT:REC:TEST 30")
    instrument.process_command("SOUR:POW2:MODE NOCTL")
    instrument.process_command("SOUR:POW:SLOP 0.5")
    instrument.process_command("SOUR:POW:SLOP:STAT ON")
    instrument.process_command("SENS:SWE:DWEL:SDEL 0.1")
    instrument.process_command("SENS:SWE:TRIG:DEL 0.2")
    instrument.process_command("SENS:SWE:TRIG:MODE POIN")
    instrument.process_command("SENS:SWE:GEN:POIN ON")
    instrument.process_command("SENS:SWE:LFEX:STAT ON")
    instrument.process_command("SENS:SWE:SPE FAST")

    assert instrument.process_command("OUTP?") == "0"
    assert instrument.process_command("OUTP:MAN:NOIS:STAT?") == "1"
    assert instrument.process_command("SOUR:CAT?") == '"Port 1,Port 2"'
    assert instrument.process_command('SOUR:PORT:NUM? "Port 2"') == "2"
    assert instrument.process_command("SOUR:POW2:LEV:IMM:AMPL?") == "-12.0"
    assert instrument.process_command("SOUR:POW2:ALC:MODE?") == "OPENloop"
    assert instrument.process_command("SOUR:POW2:ATT:AUTO?") == "0"
    assert instrument.process_command("SOUR:POW2:ATT:REC:REF?") == "20"
    assert instrument.process_command("SOUR:POW2:ATT:REC:TEST?") == "30"
    assert instrument.process_command("SOUR:POW2:MODE?") == "NOCTL"
    assert instrument.process_command("SOUR:POW:SLOP?") == "0.5"
    assert instrument.process_command("SOUR:POW:SLOP:STAT?") == "1"
    assert instrument.process_command("SENS:SWE:DWEL:SDEL?") == "0.1"
    assert instrument.process_command("SENS:SWE:TRIG:DEL?") == "0.2"
    assert instrument.process_command("SENS:SWE:TRIG:MODE?") == "POINt"
    assert instrument.process_command("SENS:SWE:GEN:POIN?") == "1"
    assert instrument.process_command("SENS:SWE:LFEX:STAT?") == "1"
    assert instrument.process_command("SENS:SWE:SPE?") == "FAST"

    instrument.process_command("*RST")
    assert instrument.process_command("OUTP?") == "1"
    assert instrument.process_command("OUTP:MAN:NOIS?") == "0"


def test_power_range_step_and_manual_sweep_time_are_coherent() -> None:
    instrument = vna()
    instrument.process_command("SOUR:POW:STAR -30")
    instrument.process_command("SOUR:POW:STOP -10")
    instrument.process_command("SOUR:POW:CENT -15")
    assert instrument.process_command("SOUR:POW:STAR?") == "-25.0"
    assert instrument.process_command("SOUR:POW:STOP?") == "-5.0"
    instrument.process_command("SOUR:POW:SPAN 10")
    assert instrument.process_command("SOUR:POW:CENT?") == "-15.0"
    assert instrument.process_command("SOUR:POW:SPAN?") == "10.0"

    instrument.process_command("SENS:FREQ:STAR 1GHz")
    instrument.process_command("SENS:FREQ:STOP 2GHz")
    instrument.process_command("SENS:SWE:STEP 250MHz")
    assert instrument.process_command("SENS:SWE:POIN?") == "5"
    assert instrument.process_command("SENS:SWE:STEP?") == "250000000"

    instrument.process_command("SENS:SWE:TIME 1.25S")
    assert instrument.process_command("SENS:SWE:TIME:AUTO?") == "0"
    assert instrument.process_command("SENS:SWE:TIME?") == "1.25"
    assert instrument.acquisition.channel(1).sweep_time == 1.25
    instrument.process_command("SENS:SWE:TIME:AUTO ON")
    assert instrument.process_command("SENS:SWE:TIME:AUTO?") == "1"
    assert instrument.acquisition.channel(1).sweep_time == pytest.approx(0.005)


def test_complete_segment_controls_and_per_port_values_round_trip() -> None:
    instrument = vna()
    instrument.process_command("SENS:SEGM1:ADD")
    instrument.process_command("SENS:SEGM1:FREQ:SPAN 1GHz")
    instrument.process_command("SENS:SEGM1:FREQ:CENT 2GHz")
    instrument.process_command("SENS:SEGM1:SWE:POIN 11")
    instrument.process_command("SENS:SEGM1:SWE:DWEL 0.001S")
    instrument.process_command("SENS:SEGM1:SWE:DEL 0.2S")
    instrument.process_command("SENS:SEGM1:SWE:GEN STEP")
    instrument.process_command("SENS:SEGM1:SWE:TIME 2S")
    instrument.process_command("SENS:SEGM1:POW2:LEV -20")
    instrument.process_command("SENS:SEGM1:POW2:ATT:REC:REF 15")
    instrument.process_command("SENS:SEGM1:POW2:ATT:REC:TEST 25")
    instrument.process_command("SENS:SEGM1:BWID:PORT2:RES 10kHz")
    instrument.process_command("SENS:SEGM1:SHLO ON")
    instrument.process_command("SENS:SEGM1:SHLO:CONT ON")
    instrument.process_command("SENS:SEGM1:SWE:DEL:CONT ON")
    instrument.process_command("SENS:SEGM1:SWE:DWEL:CONT ON")
    instrument.process_command("SENS:SEGM1:SWE:GEN:CONT ON")
    instrument.process_command("SENS:SEGM1:SWE:POIN:CONT ON")
    instrument.process_command("SENS:SEGM:BWID:PORT2:RES:CONT ON")
    instrument.process_command("SENS:SEGM:POW:LEV:CONT ON")
    instrument.process_command("SENS:SEGM:X:SPAC LOG")
    instrument.process_command('SENS:SEGM:LIST "segment fixture"')

    assert instrument.process_command("SENS:SEGM1?") == "1"
    assert instrument.process_command("SENS:SEGM1:FREQ:STAR?") == "1500000000"
    assert instrument.process_command("SENS:SEGM1:FREQ:STOP?") == "2500000000"
    assert instrument.process_command("SENS:SEGM1:FREQ:CENT?") == "2000000000"
    assert instrument.process_command("SENS:SEGM1:FREQ:SPAN?") == "1000000000"
    assert instrument.process_command("SENS:SEGM1:SWE:DEL?") == "0.2"
    assert instrument.process_command("SENS:SEGM1:SWE:GEN?") == "STEPped"
    assert instrument.process_command("SENS:SEGM1:POW2?") == "-20"
    assert instrument.process_command("SENS:SEGM1:POW2:ATT:REC:REF?") == "15"
    assert instrument.process_command("SENS:SEGM1:POW2:ATT:REC:TEST?") == "25"
    assert instrument.process_command("SENS:SEGM1:BWID:PORT2?") == "10000"
    assert instrument.process_command("SENS:SEGM1:SHLO:CONT?") == "1"
    assert instrument.process_command("SENS:SEGM1:SWE:DEL:CONT?") == "1"
    assert instrument.process_command("SENS:SEGM1:SWE:DWEL:CONT?") == "1"
    assert instrument.process_command("SENS:SEGM1:SWE:GEN:CONT?") == "1"
    assert instrument.process_command("SENS:SEGM1:SWE:POIN:CONT?") == "1"
    assert instrument.process_command("SENS:SEGM:BWID:PORT2:CONT?") == "1"
    assert instrument.process_command("SENS:SEGM:POW:CONT?") == "1"
    assert instrument.process_command("SENS:SEGM:X:SPAC?") == "LOGarithmic"
    assert instrument.process_command("SENS:SEGM:LIST?") == "segment fixture"
    assert instrument.process_command("SENS:SEGM1:SWE:POIN:TOT?") == "11"
    assert instrument.process_command("SENS:SEGM1:SWE:TIME:TOT?") == "2.0"


def test_new_source_and_segment_controls_report_deterministic_errors() -> None:
    instrument = vna()
    commands_and_errors = (
        ('SOUR:PORT:NUM? "Port 3"', -222),
        ("SOUR:POW3:ATT 10", -222),
        ("SENS:SEGM1:POW2 -10", -200),
    )
    for command, expected in commands_and_errors:
        assert instrument.process_command(command) == ""
        assert instrument.error_queue.pop().code == expected
