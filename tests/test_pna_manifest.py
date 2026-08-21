import json
from pathlib import Path

import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scpi import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ManifestError,
    command_spec_key,
    coverage_report,
    load_command_manifest,
)
from tools.pna_manifest import implementation_keys


REPOSITORY_ROOT = Path(__file__).parents[1]


def test_packaged_manifest_has_required_metadata_and_unique_commands() -> None:
    manifest = load_command_manifest()

    assert manifest.schema_version == 1
    assert manifest.snapshot["date"] == "2026-08-20"
    assert manifest.snapshot["help_revision"] == "A.20.25.xx"
    assert len(manifest.commands) >= 35
    assert {command.models for command in manifest.commands} == {
        frozenset({"N5222B", "N5242B"})
    }
    assert all(command.parameters is not None for command in manifest.commands)
    assert all("type" in command.response for command in manifest.commands)
    assert all(command.source_id in manifest.sources for command in manifest.commands)


def test_command_spec_key_is_stable_across_abbreviations() -> None:
    registry = CommandRegistry()
    specification = registry.register(
        CommandSpec(
            path=(
                HeaderNode("SENSe", index="channel", index_default=1),
                HeaderNode("SWEep"),
                HeaderNode("TIME"),
            ),
            handler=lambda invocation: None,
            query=True,
        )
    )

    assert command_spec_key(specification) == "SENSE<channel>:SWEEP:TIME?"
    assert registry.specifications == (specification,)


@pytest.mark.parametrize("model", ["N5222B", "N5242B"])
def test_coverage_report_closes_the_initial_option_query_gap(model: str) -> None:
    manifest = load_command_manifest()
    instrument = SCPIInstrument(f"Keysight {model}", model)

    report = coverage_report(
        manifest,
        implementation_keys(instrument),
        model=model,
        firmware="A.20.25.04",
    )

    assert report["summary"]["documented"] == len(manifest.commands)
    assert report["summary"]["missing"] == 0
    assert report["missing_command_ids"] == []
    assert report["summary"]["coverage_percent"] == 100


@pytest.mark.parametrize("model", ["N5222B", "N5242B"])
def test_checked_in_coverage_report_is_current(model: str) -> None:
    manifest = load_command_manifest()
    instrument = SCPIInstrument(f"Keysight {model}", model)
    expected = coverage_report(
        manifest,
        implementation_keys(instrument),
        model=model,
        firmware="A.20.25.04",
    )
    report_path = REPOSITORY_ROOT / "reports" / f"pna-coverage-{model}-A.20.25.04.json"

    assert json.loads(report_path.read_text(encoding="utf-8")) == expected


def test_manifest_rejects_duplicate_implementation_keys(tmp_path: Path) -> None:
    packaged = load_command_manifest()
    command = packaged.commands[0]
    raw = {
        "schema_version": 1,
        "snapshot": dict(packaged.snapshot),
        "sources": {key: dict(value) for key, value in packaged.sources.items()},
        "commands": [
            {
                "id": identifier,
                "syntax": command.syntax,
                "implementation_key": command.implementation_key,
                "models": sorted(command.models),
                "parameters": list(command.parameters),
                "response": dict(command.response),
                "defaults": dict(command.defaults),
                "supersedes": [],
                "source_id": command.source_id,
            }
            for identifier in ("duplicate.one", "duplicate.two")
        ],
    }
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ManifestError, match="implementation keys must be unique"):
        load_command_manifest(path)


def test_manifest_rejects_non_keysight_help_source(tmp_path: Path) -> None:
    raw = {
        "schema_version": 1,
        "snapshot": {
            "date": "2026-08-20",
            "help_revision": "A.20.25.xx",
            "firmware_pattern": "^A\\.20\\.25\\.[0-9]{2}$",
        },
        "sources": {"bad": {"title": "Unofficial", "url": "https://example.com"}},
        "commands": [],
    }
    path = tmp_path / "unofficial.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ManifestError, match="official Keysight help"):
        load_command_manifest(path)
