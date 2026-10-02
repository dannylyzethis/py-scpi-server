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
    return SCPIInstrument("Virtual VNA 4 Port", "analysis-apps", vna_capabilities=capabilities)


def numbers(response: str) -> tuple[float, ...]:
    return tuple(float(value) for value in response.split(","))


def test_uncertainty_uses_scenario_and_fallback_measurement() -> None:
    vna = instrument()
    vna.process_command("SENS:SWE:POIN 3")
    vna.attach_scenario(
        ScenarioDefinition(
            "analysis",
            (
                ScenarioStream(
                    "measurement_uncertainty.trace",
                    StreamKind.TRACE,
                    (ScenarioSample((0.1, 0.2, 0.3)),),
                    end=EndPolicy.HOLD_LAST,
                ),
            ),
        )
    )
    vna.process_command("SENS:UNC:CONF 99")
    vna.process_command("SENS:UNC:FLOO 0.005")
    vna.process_command("SENS:UNC:STAT ON")
    assert numbers(vna.process_command("CALC:UNC:DATA?")) == (0.1, 0.2, 0.3)
    assert vna.process_command("SENS:UNC:CONF?") == "99"


def test_performance_test_reports_data_and_limit_result() -> None:
    vna = instrument()
    vna.process_command("SENS:SWE:POIN 3")
    vna.attach_scenario(
        ScenarioDefinition(
            "performance",
            (
                ScenarioStream(
                    "performance_test.trace",
                    StreamKind.TRACE,
                    (ScenarioSample((-1.0, 0.0, 1.0)),),
                    end=EndPolicy.HOLD_LAST,
                ),
            ),
        )
    )
    for command in ("SENS:PERF:LIM:LOW -2", "SENS:PERF:LIM:UPP 2", "SENS:PERF:STAT ON"):
        assert vna.process_command(command) == ""
    assert numbers(vna.process_command("CALC:PERF:DATA?")) == (-1, 0, 1)
    assert vna.process_command("CALC:PERF:PASS?") == "1"
    vna.process_command("SENS:PERF:LIM:UPP 0.5")
    assert vna.process_command("CALC:PERF:PASS?") == "0"


def test_analysis_options_are_gated_and_require_enable() -> None:
    strict = instrument(applications=())
    for command in ("SENS:UNC:STAT?", "SENS:PERF:STAT?"):
        assert strict.process_command(command) == ""
        assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')
    licensed = instrument()
    assert licensed.process_command("CALC:UNC:DATA?") == ""
    assert licensed.process_command("SYST:ERR?").startswith('-221,"Settings conflict')


def test_analysis_state_survives_cls_and_resets_with_rst() -> None:
    vna = instrument()
    vna.process_command("SENS:UNC:STAT ON")
    vna.process_command("SENS:PERF:STAT ON")
    vna.process_command("*CLS")
    assert vna.process_command("SENS:UNC:STAT?") == "1"
    assert vna.process_command("SENS:PERF:STAT?") == "1"
    vna.process_command("*RST")
    assert vna.process_command("SENS:UNC:STAT?") == "0"
    assert vna.process_command("SENS:PERF:STAT?") == "0"
