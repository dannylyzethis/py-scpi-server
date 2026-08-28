from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_one_workflow_uses_shared_verification_profiles_without_direct_pytest() -> None:
    workflows = sorted((REPOSITORY_ROOT / ".github" / "workflows").glob("*.yml"))
    assert [path.name for path in workflows] == ["ci.yml"]

    workflow = workflows[0].read_text(encoding="utf-8")
    assert "python tools/verify.py test" in workflow
    assert "python tools/verify.py quality" in workflow
    assert "python -m pytest" not in workflow
    assert 'python-version: "3.14"' in workflow
    assert "windows-latest" in workflow and "ubuntu-latest" in workflow
    assert "docker build" in workflow and "socket.create_connection" in workflow


def test_release_guide_uses_the_same_quality_entry_point() -> None:
    guide = (REPOSITORY_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
    assert "python tools/verify.py quality" in guide
    assert "python tools/verify.py test" in guide
    assert "python tools/generate_catalog.py --write" in guide
    assert "tools/ci_pytest.py" not in guide


def test_quality_profile_enforces_formatting_and_import_order() -> None:
    verification = (REPOSITORY_ROOT / "tools" / "verify.py").read_text(encoding="utf-8")
    assert 'run("-m", "ruff", "format", "--check", "src", "tests", "tools")' in verification
    assert 'run("-m", "build", "--outdir", str(distribution))' in verification
    assert "--no-isolation" not in verification
    assert "::error title=Verification command failed::" in verification

    configuration = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'select = ["E4", "E7", "E9", "F", "I"]' in configuration
    assert "*.py text eol=lf" in (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")
