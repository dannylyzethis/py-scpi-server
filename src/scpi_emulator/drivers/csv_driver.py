"""Catalog adapter for legacy five-column CSV instrument definitions."""

from __future__ import annotations

from pathlib import Path

from scpi_emulator import EMULATOR_FIRMWARE, __version__

from .catalog import (
    CatalogError,
    DriverDescriptor,
    DriverMaturity,
    InstrumentRequest,
    ModelDescriptor,
    SupportLevel,
    TransportDescriptor,
)


CSV_DRIVER_ID = "csv-instruments"


class CSVDriver:
    """Expose every Equipment block in a configured CSV directory as a model."""

    def __init__(self, directory: str | Path) -> None:
        from scpi_emulator.emulator import (
            ConfigurationError,
            load_compatibility_instruments,
        )

        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise CatalogError(f"CSV driver directory does not exist: {self.directory}")

        models: list[ModelDescriptor] = []
        self._sources: dict[str, Path] = {}
        for source in sorted(self.directory.glob("*.csv")):
            try:
                loaded, _ = load_compatibility_instruments(source)
            except ConfigurationError as error:
                raise CatalogError(f"could not catalog CSV file {source}: {error}") from error
            for equipment_id, item in loaded.items():
                instrument = item["instrument"]
                key = equipment_id.casefold()
                if key in self._sources:
                    first_source = self._sources[key]
                    raise CatalogError(
                        f"duplicate equipment name {instrument.name!r} in "
                        f"{str(first_source)!r} and {str(source)!r}"
                    )
                self._sources[key] = source
                models.append(
                    ModelDescriptor(
                        model=equipment_id,
                        display_name=instrument.name,
                        instrument_class="CSV",
                        firmware_snapshots=(EMULATOR_FIRMWARE,),
                    )
                )
        if not models:
            raise CatalogError(f"CSV driver directory contains no instruments: {self.directory}")

        self.descriptor = DriverDescriptor(
            id=CSV_DRIVER_ID,
            display_name="CSV instruments",
            version=__version__,
            maturity=DriverMaturity.EXPERIMENTAL,
            models=tuple(models),
            transports=(
                TransportDescriptor(
                    "raw-socket",
                    "TCPIP::{host}::{port}::SOCKET",
                    SupportLevel.IMPLEMENTED,
                ),
            ),
            scenario_inputs=(),
            command_coverage=(),
        )

    def create_instrument(self, request: InstrumentRequest) -> object:
        from scpi_emulator.emulator import (
            ConfigurationError,
            load_compatibility_instruments,
        )

        model = self.descriptor.model(request.model)
        firmware = request.firmware or model.firmware_snapshots[0]
        if firmware not in model.firmware_snapshots:
            raise CatalogError(
                f"driver {CSV_DRIVER_ID!r} has no verified {request.model} firmware {firmware!r}"
            )
        if request.configuration:
            raise CatalogError("CSV instruments have no catalog configuration options")

        source = self._sources[model.model.casefold()]
        try:
            loaded, _ = load_compatibility_instruments(source)
        except ConfigurationError as error:
            raise CatalogError(f"could not load CSV instrument from {source}: {error}") from error
        instrument = loaded[model.model]["instrument"]
        instrument.id = request.instrument_id
        if request.name is not None:
            instrument.name = request.name
        if request.serial_number is not None:
            try:
                instrument.set_serial_number(request.serial_number)
            except ValueError as error:
                raise CatalogError(
                    f"CSV instrument {request.model!r} cannot override its serial number: {error}"
                ) from error
        return instrument
