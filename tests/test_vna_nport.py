from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scenario import (
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import VNACapabilities


def instrument(model: str, *, applications=("all",)) -> SCPIInstrument:
    capabilities = VNACapabilities.create(model, applications=applications)
    return SCPIInstrument(f"Virtual {model}", "n-port", vna_capabilities=capabilities)


def numbers(response: str) -> tuple[float, ...]:
    return tuple(float(value) for value in response.split(","))


def test_four_port_measurement_creation_and_scenario_data() -> None:
    vna = instrument("vna-4-port")
    vna.process_command("SENS:SWE:POIN 2")
    vna.attach_scenario(
        ScenarioDefinition(
            "multiport",
            (
                ScenarioStream(
                    "S41",
                    StreamKind.TRACE,
                    (ScenarioSample((1 + 2j, 3 + 4j)),),
                    end=EndPolicy.HOLD_LAST,
                ),
            ),
        )
    )
    assert vna.process_command("SENS:NPORT:STAT ON") == ""
    assert vna.process_command('CALC:NPORT:DEF "Forward41",4,1') == ""
    assert vna.process_command("CALC:PAR:CAT?") == '"CH1_S11_1,S11,Forward41,S41"'
    assert numbers(vna.process_command("CALC:NPORT:DATA? 4,1")) == (1, 2, 3, 4)


def test_port_validation_follows_configured_instrument_shape() -> None:
    two_port = instrument("vna-2-port")
    two_port.process_command("SENS:NPORT:STAT ON")
    assert two_port.process_command('CALC:NPORT:DEF "Invalid",3,1') == ""
    assert two_port.process_command("SYST:ERR?").startswith('-222,"Data out of range')
    four_port = instrument("vna-4-port")
    four_port.process_command("SENS:NPORT:STAT ON")
    assert four_port.process_command('CALC:NPORT:DEF "Valid",3,1') == ""
    assert four_port.process_command("SYST:ERR?") == '0,"No error"'


def test_nport_is_option_gated_and_must_be_enabled() -> None:
    strict = instrument("vna-4-port", applications=())
    assert strict.process_command("SENS:NPORT:STAT?") == ""
    assert strict.process_command("SYST:ERR?").startswith('-113,"Command unavailable')
    licensed = instrument("vna-4-port")
    assert licensed.process_command("CALC:NPORT:DATA? 1,1") == ""
    assert licensed.process_command("SYST:ERR?").startswith('-221,"Settings conflict')


def test_nport_state_survives_cls_and_resets_with_rst() -> None:
    vna = instrument("vna-4-port")
    vna.process_command("SENS:NPORT:STAT ON")
    vna.process_command("*CLS")
    assert vna.process_command("SENS:NPORT:STAT?") == "1"
    vna.process_command("*RST")
    assert vna.process_command("SENS:NPORT:STAT?") == "0"
