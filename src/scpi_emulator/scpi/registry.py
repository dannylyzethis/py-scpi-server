"""Typed SCPI command registration, matching, and argument validation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from .parser import (
    BinaryBlock,
    Command,
    NumericValue,
    Parameter,
    ParameterKind,
    mnemonic_matches,
)


class SCPICommandError(Exception):
    """A deterministic SCPI command or execution error."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f'{code},"{message}"')

    @property
    def response(self) -> str:
        return str(self)


class ParameterType(Enum):
    STRING = "string"
    CHARACTER = "character"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"
    BINARY = "binary"


@dataclass(frozen=True)
class ParameterSpec:
    """The expected type and constraints for one command parameter."""

    type: ParameterType
    name: str = "parameter"
    required: bool = True
    default: Any = None
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None
    choices: tuple[str, ...] = ()
    units: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        normalized_units = frozenset(unit.upper() for unit in self.units)
        object.__setattr__(self, "units", normalized_units)
        if self.type is ParameterType.ENUM and not self.choices:
            raise ValueError("enum parameters require at least one choice")
        if self.minimum is not None and self.maximum is not None:
            if Decimal(self.minimum) > Decimal(self.maximum):
                raise ValueError("parameter minimum cannot exceed maximum")


@dataclass(frozen=True)
class HeaderNode:
    """One mixed-case SCPI mnemonic and its optional numeric suffix binding."""

    mnemonic: str
    index: str | None = None
    index_default: int | None = None

    def __post_init__(self) -> None:
        if self.index_default is not None and self.index is None:
            raise ValueError("an index default requires an index binding name")
        if self.mnemonic.startswith("*"):
            valid = self.mnemonic[1:].isalpha()
        else:
            valid = self.mnemonic.isalpha()
        if not valid:
            raise ValueError(f"invalid command mnemonic specification: {self.mnemonic!r}")


@dataclass(frozen=True)
class Invocation:
    """Context made available to capability predicates and command handlers."""

    command: Command
    indices: Mapping[str, int]
    capabilities: frozenset[str]


Handler = Callable[..., Any]
AvailabilityPredicate = Callable[[Invocation], bool]


@dataclass(frozen=True)
class CommandSpec:
    """A typed command header, parameter list, handler, and availability rule."""

    path: tuple[HeaderNode, ...]
    handler: Handler
    parameters: tuple[ParameterSpec, ...] = ()
    query: bool = False
    common: bool = False
    available: AvailabilityPredicate | None = None
    exists: AvailabilityPredicate | None = None
    required_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("command path cannot be empty")
        if self.common:
            if len(self.path) != 1 or not self.path[0].mnemonic.startswith("*"):
                raise ValueError("a common command must have one '*' header node")
        elif any(node.mnemonic.startswith("*") for node in self.path):
            raise ValueError("subsystem command nodes cannot start with '*'")
        optional_seen = False
        for parameter in self.parameters:
            if not parameter.required:
                optional_seen = True
            elif optional_seen:
                raise ValueError("required parameters cannot follow optional parameters")


@dataclass(frozen=True)
class ResolvedCommand:
    specification: CommandSpec
    invocation: Invocation


@dataclass
class CommandRegistry:
    """Registry that resolves parsed SCPI commands without executable regexes."""

    capabilities: frozenset[str] = frozenset()
    _specifications: list[CommandSpec] = field(default_factory=list, init=False)

    def __init__(self, capabilities: Iterable[str] = ()) -> None:
        self.capabilities = frozenset(capabilities)
        self._specifications = []

    def register(self, specification: CommandSpec) -> CommandSpec:
        if specification in self._specifications:
            raise ValueError("command specification is already registered")
        self._specifications.append(specification)
        return specification

    @property
    def specifications(self) -> tuple[CommandSpec, ...]:
        """Return an immutable view used by diagnostics and coverage tooling."""
        return tuple(self._specifications)

    def resolve(self, command: Command) -> ResolvedCommand:
        matches: list[ResolvedCommand] = []
        unavailable = False
        nonexistent = False
        for specification in self._specifications:
            indices = _match_header(command, specification)
            if indices is None:
                continue
            invocation = Invocation(command, indices, self.capabilities)
            if not specification.required_capabilities.issubset(self.capabilities):
                unavailable = True
                continue
            if specification.available is not None and not specification.available(invocation):
                unavailable = True
                continue
            if specification.exists is not None and not specification.exists(invocation):
                nonexistent = True
                continue
            matches.append(ResolvedCommand(specification, invocation))

        if len(matches) > 1:
            raise RuntimeError(f"ambiguous command registry match for {command.header}")
        if not matches:
            if nonexistent:
                raise SCPICommandError(
                    -200, f"Execution error; addressed object does not exist; {command.header}"
                )
            detail = "Command unavailable" if unavailable else "Undefined header"
            raise SCPICommandError(-113, f"{detail}; {command.header}")
        return matches[0]

    def dispatch(self, command: Command) -> Any:
        resolved = self.resolve(command)
        arguments = _coerce_parameters(command.parameters, resolved.specification.parameters)
        return resolved.specification.handler(resolved.invocation, *arguments)


