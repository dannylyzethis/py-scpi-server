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
    return SCPIInstrument("Virtual VNA 4 Port", "active-source", vna_capabilities=capabilities)


def numbers(response: str) -> tuple[float, ...]:
    return tuple(float(item) for item in response.split(","))


def test_active_hot_parameter_scenario_drives_normal_and_application_data() -> None:
    vna = instrument()
    vna.process_command("SENS:SWE:POIN 3")
    vna.attach_scenario(
        ScenarioDefinition(
            "active-source",
            (
                ScenarioStream(
                    "S11",
                    StreamKind.TRACE,
                    (ScenarioSample((0j, 0j, 0j)),),
                    end=EndPolicy.HOLD_LAST,
                ),
                ScenarioStream(
                    "active_hot_parameters.trace",
                    StreamKind.TRACE,
                    (ScenarioSample((1 + 2j, 3 + 4j, 5 + 6j)),),
                    end=EndPolicy.HOLD_LAST,
                ),
            ),
        )
    )

    assert vna.process_command("SENS:AHP:BIAS:VOLT 3.3") == ""
    assert vna.process_command("SENS:AHP:BIAS:CURR 0.125") == ""
    assert vna.process_command("SENS:AHP:STAT ON") == ""
    assert vna.process_command("SENS:AHP:BIAS:VOLT?") == "3.3"
    assert numbers(vna.process_command("CALC:AHP:DATA?")) == (1, 2, 3, 4, 5, 6)
    assert numbers(vna.process_command("CALC:DATA? SDAT")) == (1, 2, 3, 4, 5, 6)


def test_phase_and_true_mode_are_composable_trace_modifiers() -> None:
    vna = instrument()
    vna.process_command("SENS:SWE:POIN 2")
    measurement = vna.vna_measurements.selected(1)
    measurement.samples = (1 + 0j, 2 + 0j)

    for command in (
        "SOUR1:PHAS:ANGL 90",
        "SOUR1:PHAS:STAT ON",
        "SENS:TMST:MODE DIFF",
        "SENS:TMST:AMPL:RAT 2",
        "SENS:TMST:STAT ON",
    ):
        assert vna.process_command(command) == ""

    result = numbers(vna.process_command("CALC:DATA? SDAT"))
    assert result == pytest.approx((0.0, -2.0, 0.0, -4.0), abs=1e-12)
    assert vna.process_command("SOUR1:PHAS:ANGL?") == "90"
    assert vna.process_command("SENS:TMST:MODE?") == "DIFFerential"


def test_active_source_options_are_gated_and_source_count_is_validated() -> None:
    strict = instrument(applications=())
    for command in ("SENS:AHP:STAT?", "SOUR1:PHAS:STAT?", "SENS:TMST:STAT?"):
        assert strict.process_command(command) == ""
        assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')

    option_enabled = instrument()
    assert option_enabled.process_command("SOUR3:PHAS:STAT ON") == ""
    assert option_enabled.process_command("SYST:ERR?").startswith('-222,"Data out of range')


def test_active_source_state_survives_cls_and_resets_with_rst() -> None:
    vna = instrument()
    vna.process_command("SENS:AHP:STAT ON")
    vna.process_command("SOUR1:PHAS:STAT ON")
    vna.process_command("SENS:TMST:STAT ON")

    vna.process_command("*CLS")
    assert vna.process_command("SENS:AHP:STAT?") == "1"
    assert vna.process_command("SOUR1:PHAS:STAT?") == "1"
    assert vna.process_command("SENS:TMST:STAT?") == "1"

    vna.process_command("*RST")
    assert vna.process_command("SENS:AHP:STAT?") == "0"
    assert vna.process_command("SOUR1:PHAS:STAT?") == "0"
    assert vna.process_command("SENS:TMST:STAT?") == "0"
