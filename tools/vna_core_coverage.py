"""Report VNA core-command syntax coverage against an independent inventory."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections import Counter
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scpi import (
    CommandSpec,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
    command_spec_key,
    parse_program_message,
    registry_implementation_keys,
)


def load_inventory() -> dict:
    resource = files("scpi_emulator").joinpath("profiles/vna_core_compatibility.v1.json")
    inventory = json.loads(resource.read_text(encoding="utf-8"))
    if inventory.get("schema_version") != 1:
        raise ValueError("unsupported VNA core compatibility schema")
    commands = inventory.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("VNA core compatibility inventory must contain commands")
    allowed_classifications = {
        "excluded_calibration",
        "excluded_file_io",
        "excluded_front_panel",
        "excluded_identity_override",
        "excluded_legacy_control",
        "excluded_obsolete",
        "excluded_physical_hardware",
        "review_required",
    }
    identities = set()
    for command in commands:
        if set(command) != {"family", "syntax", "forms", "classification"}:
            raise ValueError("VNA core command entries have invalid fields")
        if not all(isinstance(command[field], str) for field in ("family", "syntax")):
            raise ValueError("VNA core command family and syntax must be strings")
        if " " in command["syntax"] or command["syntax"].count("[") != command["syntax"].count("]"):
            raise ValueError(f"invalid VNA core syntax {command['syntax']!r}")
        if not command["forms"] or not set(command["forms"]) <= {"set", "query"}:
            raise ValueError(f"invalid forms for VNA core syntax {command['syntax']!r}")
        if command["classification"] not in allowed_classifications:
            raise ValueError(f"invalid classification for {command['syntax']!r}")
        identity = (command["syntax"].upper(), tuple(command["forms"]))
        if identity in identities:
            raise ValueError(f"duplicate VNA core command {command['syntax']!r}")
        identities.add(identity)
    return inventory


def _without_indices(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _primary_alternative(value: str) -> str:
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"\[([^|\[\]]+)\|[^\[\]]+\]", r"[\1]", value)
        value = re.sub(
            r"(?<=:)([^:\[\]|]+)\|([^:\[\]|]+)(?=[:\[]|$)",
            r"\1",
            value,
        )
    return value


def _expand_optional(value: str, *, include: bool) -> str:
    previous = None
    while previous != value:
        previous = value
        value = re.sub(
            r"\[([^\[\]]*)\]",
            (lambda match: match.group(1)) if include else "",
            value,
        )
    return value


def _inventory_key(syntax: str, form: str, *, expanded: bool) -> str:
    syntax = syntax.removesuffix("?")
    syntax = _primary_alternative(syntax)
    syntax = _expand_optional(syntax, include=expanded)
    # A colon-prefixed placeholder describes an optional catalog selector,
    # not a literal registry header (for example ``CATalog[:<char>]``).
    syntax = re.sub(r":<[^>]+>", "", syntax)
    key = _without_indices(syntax).lstrip(":").upper()
    return key + ("?" if form == "query" else "")


def _registry_key(value: str) -> str:
    return _without_indices(value).upper()


def _mnemonic(value: str, *, abbreviated: bool) -> str:
    if not abbreviated or value.startswith("*"):
        return value.upper()
    mandatory = "".join(character for character in value if character.isupper())
    return mandatory or value.upper()


def _parameter_value(specification: ParameterSpec, key: str) -> str:
    if specification.type is ParameterType.STRING:
        if key == "CALCULATE:DATA:SNP:PORTS?":
            return '"1,2"'
        if key.startswith("MMEMORY:"):
            return '"audit.sta"'
        return '"audit"'
    if specification.type is ParameterType.CHARACTER:
        return "audit"
    if specification.type is ParameterType.ENUM:
        return specification.choices[0]
    if specification.type is ParameterType.BOOLEAN:
        return "0"
    if specification.type is ParameterType.BINARY:
        return "#10"
    if specification.type in {ParameterType.NUMBER, ParameterType.INTEGER}:
        minimum = Decimal(specification.minimum) if specification.minimum is not None else None
        maximum = Decimal(specification.maximum) if specification.maximum is not None else None
        candidate = Decimal(25_005_000_000) if "HZ" in specification.units else Decimal(1)
        if minimum is not None and candidate < minimum:
            candidate = minimum
        if maximum is not None and candidate > maximum:
            candidate = maximum
        if specification.type is ParameterType.INTEGER:
            return str(int(candidate))
        return str(candidate)
    raise AssertionError(f"unsupported parameter type {specification.type}")


def _probe_command(specification: CommandSpec, key: str, *, abbreviated: bool) -> str:
    nodes = []
    for node in specification.path:
        suffix = ""
        if node.index is not None:
            suffix = str(node.index_default if node.index_default is not None else 1)
        nodes.append(_mnemonic(node.mnemonic, abbreviated=abbreviated) + suffix)
    header = nodes[0] if specification.common else ":".join(nodes)
    if specification.query:
        header += "?"
    parameters = [
        _parameter_value(parameter, key)
        for parameter in specification.parameters
        if parameter.required
    ]
    return header + ((" " + ",".join(parameters)) if parameters else "")


def _dispatch(instrument: SCPIInstrument, source: str) -> None:
    command = parse_program_message(source).commands[0]
    instrument.core_registry.dispatch(command)


def _prepare_probe(instrument: SCPIInstrument, key: str) -> None:
    if key.startswith("SENSE:SEGMENT") and key not in {
        "SENSE:SEGMENT:ADD",
        "SENSE:SEGMENT:COUNT?",
        "SENSE:SEGMENT:DELETE:ALL",
    }:
        _dispatch(instrument, "SENS1:SEGM1:ADD")
    if key in {
        "CALCULATE:PARAMETER:DELETE",
        "CALCULATE:PARAMETER:DELETE:NAME",
        "CALCULATE:PARAMETER:SELECT",
        "DISPLAY:MEASURE:FEED",
        "DISPLAY:WINDOW:TRACE:FEED",
    }:
        _dispatch(instrument, 'CALC1:PAR:DEF:EXT "audit","S11"')
    if key == "MMEMORY:DELETE":
        _dispatch(instrument, 'MMEM:STOR:STAT "audit.sta"')


def _runtime_probe(model: str, keys: set[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="vna-core-audit-") as temporary:
        instrument = SCPIInstrument(f"Virtual {model}", model, state_directory=Path(temporary))
        specifications = {
            _registry_key(command_spec_key(specification)): specification
            for specification in instrument.core_registry.specifications
        }
        outcomes = {"full": Counter(), "minimum": Counter()}
        failures = {"full": [], "minimum": []}
        for key in sorted(keys):
            specification = specifications[key]
            for spelling, abbreviated in (("full", False), ("minimum", True)):
                instrument._reset()
                _prepare_probe(instrument, key)
                source = _probe_command(specification, key, abbreviated=abbreviated)
                try:
                    _dispatch(instrument, source)
                except SCPICommandError as error:
                    outcome = f"scpi_error_{error.code}"
                    failures[spelling].append({"key": key, "probe": source, "result": outcome})
                except Exception as error:  # pragma: no cover - reported for diagnosis
                    outcome = f"unexpected_{type(error).__name__}"
                    failures[spelling].append({"key": key, "probe": source, "result": outcome})
                else:
                    outcome = "passed"
                outcomes[spelling][outcome] += 1
    return {
        "attempted_registry_forms": len(keys),
        "full_mnemonic_outcomes": dict(sorted(outcomes["full"].items())),
        "minimum_mnemonic_outcomes": dict(sorted(outcomes["minimum"].items())),
        "full_mnemonic_failures": failures["full"],
        "minimum_mnemonic_failures": failures["minimum"],
    }


def build_report(model: str) -> dict:
    inventory = load_inventory()
    instrument = SCPIInstrument(f"Virtual {model}", model)
    implemented = {
        _registry_key(key) for key in registry_implementation_keys(instrument.core_registry)
    }
    families: dict[str, dict] = {}
    total_forms = 0
    minimum_present = 0
    expanded_present = 0
    excluded_forms = 0
    classification_forms = Counter()
    probe_keys: set[str] = set()
    for command in inventory["commands"]:
        forms = command["forms"]
        classification = command["classification"]
        family = families.setdefault(
            command["family"],
            {
                "excluded_obsolete_forms": 0,
                "excluded_forms": 0,
                "expanded_header_forms_missing": 0,
                "expanded_header_forms_present": 0,
                "missing_expanded_keys": [],
                "missing_minimum_keys": [],
                "minimum_header_forms_missing": 0,
                "minimum_header_forms_present": 0,
                "review_required_forms": 0,
            },
        )
        for form in forms:
            minimum_key = _inventory_key(command["syntax"], form, expanded=False)
            expanded_key = _inventory_key(command["syntax"], form, expanded=True)
            excluded = classification.startswith("excluded_")
            total_forms += 1
            excluded_forms += int(excluded)
            classification_forms[classification] += 1
            family["excluded_forms"] += int(excluded)
            family["excluded_obsolete_forms"] += int(classification == "excluded_obsolete")
            minimum_match = minimum_key in implemented
            expanded_match = expanded_key in implemented
            minimum_present += int(minimum_match and not excluded)
            expanded_present += int(expanded_match and not excluded)
            if excluded:
                continue
            if minimum_match:
                probe_keys.add(minimum_key)
            if expanded_match:
                probe_keys.add(expanded_key)
            family["review_required_forms"] += 1
            family[f"minimum_header_forms_{'present' if minimum_match else 'missing'}"] += 1
            family[f"expanded_header_forms_{'present' if expanded_match else 'missing'}"] += 1
            if not minimum_match:
                family["missing_minimum_keys"].append(minimum_key)
            if not expanded_match:
                family["missing_expanded_keys"].append(expanded_key)
    in_scope_forms = total_forms - excluded_forms
    return {
        "schema_version": 1,
        "inventory_snapshot": inventory["snapshot"],
        "target": {"model": model},
        "summary": {
            "command_headers": len(inventory["commands"]),
            "command_forms": total_forms,
            "excluded_obsolete_forms": classification_forms["excluded_obsolete"],
            "excluded_forms": excluded_forms,
            "classification_forms": dict(sorted(classification_forms.items())),
            "in_scope_forms": in_scope_forms,
            "review_required_forms": classification_forms["review_required"],
            "minimum_header_forms_present": minimum_present,
            "minimum_header_forms_missing": in_scope_forms - minimum_present,
            "expanded_header_forms_present": expanded_present,
            "expanded_header_forms_missing": in_scope_forms - expanded_present,
        },
        "families": {
            name: {
                **result,
                "missing_expanded_keys": sorted(set(result["missing_expanded_keys"])),
                "missing_minimum_keys": sorted(set(result["missing_minimum_keys"])),
            }
            for name, result in sorted(families.items())
        },
        "runtime_probe": _runtime_probe(model, probe_keys),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("vna-2-port", "vna-4-port"), default="vna-2-port")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-gaps",
        action="store_true",
        help="return success while the newly established inventory still has gaps",
    )
    args = parser.parse_args(argv)
    report = build_report(args.model)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    probe = report["runtime_probe"]
    gaps = (
        report["summary"]["minimum_header_forms_missing"]
        + report["summary"]["expanded_header_forms_missing"]
        + len(probe["full_mnemonic_failures"])
        + len(probe["minimum_mnemonic_failures"])
    )
    return 1 if gaps and not args.allow_gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
