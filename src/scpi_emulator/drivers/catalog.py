"""UI-independent instrument driver metadata and catalog registration."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


DRIVER_ENTRY_POINT_GROUP = "scpi_emulator.drivers"


class CatalogError(ValueError):
    """Raised when driver metadata or catalog registration is invalid."""


class DriverMaturity(str, Enum):
    EXPERIMENTAL = "experimental"
    ALPHA = "alpha"
    BETA = "beta"
    STABLE = "stable"


class SupportLevel(str, Enum):
    PLANNED = "planned"
    PARTIAL = "partial"
    IMPLEMENTED = "implemented"


@dataclass(frozen=True)
class TransportDescriptor:
    """One resource/transport shape advertised by a driver."""

    name: str
    resource_template: str
    support: SupportLevel

    def __post_init__(self) -> None:
        _require_text(self.name, "transport name")
        _require_text(self.resource_template, "transport resource template")
        if not isinstance(self.support, SupportLevel):
            raise CatalogError("transport support must be a SupportLevel")


@dataclass(frozen=True)
class ScenarioInputDescriptor:
    """A scalar, trace, event, or other scenario stream understood by a driver."""

    kind: str
    support: SupportLevel
    description: str

    def __post_init__(self) -> None:
        _require_identifier(self.kind, "scenario input kind")
        _require_text(self.description, "scenario input description")
        if not isinstance(self.support, SupportLevel):
            raise CatalogError("scenario input support must be a SupportLevel")


@dataclass(frozen=True)
class ModelDescriptor:
    """Immutable compatibility metadata for one instrument model."""

    model: str
    display_name: str
    instrument_class: str
    firmware_snapshots: tuple[str, ...]
    hardware_configurations: tuple[str, ...] = ()
    hardware_options: tuple[str, ...] = ()
    application_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.model, "model")
        _require_text(self.display_name, "model display name")
        _require_text(self.instrument_class, "instrument class")
        if not self.firmware_snapshots:
            raise CatalogError(f"model {self.model!r} requires a firmware snapshot")
        _require_unique(self.firmware_snapshots, f"model {self.model!r} firmware snapshots")
        _require_unique(self.hardware_configurations, f"model {self.model!r} configurations")
        _require_unique(self.hardware_options, f"model {self.model!r} hardware options")
        _require_unique(self.application_options, f"model {self.model!r} application options")


@dataclass(frozen=True)
class CommandCoverage:
    """Coverage of a named command manifest for one model/firmware snapshot."""

    model: str
    firmware: str
    manifest: str
    report: str
    documented: int
    implemented: int

    def __post_init__(self) -> None:
        _require_identifier(self.model, "coverage model")
        _require_text(self.firmware, "coverage firmware")
        _require_text(self.manifest, "coverage manifest")
        _require_text(self.report, "coverage report")
        if self.documented < 0 or not 0 <= self.implemented <= self.documented:
            raise CatalogError("command coverage counts are inconsistent")

    @property
    def percent(self) -> float:
        return round(100 * self.implemented / self.documented, 2) if self.documented else 0.0


@dataclass(frozen=True)
class DriverDescriptor:
    """Everything a bench builder needs to enumerate a driver without UI code."""

    id: str
    display_name: str
    version: str
    maturity: DriverMaturity
    models: tuple[ModelDescriptor, ...]
    transports: tuple[TransportDescriptor, ...]
    scenario_inputs: tuple[ScenarioInputDescriptor, ...]
    command_coverage: tuple[CommandCoverage, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.id, "driver id")
        _require_text(self.display_name, "driver display name")
        _require_text(self.version, "driver version")
        if not isinstance(self.maturity, DriverMaturity):
            raise CatalogError("driver maturity must be a DriverMaturity")
        if not self.models:
            raise CatalogError(f"driver {self.id!r} requires at least one model")
        if not self.transports:
            raise CatalogError(f"driver {self.id!r} requires at least one transport")
        model_ids = tuple(model.model for model in self.models)
        _require_unique(model_ids, f"driver {self.id!r} models")
        _require_unique(
            tuple(transport.name for transport in self.transports),
            f"driver {self.id!r} transports",
        )
        _require_unique(
            tuple(item.kind for item in self.scenario_inputs),
            f"driver {self.id!r} scenario inputs",
        )
        known_models = {model.model: model for model in self.models}
        coverage_keys: list[tuple[str, str]] = []
        for coverage in self.command_coverage:
            if coverage.model not in known_models:
                raise CatalogError(
                    f"coverage references unknown model {coverage.model!r} in driver {self.id!r}"
                )
            if coverage.firmware not in known_models[coverage.model].firmware_snapshots:
                raise CatalogError(
                    f"coverage references unknown firmware {coverage.firmware!r} "
                    f"for model {coverage.model!r}"
                )
            coverage_keys.append((coverage.model, coverage.firmware))
        if len(coverage_keys) != len(set(coverage_keys)):
            raise CatalogError(f"driver {self.id!r} command coverage entries must be unique")

    def model(self, model: str) -> ModelDescriptor:
        normalized = model.upper()
        for descriptor in self.models:
            if descriptor.model.upper() == normalized:
                return descriptor
        raise CatalogError(f"driver {self.id!r} does not support model {model!r}")


@dataclass(frozen=True)
class InstrumentRequest:
    """Transport-neutral request passed from a bench composition to a driver."""

    instrument_id: str
    model: str
    name: str | None = None
    firmware: str | None = None
    serial_number: str | None = None
    configuration: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.instrument_id, "instrument id")
        _require_identifier(self.model, "instrument model")
        if self.name is not None:
            _require_text(self.name, "instrument name")
        if self.firmware is not None:
            _require_text(self.firmware, "instrument firmware")
        if self.serial_number is not None:
            _require_text(self.serial_number, "instrument serial number")
        if not isinstance(self.configuration, Mapping):
            raise CatalogError("instrument configuration must be a mapping")


@runtime_checkable
class InstrumentDriver(Protocol):
    """Minimal plug-in contract implemented by an instrument family."""

    @property
    def descriptor(self) -> DriverDescriptor: ...

    def create_instrument(self, request: InstrumentRequest) -> object: ...


@dataclass(frozen=True)
class ModelMatch:
    driver: DriverDescriptor
    model: ModelDescriptor


class DriverCatalog:
    """Register built-in or entry-point instrument drivers without UI coupling."""

    def __init__(self, drivers: Iterable[InstrumentDriver] = ()) -> None:
        self._drivers: dict[str, InstrumentDriver] = {}
        for driver in drivers:
            self.register(driver)

    def register(self, driver: InstrumentDriver) -> InstrumentDriver:
        descriptor = getattr(driver, "descriptor", None)
        factory = getattr(driver, "create_instrument", None)
        if not isinstance(descriptor, DriverDescriptor) or not callable(factory):
            raise CatalogError("driver must provide a DriverDescriptor and create_instrument()")
        key = descriptor.id.casefold()
        if key in self._drivers:
            raise CatalogError(f"driver id {descriptor.id!r} is already registered")
        self._drivers[key] = driver
        return driver

    @property
    def descriptors(self) -> tuple[DriverDescriptor, ...]:
        return tuple(
            driver.descriptor
            for driver in sorted(self._drivers.values(), key=lambda item: item.descriptor.id)
        )

    def get(self, driver_id: str) -> InstrumentDriver:
        try:
            return self._drivers[driver_id.casefold()]
        except KeyError as error:
            raise CatalogError(f"unknown driver {driver_id!r}") from error

    def find_model(self, model: str) -> tuple[ModelMatch, ...]:
        matches: list[ModelMatch] = []
        for descriptor in self.descriptors:
            for candidate in descriptor.models:
                if candidate.model.casefold() == model.casefold():
                    matches.append(ModelMatch(descriptor, candidate))
        return tuple(matches)

    def create(self, driver_id: str, request: InstrumentRequest) -> object:
        driver = self.get(driver_id)
        driver.descriptor.model(request.model)
        return driver.create_instrument(request)

    def discover(self, entry_points: Iterable[object] | None = None) -> tuple[str, ...]:
        """Load third-party drivers from ``scpi_emulator.drivers`` entry points."""
        if entry_points is None:
            from importlib.metadata import entry_points as installed_entry_points

            available = installed_entry_points()
            entry_points = (
                available.select(group=DRIVER_ENTRY_POINT_GROUP)
                if hasattr(available, "select")
                else available.get(DRIVER_ENTRY_POINT_GROUP, ())
            )
        registered: list[str] = []
        for entry_point in entry_points:
            try:
                loaded = entry_point.load()  # type: ignore[attr-defined]
                driver = loaded() if isinstance(loaded, type) else loaded
                self.register(driver)
                registered.append(driver.descriptor.id)
            except Exception as error:
                name = getattr(entry_point, "name", "<unknown>")
                raise CatalogError(f"could not load driver entry point {name!r}: {error}") from error
        return tuple(registered)


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{field_name} must be a non-empty string")


def _require_identifier(value: str, field_name: str) -> None:
    _require_text(value, field_name)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is None:
        raise CatalogError(f"{field_name} contains unsupported characters: {value!r}")


def _require_unique(values: tuple[str, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise CatalogError(f"{field_name} must be unique")
