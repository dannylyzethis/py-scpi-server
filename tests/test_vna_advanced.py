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
from scpi_emulator.scpi.advanced import (
    CUSTOM_MEASUREMENT_CLASSES,
    CUSTOM_MEASUREMENT_REQUIREMENTS,
)


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


@pytest.mark.parametrize(
    ("measurement_class", "parameter", "state_query"),
    (
        ("Spectrum Analyzer", "B", "SENS1:SA:STAT?"),
        ("Swept IMD", "IM3", "SENS1:IMD:STAT?"),
        ("Modulation Distortion", "EVM", "SENS1:DIST:STAT?"),
        ("Phase Noise", "PN", "SENS1:PN:STAT?"),
        ("Differential I/Q", "DIQ", "SENS1:DIQ:STAT?"),
        ("Wideband I/Q", "IQ", "SENS1:IQ:STAT?"),
        ("Gain Compression", "S21", "SENS1:GC:STAT?"),
        ("Noise Figure", "NF", "SENS1:NOIS:STAT?"),
        ("Scalar Mixer/Converter", "S21", "SENS1:MIX:STAT?"),
        ("Vector Mixer/Converter", "S21", "SENS1:MIX:STAT?"),
    ),
)
def test_every_supported_custom_measurement_class_is_connected(
    measurement_class: str, parameter: str, state_query: str
) -> None:
    instrument = advanced_vna()
    command = f"CALC1:CUST:DEF 'routed','{measurement_class}','{parameter}'"

    assert instrument.process_command(command) == ""
    assert instrument.process_command("SYST:ERR?") == '0,"No error"'
    assert instrument.process_command(state_query) == "1"
    assert instrument.process_command("CALC1:PAR:MNUM?") == "2"
    assert "routed" in instrument.process_command("CALC1:PAR:CAT:EXT?")


def test_custom_measurement_registry_covers_every_engine() -> None:
    expected = {
        "spectrum",
        "imd",
        "distortion",
        "phase_noise",
        "diq",
        "wideband_iq",
        "gain_compression",
        "noise_figure",
        "scalar_converter",
        "vector_converter",
    }
    assert set(CUSTOM_MEASUREMENT_CLASSES.values()) == expected
    assert set(CUSTOM_MEASUREMENT_REQUIREMENTS) == expected


def test_custom_converter_definition_selects_scalar_and_vector_modes() -> None:
    instrument = advanced_vna()

    instrument.process_command("CALC1:CUST:DEF 'gain','Gain Compression','S21'")
    assert instrument.process_command("SENS1:GC:STAT?") == "1"

    instrument.process_command("CALC1:CUST:DEF 'scalar_mix','Scalar Mixer/Converter','S21'")
    assert instrument.process_command("SENS1:GC:STAT?") == "0"
    assert instrument.process_command("SENS1:MIX:CONV:TYPE?") == "SCALar"

    instrument.process_command("CALC1:CUST:DEF 'vector_mix','Vector Mixer/Converter','S21'")
    assert instrument.process_command("SENS1:MIX:CONV:TYPE?") == "VECTor"


def test_custom_application_definition_reports_option_and_class_errors() -> None:
    strict = SCPIInstrument(
        "Virtual VNA 2 Port",
        "strict-custom",
        vna_capabilities=VNACapabilities.create("vna-2-port", applications=()),
    )
    assert strict.process_command("CALC:CUST:DEF 'gain','Gain Compression','S21'") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')
    assert "gain" not in strict.process_command("CALC:PAR:CAT:EXT?")

    instrument = advanced_vna()
    assert instrument.process_command("CALC:CUST:DEF 'bad','Unknown Class','S21'") == ""
    assert instrument.process_command("SYST:ERR?").startswith(
        '-224,"Illegal parameter value; measurement class'
    )


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


def test_advanced_option_address_cls_and_reset_semantics() -> None:
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


def test_extended_imd_frequency_mapping_and_axis_round_trip() -> None:
    instrument = advanced_vna()
    for command in (
        "SENS:IMD:SWE:TYPE SEGM",
        "SENS:IMD:FREQ:STAR 1GHz",
        "SENS:IMD:FREQ:STOP 2GHz",
        "SENS:IMD:FREQ:CW 1.5GHz",
        "SENS:IMD:FREQ:F1 1.1GHz",
        "SENS:IMD:FREQ:F2 1.2GHz",
        "SENS:IMD:NORM:MODE ON",
        "SENS:IMD:PMAP:INP 1",
        "SENS:IMD:PMAP:OUTP 4",
        "SENS:IMD:STAT ON",
    ):
        instrument.process_command(command)

    assert instrument.process_command("SYST:ERR?") == '0,"No error"'
    assert float(instrument.process_command("SENS:IMD:FREQ:CW?")) == 1.5e9
    assert float(instrument.process_command("SENS:IMD:FREQ:F2?")) == 1.2e9
    assert instrument.process_command("SENS:IMD:NORM:MODE?") == "1"
    assert instrument.process_command("SENS:IMD:PMAP:OUTP?") == "4"
    assert values(instrument.process_command("CALC:MEAS:DATA:X?")) == pytest.approx(
        (1e9, 1.333333333333e9, 1.666666666667e9, 2e9)
    )


