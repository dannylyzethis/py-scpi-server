from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = (
    REPOSITORY_ROOT / "readme.md",
    REPOSITORY_ROOT / "TODO.md",
    REPOSITORY_ROOT / "roadmap.md",
    REPOSITORY_ROOT / "changelog.md",
    *sorted((REPOSITORY_ROOT / "docs").glob("*.md")),
    *sorted((REPOSITORY_ROOT / "examples").rglob("README.md")),
)


def test_documentation_local_links_resolve() -> None:
    broken: list[str] = []
    for document in DOCUMENTS:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (document.parent / relative).resolve().exists():
                broken.append(f"{document.relative_to(REPOSITORY_ROOT)} -> {target}")
    assert broken == []


def test_current_roadmap_does_not_claim_legacy_release_or_python_support() -> None:
    roadmap = (REPOSITORY_ROOT / "roadmap.md").read_text(encoding="utf-8")
    todo = (REPOSITORY_ROOT / "TODO.md").read_text(encoding="utf-8")
    changelog = (REPOSITORY_ROOT / "changelog.md").read_text(encoding="utf-8")

    assert "Current Status (v2.3)" not in roadmap
    assert "Python Compatibility**: 3.6+" not in roadmap
    assert "- [ ] `scpi-m5`" not in todo
    assert "- [ ] `scpi-m6`" not in todo
    assert "| 4.x | Active alpha | 3.10+ |" in changelog
    assert "## [Unreleased]" in changelog
