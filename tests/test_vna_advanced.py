import pytest

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


def trace(name, *values, advance=AdvancePolicy.READ):
    return ScenarioStream(
        name,
        StreamKind.TRACE,
        tuple(ScenarioSample(value) for value in values),
        advance=advance,
        end=EndPolicy.HOLD_LAST,
    )


def advanced_vna(*streams) -> SCPIInstrument:
    instrument = SCPIInstrument(
        "Virtual VNA 4 Port",
        "advanced",
        vna_capabilities=VNACapabilities.create("vna-4-port"),
    )
    instrument.process_command("SENS:SWE:POIN 4")
    base = trace("S11", (1, 2, 3, 4), advance=AdvancePolicy.TRIGGER)
    instrument.attach_scenario(ScenarioDefinition("advanced-dut", (base, *streams)))
    instrument.process_command("FORM:DATA ASC")
    return instrument


def values(response: str) -> tuple[float, ...]:
    return tuple(float(value) for value in response.split(","))


def test_spectrum_setup_data_and_marker_workflow() -> None:
    instrument = advanced_vna(trace("spectrum.trace", (-80, -20, -50, -60), (-70, -10, -40, -50)))
    for command in (
        "SENS:SA:BAND:RES 100kHz",
        "SENS:SA:BAND:VID 10kHz",
        "SENS:SA:DET:FUNC PEAK",
        "SENS:SA:AVER:COUN 8",
        "SENS:SA:REF:LEV -5",
        "SENS:SA:STAT ON",
    ):
        assert instrument.process_command(command) == ""

    assert float(instrument.process_command("SENS:SA:BAND:RES?")) == 100e3
    assert instrument.process_command("SENS:SA:DET:FUNC?") == "PEAK"
    assert instrument.process_command("SENS:SA:AVER:COUN?") == "8"
    assert values(instrument.process_command("CALC:SA:DATA? TRACE")) == (-80, -20, -50, -60)

    instrument.process_command("CALC:SA:MARK1:MAX")
    marker_x = float(instrument.process_command("CALC:SA:MARK1:X?"))
    axis = values(instrument.process_command("CALC:MEAS:DATA:X?"))
    assert marker_x == pytest.approx(axis[1])
    assert float(instrument.process_command("CALC:SA:MARK1:Y?")) == -10


def test_custom_measurement_definition_selects_and_activates_real_vna_class() -> None:
    instrument = advanced_vna(trace("spectrum.trace", (-80, -20, -50, -60)))
    assert instrument.process_command("CALC:CUST:DEF 'sa_meas','Spectrum Analyzer','B'") == ""

    assert instrument.process_command("SENS:SA:STAT?") == "1"
    assert "sa_meas" in instrument.process_command("CALC:PAR:CAT:EXT?")
    assert values(instrument.process_command("CALC:SA:DATA? TRACE")) == (-80, -20, -50, -60)


def test_imd_setup_and_deterministic_results() -> None:
    instrument = advanced_vna(trace("imd.im3", (-61, -60, -58, -55)))
    commands = (
        "SENS:IMD:SWE:TYPE FCEN",
        "SENS:IMD:FREQ:FCEN:CENT 2GHz",
        "SENS:IMD:FREQ:FCEN:SPAN 400MHz",
        "SENS:IMD:FREQ:DFR 10MHz",
        "SENS:IMD:TPOW:F1 -15",
        "SENS:IMD:TPOW:F2 -16",
        "SENS:IMD:IFBW:MAIN 1kHz",
        "SENS:IMD:STAT ON",
    )
    for command in commands:
        assert instrument.process_command(command) == ""

    assert float(instrument.process_command("SENS:IMD:FREQ:FCEN:CENT?")) == 2e9
    assert instrument.process_command("SENS:IMD:TPOW:F2?") == "-16.0"
    assert instrument.process_command("SENS:IMD:HOPR?") == "9"
    assert values(instrument.process_command("CALC:IMD:DATA? IM3")) == (-61, -60, -58, -55)
    assert values(instrument.process_command("CALC:MEAS:DATA:X?")) == pytest.approx(
        (1.8e9, 1.933333333333e9, 2.066666666667e9, 2.2e9)
    )


