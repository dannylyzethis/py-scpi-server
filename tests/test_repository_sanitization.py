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
        directories[:] = [name for name in directories if name not in SKIPPED_PARTS]
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
