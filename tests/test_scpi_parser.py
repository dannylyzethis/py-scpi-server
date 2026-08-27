from decimal import Decimal

import pytest

from scpi_emulator.scpi import (
    BinaryBlock,
    NumericValue,
    ParameterKind,
    SCPIParseError,
    mnemonic_matches,
    parse_program_message,
    split_program_message_units,
)


def test_raw_program_units_preserve_case_quotes_and_embedded_semicolons() -> None:
    assert split_program_message_units('LABEL "A;MixedCase";VALUE?') == (
        'LABEL "A;MixedCase"',
        "VALUE?",
    )


def test_common_query_and_message_terminator() -> None:
    message = parse_program_message("*idn?\r\n")

    assert len(message.commands) == 1
    command = message.commands[0]
    assert command.common is True
    assert command.query is True
    assert command.header == "*IDN?"
    assert command.parameters == ()


def test_indexed_absolute_header_and_numeric_unit() -> None:
    command = parse_program_message(":sens2:freq:start 1.5GHz").commands[0]

    assert command.absolute is True
    assert command.header == "SENS2:FREQ:START"
    assert [segment.mnemonic for segment in command.path] == ["SENS", "FREQ", "START"]
    assert [segment.suffix for segment in command.path] == [2, None, None]
    assert command.parameters[0].kind is ParameterKind.NUMERIC
    assert command.parameters[0].value == NumericValue(Decimal("1.5"), "GHZ")


def test_relative_headers_resolve_against_previous_parent() -> None:
    message = parse_program_message(":SENS1:FREQ:STAR 1e6;STOP 2.5E9;:SOUR1:POW -10dBm;LEV 2")

    assert [command.header for command in message.commands] == [
        "SENS1:FREQ:STAR",
        "SENS1:FREQ:STOP",
        "SOUR1:POW",
        "SOUR1:LEV",
    ]


def test_common_command_does_not_change_relative_header_context() -> None:
    message = parse_program_message(":SENS:FREQ:STAR 1;*OPC?;STOP 2")

    assert [command.header for command in message.commands] == [
        "SENS:FREQ:STAR",
        "*OPC?",
        "SENS:FREQ:STOP",
    ]


def test_parameter_list_preserves_character_case_and_parses_numbers() -> None:
    command = parse_program_message("FORM:DATA Real,64,-1.25e-3V").commands[0]

    assert [parameter.kind for parameter in command.parameters] == [
        ParameterKind.CHARACTER,
        ParameterKind.NUMERIC,
        ParameterKind.NUMERIC,
    ]
    assert command.parameters[0].value == "Real"
    assert command.parameters[1].value == NumericValue(Decimal("64"))
    assert command.parameters[2].value == NumericValue(Decimal("-1.25e-3"), "V")


def test_quoted_commas_and_semicolons_remain_in_one_string_parameter() -> None:
    message = parse_program_message('DISP:TEXT "Mixed;Case,Value";TEXT?')

    assert len(message.commands) == 2
    assert message.commands[0].parameters[0].kind is ParameterKind.STRING
    assert message.commands[0].parameters[0].value == "Mixed;Case,Value"
    assert message.commands[1].header == "DISP:TEXT?"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("TEXT 'It''s valid'", "It's valid"),
        ('TEXT "A ""quoted"" value"', 'A "quoted" value'),
        ('TEXT "µV"', "µV"),
    ],
)
def test_string_escaping_and_utf8(source: str, expected: str) -> None:
    parameter = parse_program_message(source).commands[0].parameters[0]
    assert parameter.kind is ParameterKind.STRING
    assert parameter.value == expected


def test_definite_binary_block_can_contain_delimiters_and_non_utf8_data() -> None:
    payload = b"a;,\xffb"
    block = b"#1" + str(len(payload)).encode("ascii") + payload
    message = parse_program_message(b"MMEM:DATA " + block + b";*OPC?")

    assert len(message.commands) == 2
    parameter = message.commands[0].parameters[0]
    assert parameter.kind is ParameterKind.BINARY_BLOCK
    assert parameter.value == BinaryBlock(payload)
    assert message.commands[1].header == "*OPC?"


def test_indefinite_binary_block_consumes_message_remainder() -> None:
    command = parse_program_message(b"MMEM:DATA #0abc;still,data").commands[0]

    assert len(command.parameters) == 1
    assert command.parameters[0].value == BinaryBlock(b"abc;still,data", indefinite=True)


@pytest.mark.parametrize(
    ("candidate", "specification", "matches"),
    [
        ("CALC", "CALCulate", True),
        ("CALCU", "CALCulate", True),
        ("CALCULATE", "CALCulate", True),
        ("CAL", "CALCulate", False),
        ("CULATE", "CALCulate", False),
        ("sense", "SENSe", True),
    ],
)
def test_mnemonic_abbreviation_matching(candidate: str, specification: str, matches: bool) -> None:
    assert mnemonic_matches(candidate, specification) is matches


@pytest.mark.parametrize(
    ("source", "error_text"),
    [
        ("", "program message is empty"),
        ("SENS::FREQ?", "invalid header segment"),
        ("SENS:FREQ??", "invalid program header"),
        ("SENS:FREQ 1,,2", "empty parameter"),
        ('DISP:TEXT "unterminated', "unterminated string parameter"),
        (b"MMEM:DATA #", "missing its length digit"),
        (b"MMEM:DATA #x", "length digit must be numeric"),
        (b"MMEM:DATA #212x", "payload is truncated"),
        ("*IDN?;", "empty command unit"),
    ],
)
def test_malformed_messages_raise_parse_errors(source: str | bytes, error_text: str) -> None:
    with pytest.raises(SCPIParseError, match=error_text):
        parse_program_message(source)
