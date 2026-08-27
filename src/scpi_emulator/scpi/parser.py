"""SCPI program-message tokenizer and parser.

The parser is deliberately independent from command dispatch. It preserves program data while
resolving SCPI subsystem paths, so the command registry can apply model-specific types and
capability rules later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class SCPIParseError(ValueError):
    """A malformed SCPI program message."""

    def __init__(self, message: str, offset: int | None = None) -> None:
        self.offset = offset
        suffix = f" at byte {offset}" if offset is not None else ""
        super().__init__(f"{message}{suffix}")


class ParameterKind(Enum):
    STRING = "string"
    NUMERIC = "numeric"
    CHARACTER = "character"
    BINARY_BLOCK = "binary_block"


@dataclass(frozen=True)
class NumericValue:
    value: Decimal
    unit: str | None = None


@dataclass(frozen=True)
class BinaryBlock:
    data: bytes
    indefinite: bool = False


@dataclass(frozen=True)
class Parameter:
    raw: bytes
    kind: ParameterKind
    value: str | NumericValue | BinaryBlock


@dataclass(frozen=True)
class HeaderSegment:
    mnemonic: str
    suffix: int | None = None

    @property
    def text(self) -> str:
        suffix = "" if self.suffix is None else str(self.suffix)
        return f"{self.mnemonic}{suffix}"


@dataclass(frozen=True)
class Command:
    path: tuple[HeaderSegment, ...]
    parameters: tuple[Parameter, ...]
    query: bool = False
    common: bool = False
    absolute: bool = False

    @property
    def header(self) -> str:
        if self.common:
            value = self.path[0].mnemonic
        else:
            value = ":".join(segment.text for segment in self.path)
        return f"{value}{'?' if self.query else ''}"


@dataclass(frozen=True)
class ProgramMessage:
    commands: tuple[Command, ...]


_HEADER_SEGMENT = re.compile(r"^(?P<mnemonic>[A-Za-z]+)(?P<suffix>[0-9]*)$")
_COMMON_HEADER = re.compile(r"^(?P<mnemonic>\*[A-Za-z]+)(?P<query>\?)?$")
_NUMERIC = re.compile(
    r"^(?P<number>[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))"
    r"(?:[Ee][+-]?[0-9]+)?)(?P<unit>[A-Za-z][A-Za-z0-9/_]*)?$"
)


def mnemonic_matches(candidate: str, specification: str) -> bool:
    """Return whether a header mnemonic matches mixed-case SCPI specification syntax.

    ``CALC`` and ``CALCULATE`` both match ``CALCulate``; shorter or non-prefix forms do not.
    """
    if not candidate or not specification or not specification[0].isalpha():
        return False
    mandatory_length = 0
    for character in specification:
        if not character.isupper():
            break
        mandatory_length += 1
    if mandatory_length == 0:
        mandatory_length = len(specification)

    candidate_upper = candidate.upper()
    specification_upper = specification.upper()
    return mandatory_length <= len(candidate_upper) <= len(
        specification_upper
    ) and specification_upper.startswith(candidate_upper)


def parse_program_message(message: str | bytes) -> ProgramMessage:
    """Parse one SCPI program message and resolve relative subsystem headers."""
    data = message.encode("utf-8") if isinstance(message, str) else bytes(message)
    data = _remove_terminator(data)
    if not _trim(data):
        raise SCPIParseError("program message is empty")

    units = _split_outside_data(data, ord(";"))
    commands: list[Command] = []
    context: tuple[HeaderSegment, ...] = ()

    for unit_index, unit in enumerate(units):
        unit = _trim(unit)
        if not unit:
            raise SCPIParseError("empty command unit")

        header_bytes, parameter_bytes = _split_header_and_parameters(unit)
        segments, query, common, absolute = _parse_header(header_bytes)
        if common:
            path = segments
        elif absolute or unit_index == 0 or not context:
            path = segments
            context = path
        else:
            path = (*context[:-1], *segments)
            context = path

        parameters = _parse_parameters(parameter_bytes)
        commands.append(
            Command(
                path=path,
                parameters=parameters,
                query=query,
                common=common,
                absolute=absolute,
            )
        )

    return ProgramMessage(tuple(commands))


def split_program_message_units(message: str | bytes) -> tuple[str | bytes, ...]:
    """Split outside quoted strings and binary blocks while preserving raw data."""
    text_input = isinstance(message, str)
    data = message.encode("utf-8") if text_input else bytes(message)
    data = _remove_terminator(data)
    if not _trim(data):
        raise SCPIParseError("program message is empty")
    units = tuple(_trim(unit) for unit in _split_outside_data(data, ord(";")))
    if any(not unit for unit in units):
        raise SCPIParseError("empty command unit")
    if not text_input:
        return units
    try:
        return tuple(unit.decode("utf-8") for unit in units)
    except UnicodeDecodeError as error:
        raise SCPIParseError("program message is not valid UTF-8", error.start) from error


def _remove_terminator(data: bytes) -> bytes:
    if data.endswith(b"\r\n"):
        return data[:-2]
    if data.endswith((b"\n", b"\r")):
        return data[:-1]
    return data


def _trim(data: bytes) -> bytes:
    return data.strip(b" \t")


def _split_header_and_parameters(unit: bytes) -> tuple[bytes, bytes | None]:
    for index, value in enumerate(unit):
        if value in (ord(" "), ord("\t")):
            header = unit[:index]
            parameters = _trim(unit[index:])
            return header, parameters or None
    return unit, None


def _parse_header(
    header: bytes,
) -> tuple[tuple[HeaderSegment, ...], bool, bool, bool]:
    try:
        text = header.decode("ascii")
    except UnicodeDecodeError as error:
        raise SCPIParseError("header must contain ASCII characters") from error

    common_match = _COMMON_HEADER.fullmatch(text)
    if common_match:
        segment = HeaderSegment(common_match.group("mnemonic").upper())
        return (segment,), bool(common_match.group("query")), True, False

    absolute = text.startswith(":")
    if absolute:
        text = text[1:]
    query = text.endswith("?")
    if query:
        text = text[:-1]
    if not text or "?" in text or "*" in text:
        raise SCPIParseError("invalid program header")

    segments: list[HeaderSegment] = []
    for raw_segment in text.split(":"):
        match = _HEADER_SEGMENT.fullmatch(raw_segment)
        if not match:
            raise SCPIParseError(f"invalid header segment '{raw_segment}'")
        suffix_text = match.group("suffix")
        segments.append(
            HeaderSegment(
                mnemonic=match.group("mnemonic").upper(),
                suffix=int(suffix_text) if suffix_text else None,
            )
        )
    return tuple(segments), query, False, absolute


def _parse_parameters(data: bytes | None) -> tuple[Parameter, ...]:
    if data is None:
        return ()
    parts = _split_outside_data(data, ord(","))
    parameters = []
    for part in parts:
        part = _trim(part)
        if not part:
            raise SCPIParseError("empty parameter")
        parameters.append(_parse_parameter(part))
    return tuple(parameters)


def _parse_parameter(raw: bytes) -> Parameter:
    if raw[0] in (ord("'"), ord('"')):
        value = _parse_string(raw)
        return Parameter(raw=raw, kind=ParameterKind.STRING, value=value)
    if raw.startswith(b"#"):
        end = _binary_block_end(raw, 0)
        if end != len(raw):
            raise SCPIParseError("unexpected data after binary block", end)
        digit_count = raw[1] - ord("0")
        if digit_count == 0:
            block = BinaryBlock(data=raw[2:], indefinite=True)
        else:
            payload_start = 2 + digit_count
            block = BinaryBlock(data=raw[payload_start:], indefinite=False)
        return Parameter(raw=raw, kind=ParameterKind.BINARY_BLOCK, value=block)

    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise SCPIParseError("unquoted parameters must contain ASCII characters") from error
    numeric_match = _NUMERIC.fullmatch(text)
    if numeric_match:
        try:
            value = Decimal(numeric_match.group("number"))
        except InvalidOperation as error:
            raise SCPIParseError("invalid numeric parameter") from error
        unit = numeric_match.group("unit")
        numeric = NumericValue(value=value, unit=unit.upper() if unit else None)
        return Parameter(raw=raw, kind=ParameterKind.NUMERIC, value=numeric)
    return Parameter(raw=raw, kind=ParameterKind.CHARACTER, value=text)


def _parse_string(raw: bytes) -> str:
    quote = raw[0]
    if len(raw) < 2 or raw[-1] != quote:
        raise SCPIParseError("unterminated string parameter")
    output = bytearray()
    index = 1
    while index < len(raw) - 1:
        value = raw[index]
        if value == quote:
            if index + 1 < len(raw) - 1 and raw[index + 1] == quote:
                output.append(quote)
                index += 2
                continue
            raise SCPIParseError("unescaped quote inside string parameter", index)
        output.append(value)
        index += 1
    try:
        return bytes(output).decode("utf-8")
    except UnicodeDecodeError as error:
        raise SCPIParseError("string parameter is not valid UTF-8") from error


def _split_outside_data(data: bytes, separator: int) -> list[bytes]:
    parts: list[bytes] = []
    start = 0
    index = 0
    quote: int | None = None

    while index < len(data):
        value = data[index]
        if quote is not None:
            if value == quote:
                if index + 1 < len(data) and data[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if value in (ord("'"), ord('"')):
            quote = value
            index += 1
            continue
        if value == ord("#"):
            index = _binary_block_end(data, index)
            continue
        if value == separator:
            parts.append(data[start:index])
            start = index + 1
        index += 1

    if quote is not None:
        raise SCPIParseError("unterminated string parameter")
    parts.append(data[start:])
    return parts


def _binary_block_end(data: bytes, start: int) -> int:
    if start + 1 >= len(data):
        raise SCPIParseError("binary block is missing its length digit", start)
    length_digit = data[start + 1]
    if not ord("0") <= length_digit <= ord("9"):
        raise SCPIParseError("binary block length digit must be numeric", start + 1)
    digit_count = length_digit - ord("0")
    if digit_count == 0:
        return len(data)

    length_start = start + 2
    length_end = length_start + digit_count
    if length_end > len(data):
        raise SCPIParseError("binary block length field is truncated", length_start)
    length_bytes = data[length_start:length_end]
    if not length_bytes.isdigit():
        raise SCPIParseError("binary block length field must be numeric", length_start)
    payload_length = int(length_bytes)
    payload_end = length_end + payload_length
    if payload_end > len(data):
        raise SCPIParseError("binary block payload is truncated", length_end)
    return payload_end
