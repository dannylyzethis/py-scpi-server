"""Apply deterministic product-boundary classifications to absent VNA commands."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from vna_core_coverage import _inventory_key, _registry_key

from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scpi import registry_implementation_keys

ROOT = Path(__file__).parents[1]
INVENTORY = ROOT / "src" / "scpi_emulator" / "profiles" / "vna_core_compatibility.v1.json"


def classify(command: dict, implemented: set[str]) -> str:
    if command["classification"] != "review_required":
        return command["classification"]
    present = all(
        any(
            _inventory_key(command["syntax"], form, expanded=expanded) in implemented
            for expanded in (False, True)
        )
        for form in command["forms"]
    )
    if present:
        return "review_required"
    family = command["family"]
    syntax = command["syntax"].upper()
    if family == "display":
        return "excluded_front_panel"
    if family == "memory":
        return "excluded_file_io"
    if family != "system":
        return "review_required"
    if ":CORRECTION:" in syntax or ":FCORRECTION:" in syntax:
        return "excluded_calibration"
    if ":PERSONA:" in syntax:
        return "excluded_identity_override"
    if ":MACRO:" in syntax or ":SHORTCUT" in syntax:
        return "excluded_legacy_control"
    if any(
        token in syntax
        for token in (
            ":BEEPER:",
            ":BLIGHT",
            ":CLOCK",
            ":CONFIGURE",
            ":ERROR:REPORT:",
            ":FPRESET",
            ":ISPCONTOL",
            ":POFF",
            ":POWER",
            ":SECURITY",
            ":TOUCHSCREEN",
            ":UPRESET",
        )
    ):
        return "excluded_physical_hardware"
    return "review_required"


def main() -> None:
    document = json.loads(INVENTORY.read_text(encoding="utf-8"))
    instrument = SCPIInstrument("Virtual VNA", "vna-2-port")
    implemented = {
        _registry_key(key) for key in registry_implementation_keys(instrument.core_registry)
    }
    for command in document["commands"]:
        command["classification"] = classify(command, implemented)
    INVENTORY.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    counts = Counter(command["classification"] for command in document["commands"])
    print(", ".join(f"{name}={count}" for name, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
