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
