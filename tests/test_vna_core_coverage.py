import json
from pathlib import Path

import pytest

from tools.vna_core_coverage import _inventory_key, build_report, load_inventory

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_independent_core_inventory_is_structured_and_sanitized() -> None:
    inventory = load_inventory()

    assert inventory["snapshot"] == {
        "date": "2026-10-08",
        "contract_revision": "1.0",
    }
    assert len(inventory["commands"]) == 385
    assert {command["classification"] for command in inventory["commands"]} == {
        "excluded_calibration",
        "excluded_file_io",
        "excluded_front_panel",
        "excluded_identity_override",
        "excluded_legacy_control",
        "excluded_obsolete",
        "excluded_physical_hardware",
        "review_required",
    }
    assert all(set(command["forms"]) <= {"set", "query"} for command in inventory["commands"])
    serialized = json.dumps(inventory).casefold()
    assert "http://" not in serialized
    assert "https://" not in serialized
    assert "key" + "sight" not in serialized
    assert "agi" + "lent" not in serialized


def test_coverage_distinguishes_omitted_and_explicit_optional_headers() -> None:
    source = "SOURce<channel>:POWer<port>[:LEVel][:IMMediate][:AMPLitude]"
    frequency = "SENSe<channel>:FREQuency[:CW|:FIXed]"

    assert _inventory_key(source, "set", expanded=False) == "SOURCE:POWER"
    assert (
        _inventory_key(source, "query", expanded=True) == "SOURCE:POWER:LEVEL:IMMEDIATE:AMPLITUDE?"
    )
    assert _inventory_key(frequency, "set", expanded=False) == "SENSE:FREQUENCY"
    assert _inventory_key(frequency, "query", expanded=True) == "SENSE:FREQUENCY:CW?"
    assert _inventory_key("MMEMory:CATalog[:<char>]", "query", expanded=True) == "MMEMORY:CATALOG?"


@pytest.mark.parametrize("model", ["vna-2-port", "vna-4-port"])
def test_core_report_proves_all_in_scope_forms_are_executable(model: str) -> None:
    report = build_report(model)

    assert report["summary"]["command_headers"] == 385
    assert report["summary"]["review_required_forms"] == 461
    assert report["summary"]["in_scope_forms"] == 461
    assert report["summary"]["excluded_forms"] == 152
    assert report["summary"]["minimum_header_forms_missing"] == 0
    assert report["summary"]["expanded_header_forms_missing"] == 0
    probe = report["runtime_probe"]
    assert probe["attempted_registry_forms"] == 590
    assert probe["full_mnemonic_outcomes"] == {"passed": 590}
    assert probe["minimum_mnemonic_outcomes"] == {"passed": 590}


@pytest.mark.parametrize("model", ["vna-2-port", "vna-4-port"])
def test_checked_in_core_coverage_report_is_current(model: str) -> None:
    path = REPOSITORY_ROOT / "reports" / f"vna-core-coverage-{model}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == build_report(model)
