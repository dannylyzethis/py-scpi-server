import json
from dataclasses import FrozenInstanceError
from importlib.resources import files

import pytest

from scpi_emulator.drivers import (
    CatalogError,
    DriverCatalog,
    DriverDescriptor,
    DriverMaturity,
    DMMDriver,
    InstrumentRequest,
    ModelDescriptor,
    PNADriver,
    ScenarioInputDescriptor,
    SupportLevel,
    TransportDescriptor,
    build_driver_catalog,
)


def fake_descriptor(driver_id: str = "example-dmm") -> DriverDescriptor:
    return DriverDescriptor(
        id=driver_id,
        display_name="Example DMM",
        version="1.0.0",
        maturity=DriverMaturity.EXPERIMENTAL,
        models=(
            ModelDescriptor(
                model="DMM1000",
                display_name="Example DMM1000",
                instrument_class="DMM",
                firmware_snapshots=("1.0",),
            ),
        ),
        transports=(
            TransportDescriptor(
                "raw-socket",
                "TCPIP::{host}::{port}::SOCKET",
                SupportLevel.IMPLEMENTED,
            ),
        ),
        scenario_inputs=(
            ScenarioInputDescriptor(
                "scalar-reading",
                SupportLevel.IMPLEMENTED,
                "One sequential DMM reading.",
            ),
        ),
        command_coverage=(),
    )


class FakeDriver:
    def __init__(self, driver_id: str = "example-dmm") -> None:
        self.descriptor = fake_descriptor(driver_id)

    def create_instrument(self, request: InstrumentRequest) -> object:
        return request


class FakeEntryPoint:
    name = "example"

    @staticmethod
    def load():
        return FakeDriver


def test_builtin_catalog_advertises_pna_models_without_ui_dependency() -> None:
    catalog = build_driver_catalog(discover_plugins=False)

    assert [descriptor.id for descriptor in catalog.descriptors] == [
        "keysight-3446x",
        "keysight-pna",
    ]
    driver = catalog.get("KEYSIGHT-PNA")
    descriptor = driver.descriptor
    assert descriptor.maturity is DriverMaturity.ALPHA
    assert {model.model for model in descriptor.models} == {"N5222B", "N5242B"}
    assert descriptor.model("n5242b").instrument_class == "PNA-X"
    assert {item.name: item.support for item in descriptor.transports} == {
        "raw-socket": SupportLevel.IMPLEMENTED,
        "vxi-11": SupportLevel.IMPLEMENTED,
        "hislip": SupportLevel.IMPLEMENTED,
    }
    assert {item.kind: item.support for item in descriptor.scenario_inputs} == {
        "complex-trace": SupportLevel.IMPLEMENTED,
        "scalar-result": SupportLevel.IMPLEMENTED,
        "event": SupportLevel.PLANNED,
    }


def test_builtin_dmm_driver_advertises_and_creates_scalar_scenario_instrument() -> None:
    driver = DMMDriver()
    assert driver.descriptor.model("34461a").instrument_class == "DMM"
    assert driver.descriptor.scenario_inputs[0].support is SupportLevel.IMPLEMENTED

    instrument = driver.create_instrument(InstrumentRequest("meter", "34461A"))
    assert instrument.scalar_data is not None
    with pytest.raises(CatalogError, match="no configurable"):
        driver.create_instrument(
            InstrumentRequest("meter", "34461A", configuration={"option": "imaginary"})
        )


def test_pna_metadata_derives_models_options_and_firmware_from_snapshot() -> None:
    matrix_resource = files("scpi_emulator").joinpath("profiles/pna_compatibility.v1.json")
    matrix = json.loads(matrix_resource.read_text(encoding="utf-8"))
    descriptor = PNADriver().descriptor

    for model in descriptor.models:
        source = matrix["models"][model.model]
        assert model.firmware_snapshots == (matrix["snapshot"]["reference_firmware"],)
        assert set(model.hardware_configurations) == set(source["hardware_configurations"])
        assert set(model.hardware_options) == set(source["hardware_addons"])
        expected_apps = {
            option
            for application in matrix["applications"].values()
            if model.model in application["models"]
            for option in application["options"]
        }
        assert set(model.application_options) == expected_apps


def test_pna_coverage_metadata_matches_checked_in_reports() -> None:
    repository_root = files("scpi_emulator").joinpath("../..").resolve()
    for coverage in PNADriver().descriptor.command_coverage:
        report_path = repository_root.joinpath(coverage.report)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["target"] == {"model": coverage.model, "firmware": coverage.firmware}
        assert report["summary"]["documented"] == coverage.documented
        assert report["summary"]["implemented"] == coverage.implemented
        assert report["summary"]["coverage_percent"] == coverage.percent


def test_catalog_creates_a_configured_pna_through_the_driver_contract() -> None:
    catalog = build_driver_catalog(discover_plugins=False)
    instrument = catalog.create(
        "keysight-pna",
        InstrumentRequest(
            instrument_id="bench_pna",
            model="N5242B",
            configuration={
                "mode": "model-faithful",
                "hardware_configuration": "425",
                "application_options": ["S93080B", "S93029B"],
            },
        ),
    )

    assert instrument.process_command("*IDN?") == (
        "Keysight Technologies,N5242B,US12345678,A.20.25.04"
    )
    assert instrument.process_command("SYST:CAP:HARD:PORT:COUN?") == "4"
    assert instrument.process_command('SYST:CAP:LIC:FEAT:ENAB? "Noise Figure"') == "1"


def test_catalog_registration_and_entry_points_require_no_core_changes() -> None:
    catalog = DriverCatalog()

    catalog.register(FakeDriver())
    assert catalog.find_model("dmm1000")[0].driver.id == "example-dmm"
    request = InstrumentRequest("meter", "DMM1000")
    assert catalog.create("example-dmm", request) is request

    discovered = DriverCatalog()
    assert discovered.discover((FakeEntryPoint(),)) == ("example-dmm",)
    assert discovered.descriptors == (fake_descriptor(),)


def test_catalog_rejects_duplicate_or_incomplete_drivers() -> None:
    catalog = DriverCatalog((FakeDriver(),))

    with pytest.raises(CatalogError, match="already registered"):
        catalog.register(FakeDriver())
    with pytest.raises(CatalogError, match="must provide"):
        catalog.register(object())  # type: ignore[arg-type]
    with pytest.raises(CatalogError, match="does not support model"):
        catalog.create("example-dmm", InstrumentRequest("meter", "UNKNOWN"))


def test_descriptors_are_immutable_and_validate_inconsistent_metadata() -> None:
    descriptor = fake_descriptor()

    with pytest.raises(FrozenInstanceError):
        descriptor.display_name = "Changed"  # type: ignore[misc]
    with pytest.raises(CatalogError, match="must be unique"):
        DriverDescriptor(
            id="duplicate-models",
            display_name="Duplicate models",
            version="1",
            maturity=DriverMaturity.ALPHA,
            models=(descriptor.models[0], descriptor.models[0]),
            transports=descriptor.transports,
            scenario_inputs=(),
            command_coverage=(),
        )


def test_pna_factory_rejects_unverified_firmware_and_unknown_configuration() -> None:
    driver = PNADriver()

    with pytest.raises(CatalogError, match="no verified"):
        driver.create_instrument(InstrumentRequest("pna", "N5222B", firmware="A.99.00.00"))
    with pytest.raises(CatalogError, match="unsupported PNA configuration"):
        driver.create_instrument(
            InstrumentRequest("pna", "N5222B", configuration={"imaginary_option": True})
        )