def test_extended_distortion_setup_drives_power_axis() -> None:
    instrument = advanced_vna()
    commands = (
        "SENS:DIST:MEAS:FILT:ALPH 0.25",
        "SENS:DIST:MEAS:FILT:SRAT:AUTO OFF",
        "SENS:DIST:MEAS:CORR:APER 12",
        "SENS:DIST:MEAS:CORR:APER:AUTO OFF",
        "SENS:DIST:MOD:SOUR 'virtual-modulation'",
        "SENS:DIST:PATH:DUT:PMAP:INP 1",
        "SENS:DIST:PATH:DUT:PMAP:OUTP 2",
        "SENS:DIST:PATH:DUT:NOM:GAIN 15",
        "SENS:DIST:PATH:DUT:NOM:NF 4",
        "SENS:DIST:SWE:POW:STAR -30",
        "SENS:DIST:SWE:POW:STOP 0",
        "SENS:DIST:SWE:POW:POIN 4",
        "SENS:DIST:SWE:SPAR ON",
        "SENS:DIST:SWE:SPAR:REUS ON",
        "SENS:DIST:SWE:SPAR:BWID 2kHz",
        "SENS:DIST:SWE:SPAR:STEP 2MHz",
        "SENS:DIST:SWE:POW:SPAR:LEV -35",
        "SENS:DIST:SWE:TYPE POW",
        "SENS:DIST:STAT ON",
    )
    for command in commands:
        instrument.process_command(command)

    assert instrument.process_command("SYST:ERR?") == '0,"No error"'
    assert instrument.process_command("SENS:DIST:MEAS:FILT:SRAT:AUTO?") == "0"
    assert instrument.process_command("SENS:DIST:MOD:SOUR?") == "virtual-modulation"
    assert instrument.process_command("SENS:DIST:SWE:SPAR?") == "1"
    assert float(instrument.process_command("SENS:DIST:SWE:SPAR:STEP?")) == 2e6
    assert values(instrument.process_command("CALC:MEAS:DATA:X?")) == (-30, -20, -10, 0)


def test_phase_noise_extended_controls_round_trip() -> None:
    instrument = advanced_vna()
    instrument.process_command("SENS:PN:BWID:RAT 8")
    instrument.process_command("SENS:PN:FAV:FACT 12")
    instrument.process_command("SENS:PN:REC 'b1'")

    assert instrument.process_command("SYST:ERR?") == '0,"No error"'
    assert instrument.process_command("SENS:PN:BWID:RAT?") == "8.0"
    assert instrument.process_command("SENS:PN:FAV:FACT?") == "12"
    assert instrument.process_command("SENS:PN:REC?") == "b1"


def test_diq_coupling_parameters_and_frequency_axis() -> None:
    instrument = advanced_vna()
    instrument.process_command("SENS:DIQ:FREQ:RANG:ADD")
    instrument.process_command("SENS:DIQ:FREQ:RANG1:STAR 1GHz")
    instrument.process_command("SENS:DIQ:FREQ:RANG1:STOP 2GHz")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:STAT ON")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:ID 1")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:OFFS 20MHz")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:UCON OFF")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:MULT 2")
    instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:DIV 3")
    instrument.process_command("SENS:DIQ:PAR:DEF 'GainF1','b2_F1/a1_F1'")
    instrument.process_command("SENS:DIQ:STAT ON")

    assert instrument.process_command("SYST:ERR?") == '0,"No error"'
    assert instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:STAT?") == "1"
    assert float(instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:OFFS?")) == 20e6
    assert instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:UCON?") == "0"
    assert instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:MULT?") == "2"
    assert instrument.process_command("SENS:DIQ:PAR:CAT?") == '"GainF1:b2_F1/a1_F1"'
    assert values(instrument.process_command("CALC:MEAS:DATA:X?")) == pytest.approx(
        (1e9, 1.333333333333e9, 1.666666666667e9, 2e9)
    )

    instrument.process_command("SENS:DIQ:PAR:DEL 'GainF1'")
    assert instrument.process_command("SENS:DIQ:PAR:CAT?") == ""
    assert instrument.process_command("SENS:DIQ:FREQ:RANG2:COUP:DIV 0") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')
