"""Instrument driver catalog and built-in driver families."""

from .catalog import (
    DRIVER_ENTRY_POINT_GROUP,
    CatalogError,
    CommandCoverage,
    DriverCatalog,
    DriverDescriptor,
    DriverMaturity,
    InstrumentDriver,
    InstrumentRequest,
    ModelDescriptor,
    ModelMatch,
    ScenarioInputDescriptor,
    SupportLevel,
    TransportDescriptor,
)
from .pna import PNA_DRIVER_ID, PNADriver


def build_driver_catalog(*, discover_plugins: bool = True) -> DriverCatalog:
    """Create a fresh catalog containing built-ins and optional entry-point drivers."""
    catalog = DriverCatalog((PNADriver(),))
    if discover_plugins:
        catalog.discover()
    return catalog


__all__ = [
    "DRIVER_ENTRY_POINT_GROUP",
    "PNA_DRIVER_ID",
    "CatalogError",
    "CommandCoverage",
    "DriverCatalog",
    "DriverDescriptor",
    "DriverMaturity",
    "InstrumentDriver",
    "InstrumentRequest",
    "ModelDescriptor",
    "ModelMatch",
    "PNADriver",
    "ScenarioInputDescriptor",
    "SupportLevel",
    "TransportDescriptor",
    "build_driver_catalog",
]