def test_modulation_distortion_setup_and_evm_result() -> None:
    instrument = advanced_vna(trace("modulation_distortion.evm", (1.2, 1.4, 1.1, 1.3)))
    instrument.process_command("SENS:DIST:SWE:TYPE POW")
    instrument.process_command("SENS:DIST:SWE:CARR:FREQ 3GHz")
    instrument.process_command("SENS:DIST:SWE:CARR:LEV -12")
    instrument.process_command("SENS:DIST:MEAS:FILT:SRAT 20MHz")
    instrument.process_command("SENS:DIST:STAT ON")

    assert instrument.process_command("SENS:DIST:SWE:TYPE?") == "POWer"
    assert float(instrument.process_command("SENS:DIST:MEAS:FILT:SRAT?")) == 20e6
    assert values(instrument.process_command("CALC:DIST:DATA? EVM")) == (1.2, 1.4, 1.1, 1.3)
    assert instrument.process_command("SENS:DIST:CAL:STAT?") == "0"


def test_phase_noise_log_axis_and_trigger_advancement() -> None:
    instrument = advanced_vna(
        trace(
            "phase_noise.trace",
            (-90, -100, -110, -120),
            (-91, -101, -111, -121),
            advance=AdvancePolicy.TRIGGER,
        )
    )
    instrument.process_command("SENS:PN:NTYP PNO")
    instrument.process_command("SENS:PN:SWE:CARR:FREQ 1GHz")
    instrument.process_command("SENS:PN:OFFS:STAR 10Hz")
    instrument.process_command("SENS:PN:OFFS:STOP 10MHz")
    instrument.process_command("SENS:PN:STAT ON")

    assert values(instrument.process_command("CALC:PN:DATA? TRACE")) == (-90, -100, -110, -120)
    assert values(instrument.process_command("CALC:MEAS:DATA:X?")) == pytest.approx(
        (10, 1e3, 1e5, 1e7)
    )
    instrument.process_command("INIT:IMM")
    assert values(instrument.process_command("CALC:PN:DATA? TRACE")) == (-91, -101, -111, -121)


def test_diq_ranges_and_scenario_results() -> None:
    instrument = advanced_vna(trace("differential_iq.trace", (1 + 1j, 2 + 2j, 3 + 3j, 4 + 4j)))
    instrument.process_command("SENS:DIQ:FREQ:RANG:ADD")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:STAR 1GHz")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:STOP 2GHz")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:IFBW 10kHz")
    instrument.process_command("SENS:DIQ:STAT ON")

    assert instrument.process_command("SENS:DIQ:FREQ:RANG:COUN?") == "2"
    assert float(instrument.process_command("SENS:DIQ:FREQ:RANG2:STOP?")) == 2e9
    assert values(instrument.process_command("CALC:DATA? SDAT")) == (1, 1, 2, 2, 3, 3, 4, 4)
    instrument.process_command("SENS:DIQ:FREQ:RANG2:DEL")
    assert instrument.process_command("SENS:DIQ:FREQ:RANG:COUN?") == "1"


def test_wideband_iq_capture_uses_time_axis() -> None:
    instrument = advanced_vna(trace("wideband_iq.trace", (1j, -1j, 0.5j, -0.5j)))
    instrument.process_command("SENS:IQ:SRAT 200MHz")
    instrument.process_command("SENS:IQ:CAPT:TIME 30us")
    instrument.process_command("SENS:IQ:STAT ON")

    assert float(instrument.process_command("SENS:IQ:SRAT?")) == 200e6
    assert values(instrument.process_command("CALC:MEAS:DATA:X?")) == pytest.approx(
        (0, 10e-6, 20e-6, 30e-6)
    )
    assert values(instrument.process_command("CALC:DATA? SDAT")) == (0, 1, 0, -1, 0, 0.5, 0, -0.5)


def test_advanced_license_address_cls_and_reset_semantics() -> None:
    strict = SCPIInstrument(
        "Virtual VNA 2 Port",
        "strict",
        vna_capabilities=VNACapabilities.create("vna-2-port", applications=()),
    )
    assert strict.process_command("SENS:SA:STAT?") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    instrument = advanced_vna()
    instrument.process_command("SENS:SA:STAT ON")
    instrument.process_command("SENS:PN:STAT ON")
    assert instrument.process_command("SENS:SA:STAT?") == "0"
    assert instrument.process_command("SENS:PN:STAT?") == "1"
    assert instrument.process_command("SENS2:PN:STAT?") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-200,"Execution error')

    instrument.process_command("*CLS")
    assert instrument.process_command("SENS:PN:STAT?") == "1"
    instrument.process_command("*RST")
    assert instrument.process_command("SENS:PN:STAT?") == "0"


def test_bad_advanced_trace_shape_reports_scpi_data_error() -> None:
    instrument = advanced_vna(trace("spectrum.trace", (-20, -30)))
    instrument.process_command("SENS:SA:STAT ON")
    assert instrument.process_command("CALC:DATA? SDAT") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-230,"Data corrupt or stale')
