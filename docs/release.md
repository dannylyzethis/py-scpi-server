# Build, verification, and release guide

This project treats a release as reproducible only when a clean checkout can install the package,
pass the behavior and compatibility gates, build a wheel, and start an instrument endpoint.

## Local release verification

From a clean checkout, create a virtual environment and run:

```powershell
python -m pip install -e ".[all,dev]"
python -m ruff check src tests tools
python tools/check_licenses.py
python -m pytest --cov --cov-report=term-missing --cov-fail-under=82
python tools/pna_manifest.py --model N5222B-EMU --firmware E.1.0
python tools/pna_manifest.py --model N5242B-EMU --firmware E.1.0
python -m build
python -m pip install --force-reinstall dist/*.whl
scpi-emulator --version
```

The two manifest commands fail if the checked-in documented snapshot has an implementation gap.
The tests also compare the generated model reports with the checked-in JSON reports, so stale
compatibility claims fail CI.

The license check resolves the installed dependency closure from the reviewed roots in
`licenses/dependencies.json`. It fails for an unreviewed package, version change, metadata-license
change, or policy-disallowed license. Review upstream license files before updating the inventory.
The built wheel must contain `LICENSE.md`, `THIRD_PARTY_NOTICES.md`, and the JSON inventory under
its `.dist-info/licenses/` directory.

## Container quick start

```powershell
docker build -t scpi-emulator .
docker run --rm -p 5555:5555 -p 5559:5559 scpi-emulator
```

The default image runs the two instruments in `scpi_instruments_example.csv` as a non-root user.
Override the command and mount a read-only profile to run another bench:

```powershell
docker run --rm -p 5025:5025 `
  -v "${PWD}/my-instruments.csv:/profiles/instruments.csv:ro" `
  scpi-emulator --load /profiles/instruments.csv --start --host 0.0.0.0
```

## CI guarantees

GitHub Actions runs the suite on Linux and Windows with the oldest and newest declared Python
families, executes the real PyVISA-Py VXI-11 INSTR smoke test, enforces at least 82% branch-aware
package coverage, validates both VNA manifests, builds and reinstalls the wheel, and queries the
Docker image over a real raw-SCPI socket. Each operating-system/Python job also enforces the
reviewed dependency-license policy.

## Release checklist

1. Update the version in `pyproject.toml` and `src/scpi_emulator/__init__.py` together.
2. Update `changelog.md` and any compatibility snapshot whose behavior changed.
3. Review and update the dependency inventory and third-party notice when any dependency changes.
4. Run the local verification commands above from a clean checkout.
5. Tag the verified commit as `v<version>` and publish the wheel and source archive from `dist/`.
6. Keep the manifest model, firmware, source provenance, and known limitations explicit; do not
   describe the growing snapshot as complete VNA coverage.
