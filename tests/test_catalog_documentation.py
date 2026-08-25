import json
import re
import csv
from pathlib import Path

from scpi_emulator.drivers import build_driver_catalog


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DOCUMENT = REPOSITORY_ROOT / "docs" / "instrument-catalog.md"
OPTION_DOCUMENT = REPOSITORY_ROOT / "docs" / "instrument-options.json"
MATRIX = REPOSITORY_ROOT / "src" / "scpi_emulator" / "profiles" / "pna_compatibility.v1.json"


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


def test_user_catalog_lists_every_vna_configuration_addon_and_application_token() -> None:
    document = CATALOG_DOCUMENT.read_text(encoding="utf-8")
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    options = json.loads(OPTION_DOCUMENT.read_text(encoding="utf-8"))
    documented_models = options["drivers"]["virtual-vna"]["models"]

    for model_name, model in matrix["models"].items():
        documented = documented_models[model_name]
        assert set(documented["hardware_configurations"]) == set(
            model["hardware_configurations"]
        )
        assert set(documented["hardware_addons"]) == set(model["hardware_addons"])
        for configuration in model["hardware_configurations"]:
            assert f"`{configuration}`" in document
        for addon in model["hardware_addons"]:
            assert f"`{addon}`" in document
        expected_options = {
            option
            for application in matrix["applications"].values()
            if model_name in application["models"]
            for option in application["options"]
        }
        assert set(documented["application_options"]) == expected_options
    assert "instrument-options.json" in document


def test_user_catalog_explains_all_bench_fields_and_both_vna_modes() -> None:
    document = CATALOG_DOCUMENT.read_text(encoding="utf-8")

    for field in (
        "id",
        "driver",
        "model",
        "resource",
        "name",
        "serial_number",
        "firmware",
        "configuration",
        "hardware_configuration",
        "hardware_addons",
        "application_options",
    ):
        assert f"`{field}`" in document
    assert "`model-faithful`" in document
    assert "`all-applications`" in document


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

    assert equipment_blocks == 11
    assert len(model_ids) == 10
    assert "4 built-in models plus 10 bundled CSV model" in document
