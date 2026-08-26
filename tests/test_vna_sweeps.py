import pytest

from scpi_emulator.emulator import SCPIInstrument


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
    assert instrument.vna_measurements.selected(1).stimulus == pytest.approx(
        (1e9, 10**9.5, 1e10)
    )

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
