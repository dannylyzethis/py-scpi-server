from decimal import Decimal

import pytest

from scpi_emulator.scpi import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    NumericValue,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
    parse_program_message,
)


def command(source: str | bytes):
    return parse_program_message(source).commands[0]


def test_dispatch_matches_abbreviations_and_passes_typed_number_and_index() -> None:
    received = []
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            path=(
                HeaderNode("SENSe", index="channel", index_default=1),
                HeaderNode("FREQuency"),
                HeaderNode("STARt"),
            ),
            parameters=(
                ParameterSpec(
                    ParameterType.NUMBER,
                    "frequency",
                    minimum=Decimal("1"),
                    units=frozenset({"HZ", "KHZ", "MHZ", "GHZ"}),
                ),
            ),
            handler=lambda invocation, value: received.append(
                (invocation.indices["channel"], value)
            ),
        )
    )

    registry.dispatch(command("SENS2:FREQ:STAR 1.5GHz"))
    registry.dispatch(command("SENSE:FREQUENCY:START 2Hz"))

    assert received == [
        (2, NumericValue(Decimal("1.5"), "GHZ")),
        (1, NumericValue(Decimal("2"), "HZ")),
    ]


def test_query_and_event_forms_are_distinct() -> None:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            path=(HeaderNode("SOURce"), HeaderNode("POWer")),
            handler=lambda invocation, value: value,
            parameters=(ParameterSpec(ParameterType.NUMBER),),
        )
    )
    registry.register(
        CommandSpec(
            path=(HeaderNode("SOURce"), HeaderNode("POWer")),
            handler=lambda invocation: "-10",
            query=True,
        )
    )

    assert registry.dispatch(command("SOUR:POW?")) == "-10"
    assert registry.dispatch(command("SOUR:POW -12")) == NumericValue(Decimal("-12"))


def test_common_command_registration() -> None:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            path=(HeaderNode("*IDN"),),
            handler=lambda invocation: "Vendor,Model,Serial,Firmware",
            query=True,
            common=True,
        )
    )

    assert registry.dispatch(command("*idn?")) == "Vendor,Model,Serial,Firmware"


def test_parameter_types_defaults_and_canonical_enum_values() -> None:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            path=(HeaderNode("CONFigure"),),
            parameters=(
                ParameterSpec(ParameterType.ENUM, "mode", choices=("Network", "Spectrum")),
                ParameterSpec(ParameterType.BOOLEAN, "enabled"),
                ParameterSpec(ParameterType.INTEGER, "count", minimum=1, maximum=10),
                ParameterSpec(ParameterType.STRING, "label", required=False, default="Trace"),
            ),
            handler=lambda invocation, *values: values,
        )
    )

    assert registry.dispatch(command("CONF network,ON,4")) == (
        "Network",
        True,
        4,
        "Trace",
    )
    assert registry.dispatch(command('CONFIGURE spectrum,0,1,"Mixed Case"')) == (
        "Spectrum",
        False,
        1,
        "Mixed Case",
    )


def test_binary_handler_receives_payload_bytes() -> None:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            path=(HeaderNode("MMEMory"), HeaderNode("DATA")),
            parameters=(ParameterSpec(ParameterType.BINARY, "data"),),
            handler=lambda invocation, data: data,
        )
    )

    assert registry.dispatch(command(b"MMEM:DATA #14a;,\xff")) == b"a;,\xff"


def test_required_capability_and_predicate_guard_commands() -> None:
    specification = CommandSpec(
        path=(HeaderNode("SENSe", index="channel"), HeaderNode("NOISe")),
        handler=lambda invocation: invocation.indices["channel"],
        query=True,
        required_capabilities=frozenset({"noise-figure"}),
        available=lambda invocation: invocation.indices["channel"] <= 4,
    )
    enabled = CommandRegistry({"noise-figure"})
    enabled.register(specification)
    disabled = CommandRegistry()
    disabled.register(specification)

    assert enabled.dispatch(command("SENS4:NOIS?")) == 4
    for registry, source in ((enabled, "SENS5:NOIS?"), (disabled, "SENS1:NOIS?")):
        with pytest.raises(SCPICommandError) as error:
            registry.dispatch(command(source))
        assert error.value.code == -113
        assert "Command unavailable" in error.value.message


def test_existence_predicate_rejects_address_before_handler() -> None:
    calls = []
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            path=(
                HeaderNode("CALCulate", index="channel", index_default=1),
                HeaderNode("DATA"),
            ),
            handler=lambda invocation: calls.append(invocation.indices["channel"]),
            query=True,
            exists=lambda invocation: invocation.indices["channel"] in {1, 3},
        )
    )

    assert registry.dispatch(command("CALC3:DATA?")) is None
    with pytest.raises(SCPICommandError, match="addressed object does not exist") as error:
        registry.dispatch(command("CALC2:DATA?"))
    assert error.value.code == -200
    assert calls == [3]


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("SOUR:POW", -109),
        ("SOUR:POW 1,2", -108),
        ('SOUR:POW "high"', -104),
        ("SOUR:POW 11", -222),
        ("SOUR:POW 2V", -224),
    ],
)
def test_invalid_parameters_have_deterministic_scpi_errors(source: str, code: int) -> None:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            path=(HeaderNode("SOURce"), HeaderNode("POWer")),
            parameters=(
                ParameterSpec(
                    ParameterType.NUMBER,
                    "power",
                    minimum=Decimal("-100"),
                    maximum=Decimal("10"),
                    units=frozenset({"DBM"}),
                ),
            ),
            handler=lambda invocation, value: value,
        )
    )

    with pytest.raises(SCPICommandError) as error:
        registry.dispatch(command(source))
    assert error.value.code == code
    assert error.value.response.startswith(f'{code},"')


def test_unknown_and_wrong_query_form_are_undefined_headers() -> None:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(path=(HeaderNode("ABORt"),), handler=lambda invocation: None)
    )

    for source in ("NOT:A:COMMAND", "ABOR?"):
        with pytest.raises(SCPICommandError) as error:
            registry.dispatch(command(source))
        assert error.value.code == -113
        assert "Undefined header" in error.value.message


def test_invalid_specifications_are_rejected_at_registration_time() -> None:
    with pytest.raises(ValueError, match="required parameters"):
        CommandSpec(
            path=(HeaderNode("TEST"),),
            handler=lambda invocation, *values: values,
            parameters=(
                ParameterSpec(ParameterType.STRING, required=False),
                ParameterSpec(ParameterType.STRING),
            ),
        )

    with pytest.raises(ValueError, match="enum parameters"):
        ParameterSpec(ParameterType.ENUM)
