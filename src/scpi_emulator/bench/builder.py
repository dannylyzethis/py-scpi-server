"""Beginner-oriented interactive builder over the driver catalog contract."""

from __future__ import annotations

import math
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from scpi_emulator.drivers import (
    ConfigurationFieldType,
    DriverCatalog,
    SupportLevel,
)

from .codec import save_bench
from .compose import BenchComposer, ComposedBench
from .model import BenchDefinition, BenchInstrument, ResourceAddress


class BenchBuildCancelled(Exception):
    """Raised when the user cancels without creating or replacing a file."""


class GuidedBenchBuilder:
    """Collect one validated schema-version-2 bench with low-knowledge defaults."""

    def __init__(
        self,
        catalog: DriverCatalog,
        *,
        input_fn: Callable[[str], str] | None = None,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.input = input_fn or input
        self.output = output_fn or print

    def build_and_save(self, path: str | Path) -> ComposedBench:
        target = Path(path).resolve()
        if target.suffix.casefold() != ".json":
            raise ValueError("bench file must use the .json extension")
        if not target.parent.is_dir():
            raise ValueError(f"bench folder does not exist: {target.parent}")
        default_name = target.stem or "virtual-bench"
        name = self._ask(f"Bench name [{default_name}]: ") or default_name
        instruments: list[BenchInstrument] = []
        next_port = 5025
        while True:
            instrument, next_port = self._instrument(len(instruments) + 1, instruments, next_port)
            instruments.append(instrument)
            if not self._yes("Add another instrument? [y/N]: ", default=False):
                break

        definition = BenchDefinition(name=name, instruments=tuple(instruments))
        composed = BenchComposer(self.catalog).compose(definition)
        self.output("Bench preview:")
        for instrument_id, resource in composed.resources().items():
            self.output(f"  {instrument_id}: {resource}")
        if target.exists() and not self._yes(f"Replace {target}? [y/N]: ", default=False):
            raise BenchBuildCancelled("existing bench was not replaced")
        if not self._yes(f"Save bench to {target}? [Y/n]: ", default=True):
            raise BenchBuildCancelled("bench was not saved")
        _atomic_save(definition, target)
        return composed

    def _instrument(
        self,
        ordinal: int,
        existing: list[BenchInstrument],
        next_port: int,
    ) -> tuple[BenchInstrument, int]:
        driver = self._select(
            "driver",
            self.catalog.descriptors,
            lambda item: f"{item.id} - {item.display_name}",
            lambda item: item.id,
        )
        model = self._select(
            "model",
            driver.models,
            lambda item: f"{item.model} - {item.display_name}",
            lambda item: item.model,
        )
        default_id = f"{_slug(model.model)}_{ordinal}"
        existing_ids = {item.id.casefold() for item in existing}
        while True:
            instrument_id = self._ask(f"Instance ID [{default_id}]: ") or default_id
            if instrument_id.casefold() not in existing_ids:
                break
            self.output(f"[ERROR] Instance ID {instrument_id!r} is already used.")
        name = self._ask(f"Display name [{model.display_name}]: ") or model.display_name
        default_serial = f"EMU-{instrument_id.upper()}"
        existing_serials = {
            item.serial_number.casefold() for item in existing if item.serial_number is not None
        }
        while True:
            serial = self._ask(f"Serial number [{default_serial}]: ") or default_serial
            if serial.casefold() not in existing_serials:
                break
            self.output(f"[ERROR] Serial number {serial!r} is already used.")
        reported_model = (
            self._ask(f"Reported model [driver default: {model.display_name}]: ") or None
        )
        transports = tuple(
            item for item in driver.transports if item.support is SupportLevel.IMPLEMENTED
        )
        transport = self._select(
            "transport",
            transports,
            lambda item: item.name,
            lambda item: item.name,
            default=1,
        )
        host = self._ask("Host [127.0.0.1]: ") or "127.0.0.1"
        used_endpoints = {item.resource.endpoint for item in existing}
        while True:
            port_text = self._ask(f"Port [{next_port}]: ")
            try:
                port = int(port_text) if port_text else next_port
                resource = ResourceAddress(transport.name, host, port)
            except (TypeError, ValueError) as error:
                self.output(f"[ERROR] Invalid resource: {error}")
                continue
            if resource.endpoint in used_endpoints:
                self.output(f"[ERROR] Resource {host}:{port} is already used.")
                continue
            break
        configuration = self._configuration(model)
        return (
            BenchInstrument(
                id=instrument_id,
                name=name,
                driver=driver.id,
                model=model.model,
                serial_number=serial,
                reported_model=reported_model,
                configuration=configuration,
                resource=resource,
            ),
            port + 1,
        )

    def _configuration(self, model) -> dict[str, object]:
        if not model.configuration_fields:
            return {}
        configuration: dict[str, object] = {}
        if not self._yes("Configure advanced driver fields? [y/N]: ", default=False):
            return configuration
        for field in model.configuration_fields:
            if field.name in configuration:
                continue
            choice_hint = f" choices: {', '.join(field.choices)}" if field.choices else ""
            while True:
                value = self._ask(
                    f"{field.name} ({field.value_type.value}; blank inherits driver default;"
                    f"{choice_hint}): "
                )
                if not value:
                    break
                try:
                    parsed = _configuration_value(field, value)
                except ValueError as error:
                    self.output(f"[ERROR] {error}")
                    continue
                configuration[field.name] = parsed
                break
        return configuration

    def _select(self, label, choices, describe, identify, *, default=None):
        self.output(f"Available {label}s:")
        for index, item in enumerate(choices, 1):
            self.output(f"  {index}. {describe(item)}")
        while True:
            suffix = f" [{default}]" if default is not None else ""
            answer = self._ask(f"Select {label}{suffix}: ")
            if not answer and default is not None:
                return choices[default - 1]
            if answer.isdigit() and 1 <= int(answer) <= len(choices):
                return choices[int(answer) - 1]
            for item in choices:
                if identify(item).casefold() == answer.casefold():
                    return item
            self.output(f"[ERROR] Choose a listed {label} number or ID.")

    def _yes(self, prompt: str, *, default: bool) -> bool:
        while True:
            answer = self._ask(prompt).casefold()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.output("[ERROR] Enter y or n.")

    def _ask(self, prompt: str) -> str:
        answer = self.input(prompt).strip()
        if answer.casefold() in {"cancel", "quit"}:
            raise BenchBuildCancelled("bench creation cancelled")
        return answer


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", value.casefold()).strip("_.-")


def _configuration_value(field, value: str):
    if field.value_type is ConfigurationFieldType.STRING_LIST:
        selected = [item.strip() for item in value.split(",") if item.strip()]
        invalid = set(selected) - set(field.choices) if field.choices else set()
        if invalid:
            raise ValueError(f"{field.name} has unknown choices: {sorted(invalid)}")
        return selected
    if field.value_type in {ConfigurationFieldType.INTEGER, ConfigurationFieldType.NUMBER}:
        try:
            number = float(value)
        except ValueError as error:
            raise ValueError(f"{field.name} must be a number") from error
        if not math.isfinite(number):
            raise ValueError(f"{field.name} must be finite")
        if field.minimum is not None and number < field.minimum:
            raise ValueError(f"{field.name} must be at least {field.minimum}")
        if field.maximum is not None and number > field.maximum:
            raise ValueError(f"{field.name} must be at most {field.maximum}")
        if field.value_type is ConfigurationFieldType.INTEGER and not number.is_integer():
            raise ValueError(f"{field.name} must be an integer")
        return int(number) if number.is_integer() else number
    if field.choices and value not in field.choices:
        raise ValueError(f"{field.name} must be one of: {', '.join(field.choices)}")
    return value


def _atomic_save(definition: BenchDefinition, target: Path) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        save_bench(definition, temporary)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
