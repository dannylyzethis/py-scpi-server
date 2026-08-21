import pytest

from scpi_emulator.emulator import SCPIInstrument


@pytest.fixture
def instrument() -> SCPIInstrument:
    device = SCPIInstrument("Test Instrument", "test_instrument")
    device.add_command("VOLT (.+)", "OK", "range:0,10")
    device.add_command("VOLT?", "5")
    device.add_command("MODE (.+)", "OK", "enum:AC,DC")
    device.add_command("MODE?", "DC")
    device.link_stateful_commands()
    return device


def test_standard_identity_and_version_are_available(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("*IDN?") == (
        "SCPI_Emulator,Test Instrument,test_instrument,2.3.0"
    )
    assert instrument.process_command("SYST:VERS?") == "1999.0"


def test_set_query_pair_persists_validated_state(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("VOLT 7.5") == "OK"
    assert instrument.process_command("VOLT?") == "7.5"
    assert instrument.process_command("mode ac") == "OK"
    assert instrument.process_command("MODE?") == "AC"


def test_validation_error_is_queued_and_drained_fifo(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("VOLT 11") == ""
    assert instrument.process_command("MODE INVALID") == ""

    first = instrument.process_command("SYST:ERR?")
    second = instrument.process_command("SYST:ERR?")

    assert first.startswith('-222,"Data out of range;')
    assert second.startswith('-108,"Parameter not allowed;')
    assert instrument.process_command("SYST:ERR?") == '0,"No error"'


def test_undefined_header_is_reported_through_error_queue(
    instrument: SCPIInstrument,
) -> None:
    assert instrument.process_command("NOT:A:COMMAND") == ""
    assert instrument.process_command("SYST:ERR?") == (
        '-113,"Undefined header; NOT:A:COMMAND"'
    )


def test_semicolon_chain_collects_query_responses(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("VOLT 2.5;VOLT?;SYST:VERS?") == "OK;2.5;1999.0"


def test_reset_restores_linked_query_defaults(instrument: SCPIInstrument) -> None:
    instrument.process_command("VOLT 2.5")
    assert instrument.process_command("VOLT?") == "2.5"

    assert instrument.process_command("*RST") == ""
    assert instrument.process_command("VOLT?") == "5"


def test_cls_clears_status_without_losing_values_or_command_responsiveness(
    instrument: SCPIInstrument,
) -> None:
    assert instrument.process_command("VOLT 7.5") == "OK"
    assert instrument.process_command("MODE AC") == "OK"
    instrument.state.update({"ese": 32, "sre": 4, "esr": 48, "stb": 36})
    assert instrument.process_command("NOT:A:COMMAND") == ""

    assert instrument.process_command("*CLS") == ""

    assert instrument.process_command("VOLT?") == "7.5"
    assert instrument.process_command("MODE?") == "AC"
    assert instrument.process_command("*IDN?").startswith("SCPI_Emulator,")
    assert instrument.process_command("SYST:ERR?") == '0,"No error"'
    assert instrument.state["ese"] == 32
    assert instrument.state["sre"] == 4
    assert "esr" not in instrument.state
    assert "stb" not in instrument.state


@pytest.mark.xfail(
    strict=True,
    reason="Legacy exact matching does not parse parameters for IEEE *ESE and *SRE setters",
)
def test_ieee_enable_register_setters_accept_parameters(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("*ESE 1") == ""
    assert instrument.process_command("*ESE?") == "1"


@pytest.mark.xfail(
    strict=True,
    reason="Legacy command processing uppercases string parameters before dispatch",
)
def test_string_parameter_case_is_preserved() -> None:
    instrument = SCPIInstrument("Test", "test")
    instrument.add_command("LABEL (.+)", "{value}")
    assert instrument.process_command("LABEL MixedCase") == "MixedCase"


@pytest.mark.xfail(
    strict=True,
    reason="Legacy chaining splits semicolons that occur inside quoted strings",
)
def test_semicolon_inside_quoted_parameter_is_not_a_command_separator() -> None:
    instrument = SCPIInstrument("Test", "test")
    instrument.add_command("LABEL (.+)", "{value}")
    assert instrument.process_command('LABEL "A;B"') == '"A;B"'
