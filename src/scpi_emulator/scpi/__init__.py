"""SCPI language primitives."""

from .parser import (
    BinaryBlock,
    Command,
    HeaderSegment,
    NumericValue,
    Parameter,
    ParameterKind,
    ProgramMessage,
    SCPIParseError,
    mnemonic_matches,
    parse_program_message,
)
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    Invocation,
    ParameterSpec,
    ParameterType,
    ResolvedCommand,
    SCPICommandError,
)

__all__ = [
    "BinaryBlock",
    "Command",
    "CommandRegistry",
    "CommandSpec",
    "HeaderSegment",
    "HeaderNode",
    "Invocation",
    "NumericValue",
    "Parameter",
    "ParameterKind",
    "ParameterSpec",
    "ParameterType",
    "ProgramMessage",
    "SCPIParseError",
    "ResolvedCommand",
    "SCPICommandError",
    "mnemonic_matches",
    "parse_program_message",
]
