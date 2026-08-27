"""Run the same test and release-quality profiles used by hosted CI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str, executable: Path | None = None) -> None:
    command = [str(executable or sys.executable), *arguments]
    print(f"\n> {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)


def run_test_profile() -> None:
    run("-m", "pytest", "-ra")


def _venv_python(directory: Path) -> Path:
    name = "python.exe" if os.name == "nt" else "python"
    folder = "Scripts" if os.name == "nt" else "bin"
    return directory / folder / name


def _venv_cli(directory: Path) -> Path:
    name = "scpi-emulator.exe" if os.name == "nt" else "scpi-emulator"
    folder = "Scripts" if os.name == "nt" else "bin"
    return directory / folder / name


def _verify_wheel(wheel: Path, environment: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    required = {
        "scpi_emulator/templates/dashboard.html",
        "scpi_emulator/static/dashboard.css",
        "scpi_emulator/static/dashboard.js",
    }
    missing = required - names
    if missing:
        raise RuntimeError(f"wheel is missing packaged assets: {sorted(missing)}")
    license_names = {name for name in names if ".dist-info/licenses/" in name}
    for filename in ("LICENSE.md", "THIRD_PARTY_NOTICES.md", "dependencies.json"):
        if not any(name.endswith(filename) for name in license_names):
            raise RuntimeError(f"wheel is missing license artifact {filename}")

    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = _venv_python(environment)
    run("-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel), executable=python)
    cli = _venv_cli(environment)
    run("--version", executable=cli)
    run("--help", executable=cli)
    for bench in (
        "examples/virtual-bench.json",
        "examples/generic-vna-bench.json",
        "examples/csv/mixed/mixed-bench.json",
    ):
        run("--bench", bench, executable=cli)


def run_quality_profile() -> None:
    run("-m", "ruff", "check", "src", "tests", "tools")
    run("tools/check_licenses.py")
    run(
        "-m",
        "pytest",
        "--cov=scpi_emulator",
        "--cov-branch",
        "--cov-report=term-missing",
        "--cov-fail-under=82",
        "-q",
    )
    for model in ("vna-2-port", "vna-4-port"):
        run("tools/vna_manifest.py", "--model", model, "--firmware", "E.1.0")

    with tempfile.TemporaryDirectory(prefix="scpi-verify-") as temporary:
        root = Path(temporary)
        distribution = root / "dist"
        run("-m", "build", "--no-isolation", "--outdir", str(distribution))
        wheels = tuple(distribution.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one wheel, found {len(wheels)}")
        _verify_wheel(wheels[0], root / "installed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "profile",
        choices=("test", "quality"),
        help="test runs the portable suite; quality runs release and package gates",
    )
    return parser


def main() -> int:
    profile = build_parser().parse_args().profile
    if profile == "test":
        run_test_profile()
    else:
        run_quality_profile()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