def _match_header(command: Command, specification: CommandSpec) -> dict[str, int] | None:
    if command.query is not specification.query or command.common is not specification.common:
        return None
    if len(command.path) != len(specification.path):
        return None

    indices: dict[str, int] = {}
    for actual, expected in zip(command.path, specification.path):
        if specification.common:
            if actual.mnemonic.upper() != expected.mnemonic.upper():
                return None
        elif not mnemonic_matches(actual.mnemonic, expected.mnemonic):
            return None
        if expected.index is None:
            if actual.suffix is not None:
                return None
            continue
        if actual.suffix is None:
            if expected.index_default is None:
                return None
            indices[expected.index] = expected.index_default
        else:
            indices[expected.index] = actual.suffix
    return indices


def _coerce_parameters(
    supplied: tuple[Parameter, ...], expected: tuple[ParameterSpec, ...]
) -> tuple[Any, ...]:
    required_count = sum(parameter.required for parameter in expected)
    if len(supplied) < required_count:
        raise SCPICommandError(-109, "Missing parameter")
    if len(supplied) > len(expected):
        raise SCPICommandError(-108, "Parameter not allowed")

    values: list[Any] = []
    for index, specification in enumerate(expected):
        if index >= len(supplied):
            values.append(specification.default)
            continue
        values.append(_coerce_parameter(supplied[index], specification))
    return tuple(values)


def _coerce_parameter(parameter: Parameter, specification: ParameterSpec) -> Any:
    kind = specification.type
    if kind is ParameterType.STRING:
        if parameter.kind is not ParameterKind.STRING:
            raise _type_error(specification, "quoted string")
        return parameter.value
    if kind is ParameterType.CHARACTER:
        if parameter.kind is not ParameterKind.CHARACTER:
            raise _type_error(specification, "character data")
        return parameter.value
    if kind is ParameterType.ENUM:
        if parameter.kind is not ParameterKind.CHARACTER:
            raise _type_error(specification, "character data")
        value = str(parameter.value)
        for choice in specification.choices:
            if value.upper() == choice.upper() or (
                choice.isalpha() and mnemonic_matches(value, choice)
            ):
                return choice
        raise SCPICommandError(-224, f"Illegal parameter value; {specification.name}")
    if kind is ParameterType.BINARY:
        if parameter.kind is not ParameterKind.BINARY_BLOCK:
            raise _type_error(specification, "binary block")
        assert isinstance(parameter.value, BinaryBlock)
        return parameter.value.data
    if kind is ParameterType.BOOLEAN:
        return _coerce_boolean(parameter, specification)
    if kind in (ParameterType.NUMBER, ParameterType.INTEGER):
        if parameter.kind is not ParameterKind.NUMERIC:
            raise _type_error(specification, "numeric data")
        assert isinstance(parameter.value, NumericValue)
        _validate_unit(parameter.value, specification)
        value = parameter.value.value
        if kind is ParameterType.INTEGER:
            if value != value.to_integral_value():
                raise _type_error(specification, "integer data")
            converted: Any = int(value)
        else:
            converted = parameter.value
        _validate_range(value, specification)
        return converted
    raise AssertionError(f"unsupported parameter type: {kind}")


def _coerce_boolean(parameter: Parameter, specification: ParameterSpec) -> bool:
    if parameter.kind is ParameterKind.CHARACTER:
        value = str(parameter.value).upper()
        if value in ("ON", "TRUE"):
            return True
        if value in ("OFF", "FALSE"):
            return False
    elif parameter.kind is ParameterKind.NUMERIC:
        assert isinstance(parameter.value, NumericValue)
        if parameter.value.unit is None and parameter.value.value in (Decimal(0), Decimal(1)):
            return bool(parameter.value.value)
    raise _type_error(specification, "boolean data")


def _validate_unit(value: NumericValue, specification: ParameterSpec) -> None:
    if value.unit is None:
        return
    if not specification.units or value.unit not in specification.units:
        raise SCPICommandError(-224, f"Illegal parameter value; {specification.name} unit")


def _validate_range(value: Decimal, specification: ParameterSpec) -> None:
    if specification.minimum is not None and value < Decimal(specification.minimum):
        raise SCPICommandError(-222, f"Data out of range; {specification.name}")
    if specification.maximum is not None and value > Decimal(specification.maximum):
        raise SCPICommandError(-222, f"Data out of range; {specification.name}")


def _type_error(specification: ParameterSpec, expected: str) -> SCPICommandError:
    return SCPICommandError(-104, f"Data type error; {specification.name} requires {expected}")
