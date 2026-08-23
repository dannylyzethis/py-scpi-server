"""Built-in Keysight PNA/PNA-X instrument driver."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from scpi_emulator import __version__

from .catalog import (
    CatalogError,
    CommandCoverage,
    DriverDescriptor,
    DriverMaturity,
    InstrumentRequest,
    ModelDescriptor,
    ScenarioInputDescriptor,
    SupportLevel,
    TransportDescriptor,
)


PNA_DRIVER_ID = "keysight-pna"
PNA_MANIFEST_RESOURCE = "profiles/pna_commands.v1.json"


class PNADriver:
    """Create PNA instruments and advertise the pinned compatibility snapshot."""

    def __init__(self) -> None:
        self._matrix = _load_json("profiles/pna_compatibility.v1.json")
        self.descriptor = _build_descriptor(self._matrix)

    def create_instrument(self, request: InstrumentRequest) -> object:
        from scpi_emulator.emulator import SCPIInstrument
        from scpi_emulator.scpi import PNACapabilities

        model = self.descriptor.model(request.model)
        firmware = request.firmware or model.firmware_snapshots[0]
        if firmware not in model.firmware_snapshots:
            raise CatalogError(
                f"driver {PNA_DRIVER_ID!r} has no verified {request.model} firmware {firmware!r}"
            )
        allowed = {
            "mode",
            "hardware_configuration",
            "hardware_addons",
            "application_options",
            "serial",
        }
        unknown = set(request.configuration) - allowed
        if unknown:
            raise CatalogError(f"unsupported PNA configuration fields: {sorted(unknown)}")
        configuration: dict[str, Any] = dict(request.configuration)
        for sequence_name in ("hardware_addons", "application_options"):
            if sequence_name in configuration:
                configuration[sequence_name] = tuple(configuration[sequence_name])
        capabilities = PNACapabilities.create(
            request.model,
            firmware=firmware,
            **configuration,
        )
        name = request.name or f"Keysight {request.model}"
        return SCPIInstrument(name, request.instrument_id, pna_capabilities=capabilities)


def _build_descriptor(matrix: dict[str, Any]) -> DriverDescriptor:
    firmware = matrix["snapshot"]["reference_firmware"]
    documented_commands = len(_load_json(PNA_MANIFEST_RESOURCE)["commands"])
    models = tuple(
        _model_descriptor(model, model_data, matrix["applications"], firmware)
        for model, model_data in sorted(matrix["models"].items())
    )
    coverage = tuple(
        CommandCoverage(
            model=model.model,
            firmware=firmware,
            manifest=PNA_MANIFEST_RESOURCE,
            report=f"reports/pna-coverage-{model.model}-{firmware}.json",
            documented=documented_commands,
            implemented=documented_commands,
        )
        for model in models
    )
    return DriverDescriptor(
        id=PNA_DRIVER_ID,
        display_name="Keysight PNA/PNA-X",
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
    applications: dict[str, Any],
    firmware: str,
) -> ModelDescriptor:
    application_options = sorted(
        {
            option
            for application in applications.values()
            if model in application["models"]
            for option in application["options"]
        }
    )
    return ModelDescriptor(
        model=model,
        display_name=f"Keysight {model} {model_data['instrument_class']}",
        instrument_class=model_data["instrument_class"],
        firmware_snapshots=(firmware,),
        hardware_configurations=tuple(sorted(model_data["hardware_configurations"])),
        hardware_options=tuple(sorted(model_data["hardware_addons"])),
        application_options=tuple(application_options),
    )


def _load_json(resource: str) -> dict[str, Any]:
    return json.loads(files("scpi_emulator").joinpath(resource).read_text(encoding="utf-8"))
