import json
from pathlib import Path

import pytest

from scpi_emulator.scpi import VNA_APPLICATION_CONTRACTS
from tools.vna_application_coverage import build_report, load_inventory

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_inventory_exactly_covers_advertised_applications() -> None:
    inventory = load_inventory()
    assert set(inventory["applications"]) == set(VNA_APPLICATION_CONTRACTS)


def test_completed_application_inventories_have_no_command_gaps() -> None:
    for model in ("vna-2-port", "vna-4-port"):
        report = build_report(model)
        completed = [
            result
            for result in report["applications"].values()
            if result["audit_status"] == "complete"
        ]
        assert completed
        assert all(result["missing_keys"] == [] for result in completed)
        assert report["applications"]["gain_compression"]["required"] == 72
        assert report["applications"]["active_hot_parameters"]["required"] == 23
        assert report["applications"]["noise_figure"]["required"] == 38
        assert report["applications"]["frequency_offset"]["required"] == 61
        assert report["applications"]["scalar_mixer"]["required"] == 93
        assert report["applications"]["frequency_converter"]["required"] == 2
        assert report["applications"]["embedded_lo"]["required"] == 26
        assert report["summary"]["audited_applications"] == 7
        assert report["summary"]["required_commands"] == 315


@pytest.mark.parametrize("model", ["vna-2-port", "vna-4-port"])
def test_checked_in_application_coverage_report_is_current(model: str) -> None:
    path = REPOSITORY_ROOT / "reports" / f"vna-application-coverage-{model}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == build_report(model)
