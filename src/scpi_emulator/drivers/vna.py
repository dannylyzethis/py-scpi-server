"""Built-in generic VNA instrument driver."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from scpi_emulator import __version__

from .catalog import (
    CatalogError,
    CommandCoverage,
    ConfigurationFieldDescriptor,
    ConfigurationFieldType,
    DriverDescriptor,
    DriverMaturity,
    InstrumentRequest,
    ModelDescriptor,
    ScenarioInputDescriptor,
    SupportLevel,
    TransportDescriptor,
)


VNA_DRIVER_ID = "virtual-vna"
VNA_MANIFEST_RESOURCE = "profiles/vna_commands.v1.json"
VNA_CAPABILITY_RESOURCE = "profiles/vna_capabilities.v1.json"


class VNADriver:
    """Create generic VNA instruments from project-owned capability metadata."""

    def __init__(self) -> None:
        self._profile = _load_json(VNA_CAPABILITY_RESOURCE)
        self.descriptor = _build_descriptor(self._profile)

    def create_instrument(self, request: InstrumentRequest) -> object:
        from scpi_emulator.emulator import SCPIInstrument
        from scpi_emulator.scpi import VNACapabilities

        model = self.descriptor.model(request.model)
        firmware = request.firmware or model.firmware_snapshots[0]
        if firmware not in model.firmware_snapshots:
            raise CatalogError(
                f"driver {VNA_DRIVER_ID!r} has no verified {request.model} firmware {firmware!r}"
            )
        allowed = {
            "source_count",
            "hardware_features",
            "applications",
            "frequency_minimum_hz",
            "frequency_maximum_hz",
            "serial",
        }
        unknown = set(request.configuration) - allowed
        if unknown:
            raise CatalogError(f"unsupported VNA configuration fields: {sorted(unknown)}")
        configuration: dict[str, Any] = dict(request.configuration)
        configured_serial = configuration.get("serial")
        if (
            request.serial_number is not None
            and configured_serial is not None
            and request.serial_number != configured_serial
        ):
            raise CatalogError(
                "bench serial_number conflicts with legacy VNA configuration.serial"
            )
        if request.serial_number is not None:
            configuration["serial"] = request.serial_number
        capabilities = VNACapabilities.create(
            request.model,
            firmware=firmware,
            **configuration,
        )
        name = request.name or model.display_name
        instrument = SCPIInstrument(name, request.instrument_id, vna_capabilities=capabilities)
        instrument.set_reported_model(request.reported_model or model.display_name)
        return instrument


def _build_descriptor(profile: dict[str, Any]) -> DriverDescriptor:
    firmware = profile["snapshot"]["reference_firmware"]
    documented_commands = len(_load_json(VNA_MANIFEST_RESOURCE)["commands"])
    models = tuple(
        _model_descriptor(model, model_data, profile, firmware)
        for model, model_data in sorted(profile["models"].items())
    )
    coverage = tuple(
        CommandCoverage(
            model=model.model,
            firmware=firmware,
            manifest=VNA_MANIFEST_RESOURCE,
            report=f"reports/vna-coverage-{model.model}-{firmware}.json",
            documented=documented_commands,
            implemented=documented_commands,
        )
        for model in models
    )
    return DriverDescriptor(
        id=VNA_DRIVER_ID,
        display_name="Virtual Vector Network Analyzer",
        version=__version__,
        maturity=DriverMaturity.ALPHA,
        models=models,
        transports=(
            TransportDescriptor(
                "raw-socket",
                "TCPIP::{host}::{port}::SOCKET",
                SupportLevel.IMPLEMENTED,
            ),
            TransportDescriptor("vxi-11", "TCPIP::{host}::INSTR", SupportLevel.IMPLEMENTED),
            TransportDescriptor(
                "hislip",
                "TCPIP::{host}::hislip0,{port}::INSTR",
                SupportLevel.IMPLEMENTED,
            ),
        ),
        scenario_inputs=(
            ScenarioInputDescriptor(
                "complex-trace",
                SupportLevel.IMPLEMENTED,
                "Complex receiver or corrected trace samples with an optional stimulus axis.",
            ),
            ScenarioInputDescriptor(
                "scalar-result",
                SupportLevel.IMPLEMENTED,
                "Application summaries such as gain-compression results.",
            ),
            ScenarioInputDescriptor(
                "event",
                SupportLevel.PLANNED,
                "Deterministic trigger, status, and fault events.",
            ),
        ),
        command_coverage=coverage,
    )


def _model_descriptor(
    model: str,
    model_data: dict[str, Any],
    profile: dict[str, Any],
    firmware: str,
) -> ModelDescriptor:
    hardware_features = tuple(sorted(profile["hardware_features"]))
    applications = tuple(
        application
        for application, requirements in sorted(profile["applications"].items())
        if requirements.get("requires_ports", 0) <= model_data["ports"]
        and requirements.get("requires_sources", 0) <= 2
    )
    return ModelDescriptor(
        model=model,
        display_name=f"Virtual VNA {model_data['ports']} Port",
        instrument_class="VNA",
        firmware_snapshots=(firmware,),
        available_hardware_features=hardware_features,
        available_applications=applications,
        configuration_fields=(
            ConfigurationFieldDescriptor(
                "source_count",
                ConfigurationFieldType.INTEGER,
                "Number of independent stimulus sources.",
                default=model_data["default_source_count"],
                minimum=1,
                maximum=2,
            ),
            ConfigurationFieldDescriptor(
                "hardware_features",
                ConfigurationFieldType.STRING_LIST,
                "Project-owned simulated hardware capabilities.",
                default=("all",),
                choices=("all", *hardware_features),
            ),
            ConfigurationFieldDescriptor(
                "applications",
                ConfigurationFieldType.STRING_LIST,
                "Project-owned functional application capabilities.",
                default=("all",),
                choices=("all", *applications),
            ),
            ConfigurationFieldDescriptor(
                "frequency_minimum_hz",
                ConfigurationFieldType.NUMBER,
                "Emulated instrument minimum frequency in hertz.",
                default=10_000_000,
                minimum=1e-12,
            ),
            ConfigurationFieldDescriptor(
                "frequency_maximum_hz",
                ConfigurationFieldType.NUMBER,
                "Emulated instrument maximum frequency in hertz.",
                default=50_000_000_000,
                minimum=1e-12,
            ),
        ),
    )


def _load_json(resource: str) -> dict[str, Any]:
    return json.loads(files("scpi_emulator").joinpath(resource).read_text(encoding="utf-8"))
