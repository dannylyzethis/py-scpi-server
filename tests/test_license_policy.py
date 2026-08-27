"""Regression guards for the reviewed dependency-license policy."""

from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_license_inventory_is_complete_and_explicit() -> None:
    inventory = json.loads(
        (REPOSITORY_ROOT / "licenses" / "dependencies.json").read_text(encoding="utf-8")
    )
    packages = inventory["packages"]
    requirements = inventory["policy"]["requirements"]

    assert requirements == {
        "commercial_enterprise_use": True,
        "mandatory_license_fee_or_royalty": False,
    }
    assert set(inventory["roots"]) <= set(packages)
    assert all(entry["license"] for entry in packages.values())
    assert all(
        entry.get("reviewed_version") or entry.get("reviewed_versions")
        for entry in packages.values()
    )
    assert all(entry["scope"] for entry in packages.values())
    assert packages["bidict"]["license"] == "MPL-2.0"
    assert packages["bidict"]["reviewed_versions"] == ["0.23.1", "0.24.1"]
    assert packages["exceptiongroup"]["license"] == "MIT"
    assert packages["tomli"]["license"] == "MIT"
    assert packages["zeroconf"]["license"] == "LGPL-2.1-or-later"
    assert set(inventory["policy"]["reviewed_copyleft_packages"]) == {
        "bidict",
        "zeroconf",
    }


def test_no_unused_socketio_bundle_is_tracked() -> None:
    assert not (REPOSITORY_ROOT / "socket.io.min.js").exists()
