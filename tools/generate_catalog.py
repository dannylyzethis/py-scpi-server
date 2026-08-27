"""Generate user-facing catalog artifacts from built-in runtime descriptors."""

from __future__ import annotations

import argparse
import json
from enum import Enum
from pathlib import Path
from typing import Any

from scpi_emulator.drivers import build_driver_catalog

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = REPOSITORY_ROOT / "docs" / "instrument-options.json"
DOCUMENT_PATH = REPOSITORY_ROOT / "docs" / "instrument-catalog.md"
START_MARKER = "<!-- BEGIN GENERATED BUILT-IN CATALOG -->"
END_MARKER = "<!-- END GENERATED BUILT-IN CATALOG -->"


def build_catalog_artifact() -> dict[str, Any]:
    """Return a stable JSON-compatible snapshot of every built-in descriptor."""
    catalog = build_driver_catalog(discover_plugins=False)
    return {
        "schema_version": 3,
        "description": "Generated from built-in runtime instrument driver descriptors.",
        "drivers": [_driver_data(driver) for driver in catalog.descriptors],
    }


def _driver_data(driver: Any) -> dict[str, Any]:
    return {
        "id": driver.id,
        "display_name": driver.display_name,
        "version": driver.version,
        "maturity": driver.maturity.value,
        "transports": [
            {
                "name": item.name,
                "resource_template": item.resource_template,
                "support": item.support.value,
            }
            for item in driver.transports
        ],
        "scenario_inputs": [
            {
                "kind": item.kind,
                "support": item.support.value,
                "description": item.description,
            }
            for item in driver.scenario_inputs
        ],
        "models": [_model_data(model) for model in driver.models],
        "command_coverage": [
            {
                "model": item.model,
                "firmware": item.firmware,
                "manifest": item.manifest,
                "report": item.report,
                "documented": item.documented,
                "implemented": item.implemented,
                "percent": item.percent,
            }
            for item in driver.command_coverage
        ],
    }


