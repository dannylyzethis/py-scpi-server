"""Repository-level guards for neutral, project-owned source material."""

from __future__ import annotations

import os
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKIPPED_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
}
TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}


def _repository_files() -> list[Path]:
    files: list[Path] = []
    for root, directories, filenames in os.walk(REPOSITORY_ROOT):
        directories[:] = [
            name
            for name in directories
            if name not in SKIPPED_PARTS and not name.endswith(".egg-info")
        ]
        files.extend(
            Path(root) / filename
            for filename in filenames
            if Path(filename).suffix.lower() != ".db"
        )
    return files


def test_repository_has_no_manufacturer_manual_artifacts() -> None:
    prohibited_suffixes = {".chm", ".doc", ".docx", ".pdf"}
    found = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in _repository_files()
        if path.suffix.lower() in prohibited_suffixes
    ]
    assert found == []


def test_repository_text_uses_neutral_equipment_identity() -> None:
    prohibited_terms = (
        "key" + "sight",
        "agi" + "lent",
        "tek" + "tronix",
        "flu" + "ke",
        "roh" + "de",
        "sch" + "warz",
        "an" + "ritsu",
        "ri" + "gol",
        "kei" + "thley",
        "le" + "croy",
        "yoko" + "gawa",
        "tele" + "dyne",
        "national" + " instruments",
    )
    pattern = re.compile("|".join(re.escape(term) for term in prohibited_terms), re.IGNORECASE)
    found: list[str] = []
    for path in _repository_files():
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            found.append(str(path.relative_to(REPOSITORY_ROOT)))
    assert found == []


def test_documentation_does_not_explain_individual_application_ids() -> None:
    concrete_application_id = re.compile(r"\bE9\d{3,}[A-Z](?:/[A-Z])?\b")
    found: list[str] = []
    for path in _repository_files():
        if path.suffix.lower() != ".md":
            continue
        if concrete_application_id.search(path.read_text(encoding="utf-8")):
            found.append(str(path.relative_to(REPOSITORY_ROOT)))
    assert found == []


def test_public_model_labels_use_emulator_suffix() -> None:
    legacy_models = (
        "N5222" + "B",
        "N5242" + "B",
        "34461" + "A",
        "E36312" + "A",
        "TDS2024" + "B",
        "33220" + "A",
        "8846" + "A",
        "E5071" + "C",
        "8753" + "D",
    )
    pattern = re.compile(
        rf"\b(?:{'|'.join(re.escape(model) for model in legacy_models)})\b(?!-EMU)"
    )
    found: list[str] = []
    for path in _repository_files():
        if ".beads" in path.parts:
            # Tracker exports and interaction logs are historical records, not
            # shipped instrument identities.
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(str(path.relative_to(REPOSITORY_ROOT)))
    assert found == []


def test_public_vna_profile_uses_project_owned_identity() -> None:
    profile_path = REPOSITORY_ROOT / "src/scpi_emulator/profiles/vna_capabilities.v1.json"
    text = profile_path.read_text(encoding="utf-8")
    assert '"reference_firmware": "E.1.0"' in text
    assert '"VNA-2PORT-EMU"' in text
    assert '"VNA-4PORT-EMU"' in text
    assert '"hardware_features"' in text
    assert '"applications"' in text


def test_tracked_text_has_no_legacy_vna_identity_or_coded_options() -> None:
    prohibited = re.compile(
        "|".join(
            (
                re.escape("N5222" + "B"),
                re.escape("N5242" + "B"),
                r"\b" + re.escape("PN" + "A") + r"(?:-X)?\b",
                r"\b" + "E" + r"93\d+[AB]\b",
            )
        ),
        re.IGNORECASE,
    )
    found: list[str] = []
    for path in _repository_files():
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if prohibited.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(str(path.relative_to(REPOSITORY_ROOT)))
    assert found == []
