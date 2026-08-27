import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scenario import (
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import CapabilityError, VNACapabilities


def pulse_vna(*streams: ScenarioStream, integrated=True) -> SCPIInstrument:
    options = ("basic_pulsed_rf", "integrated_pulsed_rf") if integrated else ("basic_pulsed_rf",)
    capabilities = VNACapabilities.create(
        "vna-2-port", hardware_features=("pulse_control",), applications=options
    )
    instrument = SCPIInstrument("Virtual VNA 2 Port", "pulse", vna_capabilities=capabilities)
    instrument.process_command("SENS:SWE:POIN 4")
    base = ScenarioStream(
        "S11",
        StreamKind.TRACE,
        (ScenarioSample((1 + 0j, 2 + 0j, 3 + 0j, 4 + 0j)),),
        end=EndPolicy.HOLD_LAST,
    )
    instrument.attach_scenario(ScenarioDefinition("pulse-dut", (base, *streams)))
    instrument.process_command("FORM:DATA ASC")
    return instrument


def pulse_trace(name, *values, advance=AdvancePolicy.READ):
    return ScenarioStream(
        name,
        StreamKind.TRACE,
        tuple(ScenarioSample(value) for value in values),
        advance=advance,
        end=EndPolicy.HOLD_LAST,
    )


def values(response: str) -> tuple[float, ...]:
    return tuple(float(value) for value in response.split(","))


def test_pulse_generator_configuration_round_trips() -> None:
    instrument = pulse_vna()
    commands = (
        "SENS:PULS:PER 1ms",
        "SENS:PULS1:DEL 50us",
        "SENS:PULS1:WIDT 100us",
        "SENS:PULS1:DINC 2us",
        "SENS:PULS1:INV ON",
        "SENS:PULS1:STAT ON",
        "SENS:PULS0:SUBP ON",
        "SENS:PULS:TPOL NEG",
        "SENS:PULS:TTYP EDGE",
        "SENS:PULS4:OPT ON",
    )
    for command in commands:
        assert instrument.process_command(command) == ""

    assert instrument.process_command("SENS:PULS:CAT?") == ("Pulse0,Pulse1,Pulse2,Pulse3,Pulse4")
    assert instrument.process_command("SENS:PULS1:STAT?") == "1"
    assert float(instrument.process_command("SENS:PULS1:DEL?")) == pytest.approx(50e-6)
    assert float(instrument.process_command("SENS:PULS1:WIDT?")) == pytest.approx(100e-6)
    assert instrument.process_command("SENS:PULS1:INV?") == "1"
    assert instrument.process_command("SENS:PULS0:SUBP?") == "1"
    assert instrument.process_command("SENS:PULS:TPOL?") == "NEGative"
    assert instrument.process_command("SENS:PULS:TTYP?") == "EDGE"
    assert instrument.process_command("SENS:PULS4:OPT?") == "1"


def test_integrated_pulse_profile_changes_axis_and_uses_scenario_trace() -> None:
    instrument = pulse_vna(pulse_trace("pulse.profile", (0j, 1 + 0j, 0.5 + 0.25j, 0j)))
    instrument.process_command("SENS:SWE:PULS:PROF:STAR 0")
    instrument.process_command("SENS:SWE:PULS:PROF:STOP 30us")
    instrument.process_command("SENS:SWE:PULS:MODE PROF")

    assert instrument.process_command("SENS:SWE:PULS:MODE?") == "PROFile"
    assert values(instrument.process_command("CALC:MEAS:DATA:X?")) == pytest.approx(
        (0, 10e-6, 20e-6, 30e-6)
    )
    assert values(instrument.process_command("CALC:DATA? SDAT")) == pytest.approx(
        (0, 0, 1, 0, 0.5, 0.25, 0, 0)
    )


def test_point_in_pulse_follows_shared_trigger_policy() -> None:
    instrument = pulse_vna(
        pulse_trace(
            "pulse.point",
            (1, 1, 1, 1),
            (2, 2, 2, 2),
            advance=AdvancePolicy.TRIGGER,
        )
    )
    instrument.process_command("SENS:SWE:PULS:MODE STD")

    assert values(instrument.process_command("CALC:DATA? SDATA")) == (1, 0, 1, 0, 1, 0, 1, 0)
    instrument.process_command("INIT:IMM")
    assert values(instrument.process_command("CALC:DATA? SDATA")) == (2, 0, 2, 0, 2, 0, 2, 0)


def test_integrated_setup_if_filter_gate_and_master_timing_round_trip() -> None:
    instrument = pulse_vna()
    commands = (
        "SENS:SWE:PULS:CWT OFF",
        "SENS:SWE:PULS:DET OFF",
        "SENS:SWE:PULS:DRIV OFF",
        "SENS:SWE:PULS:IFG OFF",
        "SENS:SWE:PULS:PRF OFF",
        "SENS:SWE:PULS:TIM OFF",
        "SENS:SWE:PULS:SWG ON",
        "SENS:SWE:PULS:WID ON",
        "SENS:SWE:PULS:MAST:PER 1ms",
        "SENS:SWE:PULS:MAST:WIDT 20us",
        "SENS:IF:FILT:AUTO OFF",
        "SENS:IF:FILT:CMOD OFF",
        "SENS:IF:FREQ:AUTO OFF",
        "SENS:IF:FREQ -9MHz",
        "SENS:IF:FILT:STAG3:TYPE PWIN",
        'SENS:IF:FILT:STAG3:PAR "D",10us',
        'SENS:PATH:CONF:ELEM "IFGateA","Pulse2"',
    )
    for command in commands:
        assert instrument.process_command(command) == ""

    assert instrument.process_command("SENS:SWE:PULS:WID?") == "1"
    assert float(instrument.process_command("SENS:SWE:PULS:MAST:PER?")) == pytest.approx(1e-3)
    assert instrument.process_command("SENS:IF:FILT:STAG3:TYPE?") == "PWINdow"
    assert float(instrument.process_command("SENS:IF:FREQ?")) == pytest.approx(-9e6)
    assert float(instrument.process_command('SENS:IF:FILT:STAG3:PAR? "D"')) == pytest.approx(10e-6)
    assert instrument.process_command('SENS:PATH:CONF:ELEM? "IFGateA"') == "Pulse2"
    assert instrument.process_command("SENS:SWE:PULS:CAL:STAT?") == "0"


def test_pulse_timing_and_scenario_shape_errors_are_scpi_errors() -> None:
    instrument = pulse_vna(pulse_trace("pulse.profile", (1, 2)))
    assert instrument.process_command("SENS:PULS1:DEL 71s") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')

    instrument.process_command("SENS:SWE:PULS:MODE PROF")
    assert instrument.process_command("CALC:DATA? SDAT") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-230,"Data corrupt or stale')


def test_pulse_license_hardware_address_and_reset_semantics() -> None:
    with pytest.raises(CapabilityError, match="requires hardware features"):
        VNACapabilities.create(
            "vna-2-port",
            hardware_features=(),
            applications=("integrated_pulsed_rf",),
        )

    basic = pulse_vna(integrated=False)
    assert basic.process_command("SENS:PULS1:STAT ON") == ""
    assert basic.process_command("SENS:SWE:PULS:MODE?") == ""
    assert basic.process_command("SYST:ERR?").startswith('-113,"Command unavailable')
    assert basic.process_command("SENS2:PULS1:STAT?") == ""
    assert basic.process_command("SYST:ERR?").startswith('-200,"Execution error')

    integrated = pulse_vna()
    integrated.process_command("SENS:PULS1:STAT ON")
    integrated.process_command("SENS:SWE:PULS:MODE PROF")
    integrated.process_command("*CLS")
    assert integrated.process_command("SENS:PULS1:STAT?") == "1"
    assert integrated.process_command("SENS:SWE:PULS:MODE?") == "PROFile"
    integrated.process_command("*RST")
    assert integrated.process_command("SENS:PULS1:STAT?") == "0"
    assert integrated.process_command("SENS:SWE:PULS:MODE?") == "OFF"
