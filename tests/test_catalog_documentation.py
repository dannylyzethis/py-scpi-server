import json
import re
import csv
from pathlib import Path

from scpi_emulator.drivers import build_driver_catalog


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DOCUMENT = REPOSITORY_ROOT / "docs" / "instrument-catalog.md"
OPTION_DOCUMENT = REPOSITORY_ROOT / "docs" / "instrument-options.json"
PROFILE = REPOSITORY_ROOT / "src" / "scpi_emulator" / "profiles" / "vna_capabilities.v1.json"


def test_user_catalog_lists_every_runtime_driver_model_firmware_and_transport() -> None:
    document = CATALOG_DOCUMENT.read_text(encoding="utf-8")
    catalog = build_driver_catalog(discover_plugins=False)

    for driver in catalog.descriptors:
        assert f"`{driver.id}`" in document
        for model in driver.models:
            assert f"`{model.model}`" in document
            for firmware in model.firmware_snapshots:
                assert f"`{firmware}`" in document
        for transport in driver.transports:
            assert f"`{transport.name}`" in document


def test_user_catalog_lists_every_vna_hardware_and_application_token() -> None:
    document = CATALOG_DOCUMENT.read_text(encoding="utf-8")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    options = json.loads(OPTION_DOCUMENT.read_text(encoding="utf-8"))
    documented_driver = options["drivers"]["virtual-vna"]
    documented_models = options["drivers"]["virtual-vna"]["models"]

    assert set(documented_driver["hardware_features"]) == set(profile["hardware_features"])
    for feature in profile["hardware_features"]:
        assert f"`{feature}`" in document
    for model_name, model in profile["models"].items():
        documented = documented_models[model_name]
        assert documented["ports"] == model["ports"]
        assert documented["default_source_count"] == model["default_source_count"]
        assert set(documented["applications"]) <= set(profile["applications"])
        for application in documented["applications"]:
            assert f"`{application}`" in document
    assert "instrument-options.json" in document


def test_user_catalog_explains_all_bench_and_generic_vna_fields() -> None:
    document = CATALOG_DOCUMENT.read_text(encoding="utf-8")

    for field in (
        "id",
        "driver",
        "model",
        "resource",
        "name",
        "reported_model",
        "serial_number",
        "firmware",
        "configuration",
        "source_count",
        "hardware_features",
        "applications",
        "frequency_minimum_hz",
        "frequency_maximum_hz",
    ):
        assert f"`{field}`" in document
    assert "`vna-2-port`" in document
    assert "`vna-4-port`" in document
    assert "no fixed upper ceiling" in document


def test_every_catalog_json_example_is_copyable_and_readme_links_catalog() -> None:
    document = CATALOG_DOCUMENT.read_text(encoding="utf-8")
    examples = re.findall(r"```json\n(.*?)\n```", document, flags=re.DOTALL)

    assert examples
    for example in examples:
        assert isinstance(json.loads(example), dict)
    readme = (REPOSITORY_ROOT / "readme.md").read_text(encoding="utf-8")
    assert "docs/instrument-catalog.md" in readme


def test_user_catalog_inventories_every_bundled_root_csv_equipment_model() -> None:
    document = CATALOG_DOCUMENT.read_text(encoding="utf-8")
    equipment_blocks = 0
    model_ids: set[str] = set()

    for source in REPOSITORY_ROOT.glob("*.csv"):
        with source.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                equipment = (row.get("Equipment") or "").strip()
                if not equipment:
                    continue
                equipment_blocks += 1
                model_id = re.sub(r"[^a-z0-9]+", "_", equipment.lower()).strip("_")
                model_ids.add(model_id)
                assert f"`{equipment}`" in document
                assert f"`{model_id}`" in document

    assert equipment_blocks == 12
    assert len(model_ids) == 12
    assert "7 built-in models plus 12 bundled CSV model" in document
