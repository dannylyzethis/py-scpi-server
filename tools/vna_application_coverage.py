"""Report syntax-level coverage for advertised VNA application commands."""

from __future__ import annotations

import argparse
import json
from importlib.resources import files
from pathlib import Path

from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scpi import VNA_APPLICATION_CONTRACTS, registry_implementation_keys


def load_inventory() -> dict:
    resource = files("scpi_emulator").joinpath("profiles/vna_application_compatibility.v1.json")
    inventory = json.loads(resource.read_text(encoding="utf-8"))
    if inventory.get("schema_version") != 1:
        raise ValueError("unsupported application compatibility schema")
    applications = inventory.get("applications")
    if not isinstance(applications, dict) or set(applications) != set(VNA_APPLICATION_CONTRACTS):
        raise ValueError("compatibility inventory must exactly match advertised applications")
    return inventory


def expected_keys(application: dict) -> set[str]:
    keys: set[str] = set()
    for command in application["commands"]:
        syntax = command["syntax"].upper()
        for form in command["forms"]:
            if form not in {"set", "query"}:
                raise ValueError(f"unsupported command form {form!r}")
            keys.add(syntax + ("?" if form == "query" else ""))
    return keys


def build_report(model: str) -> dict:
    inventory = load_inventory()
    instrument = SCPIInstrument(f"Virtual {model}", "application-audit")
    implemented = {key.upper() for key in registry_implementation_keys(instrument.core_registry)}
    applications = {}
    required = 0
    present = 0
    pending = []
    for name, application in inventory["applications"].items():
        expected = expected_keys(application)
        missing = sorted(expected - implemented)
        if application["audit_status"] == "pending":
            pending.append(name)
        required += len(expected)
        present += len(expected) - len(missing)
        applications[name] = {
            "audit_status": application["audit_status"],
            "required": len(expected),
            "implemented": len(expected) - len(missing),
            "missing_keys": missing,
        }
    return {
        "schema_version": 1,
        "target": {"model": model},
        "summary": {
            "advertised_applications": len(applications),
            "audited_applications": len(applications) - len(pending),
            "pending_applications": len(pending),
            "required_commands": required,
            "implemented_commands": present,
            "missing_commands": required - present,
        },
        "pending_application_names": sorted(pending),
        "applications": applications,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("vna-2-port", "vna-4-port"), default="vna-2-port")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.model)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    incomplete = report["summary"]["pending_applications"] or report["summary"]["missing_commands"]
    return 1 if incomplete and not args.allow_pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
