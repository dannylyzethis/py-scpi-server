"""Isolated adapter for legacy CSV command/response catalogs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .scpi.errors import ErrorQueue
from .scpi.output import BinaryResponse

if TYPE_CHECKING:
    from .scenario import ScenarioPlayer


_SCENARIO_RESPONSE = re.compile(r"\A\{\{scenario:(?P<stream>.+?)\}\}\Z")


class CSVCommandAdapter:
    """Execute only commands declared by the legacy five-column CSV format."""

    def __init__(self, error_queue: ErrorQueue) -> None:
        self.error_queue = error_queue
        self.commands: dict[str, Any] = {}
        self.state: dict[str, str] = {}
        self.validation_rules: dict[str, str] = {}
        self.default_values: dict[str, str] = {}
        self.player: ScenarioPlayer | None = None

    def attach(self, player: ScenarioPlayer) -> None:
        """Attach the shared deterministic scenario player used by this instrument."""
        self.player = player

    def add_command(self, command: str, response: str, validation: str | None = None) -> None:
        command = command.strip().upper()
        if "(.+)" in command or "{VALUE}" in command:
            pattern = command.replace("{VALUE}", r"(.+)")
            if validation:
                self.validation_rules[pattern] = validation
            self.commands[pattern] = self._parameterized_response(response, validation)
        else:
            self.commands[command] = lambda resp=response: self._response(resp)

    def add_binary_query(self, command: str, data: bytes, *, definite: bool = True) -> None:
        command = command.strip().upper()
        if not command.endswith("?"):
            raise ValueError("binary response commands must be queries")
        payload = bytes(data)
        self.commands[command] = lambda: BinaryResponse(payload, definite=definite)

    def reset(self) -> None:
        self.state.clear()

    def link_stateful_commands(self) -> None:
        groups: dict[str, dict[str, str | None]] = {}
        for command in self.commands:
            if command.startswith("*") or command.startswith("SYST:"):
                continue
            if "(.+)" in command:
                base_name = command.replace(" (.+)", "").replace("(.+)", "")
                groups.setdefault(base_name, {})["set"] = command
                groups[base_name]["validation"] = self.validation_rules.get(command)
            elif command.endswith("?"):
                groups.setdefault(command[:-1], {})["query"] = command

        for base_name, group in groups.items():
            if "set" not in group or "query" not in group:
                continue
            set_command = str(group["set"])
            query_command = str(group["query"])
            validation = group.get("validation")
            default = self.default_values.get(base_name)
            if default is None:
                try:
                    default = str(self.commands[query_command]())
                except Exception:
                    default = "0"
                self.default_values[base_name] = default
            self.commands[set_command] = self._stateful_set(base_name, validation)
            self.commands[query_command] = self._stateful_query(base_name, default)

    def dispatch(self, command: str | bytes) -> tuple[bool, Any]:
        if isinstance(command, bytes):
            try:
                command = command.decode("utf-8")
            except UnicodeDecodeError:
                self.error_queue.push(-102, "binary data is not valid for a CSV command")
                return True, ""
        command = command.strip()
        normalized = command.upper()
        handler = self.commands.get(normalized)
        if handler is not None:
            return True, handler()
        for pattern, handler in self.commands.items():
            if "(" not in pattern:
                continue
            regex = "(.+)".join(re.escape(part) for part in pattern.split("(.+)"))
            match = re.fullmatch(regex, command, flags=re.IGNORECASE)
            if match:
                return True, handler(*match.groups())
        return False, ""

    def _parameterized_response(self, template: str, validation: str | None):
        def respond(*args: str):
            if args and validation:
                error = self._validate(args[0], validation)
                if error:
                    self.error_queue.push(error[0], error[1])
                    return ""
            response = template
            for index, argument in enumerate(args, 1):
                response = response.replace(f"{{param{index}}}", argument)
                response = response.replace("{value}", argument)
            return self._response(response)

        return respond

    def _response(self, template: str):
        match = _SCENARIO_RESPONSE.fullmatch(str(template))
        if match is None:
            return str(template)
        if self.player is None:
            return "0"
        return self.player.read(match.group("stream"))

    def _stateful_set(self, base_name: str, validation: str | None):
        def set_value(*args: str) -> str:
            if not args:
                return ""
            if validation:
                error = self._validate(args[0], validation)
                if error:
                    self.error_queue.push(error[0], error[1])
                    return ""
            value = args[0]
            if validation and (
                validation.startswith("enum:") or validation == "bool"
            ):
                value = value.upper()
            self.state[f"{base_name}_VALUE"] = value
            return "OK"

        return set_value

    def _stateful_query(self, base_name: str, default: str):
        return lambda: str(self.state.get(f"{base_name}_VALUE", default))

    @staticmethod
    def _validate(parameter: str, validation: str) -> tuple[int, str] | None:
        if validation.startswith("range:"):
            try:
                lower, upper = map(float, validation.split(":", 1)[1].split(","))
                value = float(parameter)
                if not lower <= value <= upper:
                    return -222, f"expected {lower} to {upper}, got {value}"
            except ValueError:
                return -104, f"cannot convert '{parameter}' to number"
        elif validation.startswith("enum:"):
            values = [value.strip().upper() for value in validation.split(":", 1)[1].split(",")]
            if parameter.upper() not in values:
                return -108, f"expected one of {values}, got '{parameter}'"
        elif validation == "bool" and parameter.upper() not in {"ON", "OFF", "1", "0"}:
            return -108, f"expected ON/OFF/1/0, got '{parameter}'"
        return None
