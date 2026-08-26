import json
from dataclasses import FrozenInstanceError
from importlib.resources import files

import pytest

from scpi_emulator.drivers import (
    CSV_DRIVER_ID,
    CSVDriver,
    CatalogError,
    DriverCatalog,
    DriverDescriptor,
    DriverMaturity,
    DMMDriver,
    InstrumentRequest,
    ModelDescriptor,
    VNADriver,
    POWER_SUPPLY_DRIVER_ID,
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


def test_builtin_catalog_advertises_vna_models_without_ui_dependency() -> None:
    catalog = build_driver_catalog(discover_plugins=False)

    assert [descriptor.id for descriptor in catalog.descriptors] == [
        "virtual-3446x",
        "virtual-triple-psu",
        "virtual-vna",
    ]
    driver = catalog.get("VIRTUAL-VNA")
    descriptor = driver.descriptor
    assert descriptor.maturity is DriverMaturity.ALPHA
    assert {model.model for model in descriptor.models} == {"VNA-2PORT-EMU", "VNA-4PORT-EMU"}
    fields = {
        field.name: field
        for field in descriptor.model("VNA-2PORT-EMU").configuration_fields
    }
    assert set(fields) == {
        "source_count",
        "hardware_features",
        "applications",
        "frequency_minimum_hz",
        "frequency_maximum_hz",
    }
    assert fields["source_count"].default == 1
    assert fields["hardware_features"].default == ("all",)
    assert fields["applications"].default == ("all",)
    assert fields["frequency_maximum_hz"].default == 50_000_000_000
    assert fields["frequency_maximum_hz"].maximum is None
    assert descriptor.model("vna-4port-emu").instrument_class == "VNA"
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
    assert driver.descriptor.model("34461a-emu").instrument_class == "DMM"
    assert driver.descriptor.scenario_inputs[0].support is SupportLevel.IMPLEMENTED

    instrument = driver.create_instrument(InstrumentRequest("meter", "34461A-EMU"))
    assert instrument.scalar_data is not None
    serialled = driver.create_instrument(
        InstrumentRequest("meter2", "34461A-EMU", serial_number="DMM-002")
    )
    assert serialled.process_command("*IDN?").split(",")[2] == "DMM-002"
    with pytest.raises(CatalogError, match="no configurable"):
        driver.create_instrument(
            InstrumentRequest("meter", "34461A-EMU", configuration={"option": "imaginary"})
        )


def test_configured_csv_directory_adds_catalog_models_and_creates_instrument(tmp_path) -> None:
    (tmp_path / "devices.csv").write_text(
        "Equipment,Port,Command,Response,Validation\n"
        'Queue Reader,6101,*IDN?,"Emulator,Queue Reader,SN1,E.1.0",\n'
        ",,VALUE?,READY,\n",
        encoding="utf-8",
    )

    catalog = build_driver_catalog(discover_plugins=False, csv_directory=tmp_path)
    descriptors = {descriptor.id: descriptor for descriptor in catalog.descriptors}

    assert CSV_DRIVER_ID in descriptors
    descriptor = descriptors[CSV_DRIVER_ID]
    assert descriptor.maturity is DriverMaturity.EXPERIMENTAL
    assert descriptor.scenario_inputs == ()
    assert descriptor.model("queue_reader").instrument_class == "CSV"
    assert {item.name: item.support for item in descriptor.transports} == {
        "raw-socket": SupportLevel.IMPLEMENTED
    }

    instrument = catalog.create(
        CSV_DRIVER_ID,
        InstrumentRequest("bench_reader", "queue_reader", name="Bench queue reader"),
    )
    assert instrument.id == "bench_reader"
    assert instrument.name == "Bench queue reader"
    assert instrument.process_command("VALUE?") == "READY"

    assert isinstance(catalog.get(CSV_DRIVER_ID), CSVDriver)


def test_csv_driver_applies_a_per_instance_serial_number(tmp_path) -> None:
    (tmp_path / "supply.csv").write_text(
        "Equipment,Port,Command,Response,Validation\n"
        'Power Supply,,*IDN?,"SCPI Emulator,Power Supply,CSV-SERIAL,E.1.0",\n'
        ",,VOLT?,5.0,\n",
        encoding="utf-8",
    )
    catalog = build_driver_catalog(discover_plugins=False, csv_directory=tmp_path)

    first = catalog.create(
        CSV_DRIVER_ID,
        InstrumentRequest("supply1", "power_supply", serial_number="PSU-001"),
    )
    second = catalog.create(
        CSV_DRIVER_ID,
        InstrumentRequest("supply2", "power_supply", serial_number="PSU-002"),
    )

    assert first.process_command("*IDN?") == "SCPI Emulator,Power Supply,PSU-001,E.1.0"
    assert second.process_command("*IDN?") == "SCPI Emulator,Power Supply,PSU-002,E.1.0"
    assert POWER_SUPPLY_DRIVER_ID in {item.id for item in catalog.descriptors}


def test_vna_metadata_derives_models_options_and_firmware_from_snapshot() -> None:
    profile_resource = files("scpi_emulator").joinpath("profiles/vna_capabilities.v1.json")
    profile = json.loads(profile_resource.read_text(encoding="utf-8"))
    descriptor = VNADriver().descriptor

    for model in descriptor.models:
        source = profile["models"][model.model]
        assert model.firmware_snapshots == (profile["snapshot"]["reference_firmware"],)
        assert set(model.available_hardware_features) == set(profile["hardware_features"])
        assert model.configuration_fields[0].default == source["default_source_count"]
        assert set(model.available_applications) <= set(profile["applications"])
        if source["ports"] == 2:
            assert "source_phase_control" not in model.available_applications
        else:
            assert "source_phase_control" in model.available_applications


def test_vna_coverage_metadata_matches_checked_in_reports() -> None:
    repository_root = files("scpi_emulator").joinpath("../..").resolve()
    for coverage in VNADriver().descriptor.command_coverage:
        report_path = repository_root.joinpath(coverage.report)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["target"] == {"model": coverage.model, "firmware": coverage.firmware}
        assert report["summary"]["documented"] == coverage.documented
        assert report["summary"]["implemented"] == coverage.implemented
        assert report["summary"]["coverage_percent"] == coverage.percent


def test_catalog_creates_a_configured_vna_through_the_driver_contract() -> None:
    catalog = build_driver_catalog(discover_plugins=False)
    instrument = catalog.create(
        "virtual-vna",
        InstrumentRequest(
            instrument_id="bench_vna",
            model="VNA-4PORT-EMU",
            serial_number="VNA-001",
            configuration={
                "source_count": 2,
                "applications": ["frequency_offset", "noise_figure"],
            },
        ),
    )

    assert instrument.process_command("*IDN?") == (
        "SCPI Emulator,VNA-4PORT-EMU,VNA-001,E.1.0"
    )
    assert instrument.process_command("SYST:CAP:HARD:PORT:COUN?") == "4"
    assert instrument.process_command('SYST:CAP:LIC:FEAT:ENAB? "Noise Figure"') == "1"


def test_vna_driver_applies_a_narrowed_frequency_capability_everywhere() -> None:
    instrument = VNADriver().create_instrument(
        InstrumentRequest(
            instrument_id="limited_vna",
            model="VNA-2PORT-EMU",
            configuration={"frequency_maximum_hz": 20_000_000_000},
        )
    )

    assert instrument.process_command("SYST:CAP:FREQ:MIN?") == "10000000"
    assert instrument.process_command("SYST:CAP:FREQ:MAX?") == "20000000000"
    assert instrument.process_command("SENS:FREQ:STAR?") == "10000000"
    assert instrument.process_command("SENS:FREQ:STOP?") == "20000000000"
    assert instrument.vna_measurements.selected(1).stimulus[0] == 10_000_000
    assert instrument.vna_measurements.selected(1).stimulus[-1] == 20_000_000_000

    assert instrument.process_command("SENS:FREQ:STOP 21GHz") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')


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


def test_vna_factory_rejects_unverified_firmware_and_unknown_configuration() -> None:
    driver = VNADriver()

    with pytest.raises(CatalogError, match="no verified"):
        driver.create_instrument(InstrumentRequest("vna", "VNA-2PORT-EMU", firmware="A.99.00.00"))
    with pytest.raises(CatalogError, match="unsupported VNA configuration"):
        driver.create_instrument(
            InstrumentRequest("vna", "VNA-2PORT-EMU", configuration={"imaginary_option": True})
        )
