import pytest

from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scenario import (
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import VNACapabilities


def instrument(*, applications=("all",)) -> SCPIInstrument:
    capabilities = VNACapabilities.create("vna-4-port", applications=applications)
    return SCPIInstrument("Virtual VNA 4 Port", "stimulus-apps", vna_capabilities=capabilities)


def numbers(response: str) -> tuple[float, ...]:
    return tuple(float(item) for item in response.split(","))


def test_fast_cw_replaces_x_axis_and_round_trips_settings() -> None:
    vna = instrument()
    vna.process_command("SENS:SWE:POIN 3")
    assert vna.process_command("SENS:FCW:FREQ 2.4GHz") == ""
    assert vna.process_command("SENS:FCW:DWEL 0.002") == ""
    assert vna.process_command("SENS:FCW:STAT ON") == ""

    assert numbers(vna.process_command("CALC:MEAS:DATA:X?")) == (2.4e9, 2.4e9, 2.4e9)
    assert vna.process_command("SENS:FCW:FREQ?") == "2400000000"
    assert vna.process_command("SENS:FCW:DWEL?") == "0.002"


def test_scenario_waveform_envelope_modifies_normal_trace_data() -> None:
    vna = instrument()
    vna.process_command("SENS:SWE:POIN 3")
    vna.attach_scenario(
        ScenarioDefinition(
            "waveform",
            (
                ScenarioStream(
                    "S11",
                    StreamKind.TRACE,
                    (ScenarioSample((1 + 0j, 2 + 0j, 3 + 0j)),),
                    end=EndPolicy.HOLD_LAST,
                ),
                ScenarioStream(
                    "arbitrary_waveform.envelope",
                    StreamKind.TRACE,
                    (ScenarioSample((1 + 0j, 0.5 + 0j, 0 + 1j)),),
                    end=EndPolicy.HOLD_LAST,
                ),
            ),
        )
    )
    for command in ("SENS:AWG:SCAL 2", "SENS:AWG:OFFS 1", "SENS:AWG:STAT ON"):
        assert vna.process_command(command) == ""

    assert numbers(vna.process_command("CALC:DATA? SDAT")) == pytest.approx((3, 0, 4, 0, 3, 6))


def test_stimulus_applications_are_option_gated_and_validate_frequency() -> None:
    strict = instrument(applications=())
    for command in ("SENS:FCW:STAT?", "SENS:AWG:STAT?"):
        assert strict.process_command(command) == ""
        assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    option_enabled = instrument()
    assert option_enabled.process_command("SENS:FCW:FREQ 100kHz") == ""
    assert option_enabled.process_command("SYST:ERR?").startswith('-222,"Data out of range')


def test_stimulus_application_state_survives_cls_and_resets_with_rst() -> None:
    vna = instrument()
    vna.process_command("SENS:FCW:STAT ON")
    vna.process_command("SENS:AWG:STAT ON")
    vna.process_command("*CLS")
    assert vna.process_command("SENS:FCW:STAT?") == "1"
    assert vna.process_command("SENS:AWG:STAT?") == "1"

    vna.process_command("*RST")
    assert vna.process_command("SENS:FCW:STAT?") == "0"
    assert vna.process_command("SENS:AWG:STAT?") == "0"