def _model_data(model: Any) -> dict[str, Any]:
    return {
        "model": model.model,
        "display_name": model.display_name,
        "instrument_class": model.instrument_class,
        "firmware_snapshots": list(model.firmware_snapshots),
        "available_hardware_features": list(model.available_hardware_features),
        "available_applications": list(model.available_applications),
        "configuration_fields": [
            {
                "name": field.name,
                "value_type": field.value_type.value,
                "description": field.description,
                "default": _json_value(field.default),
                "choices": list(field.choices),
                "minimum": field.minimum,
                "maximum": field.maximum,
            }
            for field in model.configuration_fields
        ],
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def render_json(artifact: dict[str, Any]) -> str:
    return json.dumps(artifact, indent=2, ensure_ascii=False) + "\n"


def render_markdown(artifact: dict[str, Any]) -> str:
    drivers = artifact["drivers"]
    model_count = sum(len(driver["models"]) for driver in drivers)
    lines = [
        START_MARKER,
        "",
        "## Built-in runtime catalog",
        "",
        "This section is generated from the runtime driver descriptors. Do not edit it by hand; run",
        "`python tools/generate_catalog.py --write` after changing a descriptor.",
        "",
        f"The built-in catalog contains {model_count} models across {len(drivers)} drivers.",
        "",
        "| Driver | Model | Class | Default reported model | Firmware | Maturity |",
        "|---|---|---|---|---|---|",
    ]
    for driver in drivers:
        for model in driver["models"]:
            firmware = ", ".join(f"`{item}`" for item in model["firmware_snapshots"])
            lines.append(
                f"| `{driver['id']}` | `{model['model']}` | {model['instrument_class']} | "
                f"`{model['display_name']}` | {firmware} | `{driver['maturity']}` |"
            )

    for driver in drivers:
        lines.extend(
            [
                "",
                f"### `{driver['id']}` — {driver['display_name']}",
                "",
                f"Driver version: `{driver['version']}`.",
                "",
                "Transports:",
                "",
                "| Name | Support | Resource template |",
                "|---|---|---|",
            ]
        )
        for transport in driver["transports"]:
            lines.append(
                f"| `{transport['name']}` | `{transport['support']}` | "
                f"`{transport['resource_template']}` |"
            )

        lines.extend(["", "Scenario inputs:", ""])
        if driver["scenario_inputs"]:
            lines.extend(["| Kind | Support | Meaning |", "|---|---|---|"])
            for scenario in driver["scenario_inputs"]:
                lines.append(
                    f"| `{scenario['kind']}` | `{scenario['support']}` | "
                    f"{scenario['description']} |"
                )
        else:
            lines.append("None guaranteed by this driver descriptor.")

        for model in driver["models"]:
            lines.extend(["", f"#### `{model['model']}` options", ""])
            _append_tokens(lines, "Hardware features", model["available_hardware_features"])
            _append_tokens(lines, "Applications", model["available_applications"])
            fields = model["configuration_fields"]
            if fields:
                lines.extend(
                    [
                        "Configuration fields:",
                        "",
                        "| Field | Type | Default | Choices/range | Meaning |",
                        "|---|---|---|---|---|",
                    ]
                )
                for field in fields:
                    constraints = _constraints(field)
                    lines.append(
                        f"| `{field['name']}` | `{field['value_type']}` | "
                        f"{_format_value(field['default'])} | {constraints} | "
                        f"{field['description']} |"
                    )
            else:
                lines.extend(["Configuration fields: none.", ""])

        if driver["command_coverage"]:
            lines.extend(
                [
                    "Command coverage:",
                    "",
                    "| Model | Firmware | Implemented/documented | Report |",
                    "|---|---|---:|---|",
                ]
            )
            for coverage in driver["command_coverage"]:
                lines.append(
                    f"| `{coverage['model']}` | `{coverage['firmware']}` | "
                    f"{coverage['implemented']}/{coverage['documented']} | "
                    f"`{coverage['report']}` |"
                )

    lines.extend(["", END_MARKER])
    return "\n".join(lines)


def _append_tokens(lines: list[str], label: str, values: list[str]) -> None:
    if values:
        lines.extend([f"{label}:", "", *[f"- `{value}`" for value in values], ""])
    else:
        lines.extend([f"{label}: none.", ""])


def _format_value(value: Any) -> str:
    if value is None:
        return "none"
    return f"`{json.dumps(value, ensure_ascii=False)}`"


def _constraints(field: dict[str, Any]) -> str:
    parts: list[str] = []
    if field["choices"]:
        parts.append(", ".join(f"`{item}`" for item in field["choices"]))
    if field["minimum"] is not None:
        parts.append(f"minimum `{field['minimum']}`")
    if field["maximum"] is not None:
        parts.append(f"maximum `{field['maximum']}`")
    return "; ".join(parts) or "—"


def replace_generated_section(document: str, section: str) -> str:
    if START_MARKER not in document or END_MARKER not in document:
        raise ValueError("instrument catalog is missing generated-section markers")
    before, remainder = document.split(START_MARKER, 1)
    _, after = remainder.split(END_MARKER, 1)
    return before + section + after


def expected_artifacts() -> tuple[str, str]:
    artifact = build_catalog_artifact()
    document = DOCUMENT_PATH.read_text(encoding="utf-8")
    return render_json(artifact), replace_generated_section(document, render_markdown(artifact))


def check_catalog_artifacts() -> tuple[str, ...]:
    expected_json, expected_document = expected_artifacts()
    stale: list[str] = []
    if JSON_PATH.read_text(encoding="utf-8") != expected_json:
        stale.append(str(JSON_PATH.relative_to(REPOSITORY_ROOT)))
    if DOCUMENT_PATH.read_text(encoding="utf-8") != expected_document:
        stale.append(str(DOCUMENT_PATH.relative_to(REPOSITORY_ROOT)))
    return tuple(stale)


def write_catalog_artifacts() -> None:
    expected_json, expected_document = expected_artifacts()
    JSON_PATH.write_text(expected_json, encoding="utf-8")
    DOCUMENT_PATH.write_text(expected_document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="fail if generated files are stale")
    action.add_argument("--write", action="store_true", help="rewrite generated files")
    args = parser.parse_args()
    if args.write:
        write_catalog_artifacts()
        print("Updated catalog artifacts.")
        return 0
    stale = check_catalog_artifacts()
    if stale:
        print("Stale generated catalog artifacts: " + ", ".join(stale))
        print("Run: python tools/generate_catalog.py --write")
        return 1
    print("Catalog artifacts match runtime descriptors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
