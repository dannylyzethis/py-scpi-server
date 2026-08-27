# Build, verification, and release guide

This project treats a release as reproducible only when a clean checkout can install the package,
pass the behavior and compatibility gates, build a wheel, and start an instrument endpoint.

## Local release verification

From a clean checkout, create a virtual environment and run:

```powershell
python -m pip install -e ".[all,dev]"
python tools/verify.py quality
```

This is the same quality profile called by hosted CI. It runs Ruff, the reviewed commercial-license
policy, the complete branch-coverage suite, both VNA manifests, an isolated wheel/sdist build,
wheel-content checks, and installed-wheel CLI/bench smoke tests. For the portable behavior suite
alone, use `python tools/verify.py test`; the OS/Python matrix calls that exact profile.

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

One GitHub Actions workflow runs each configured OS/Python combination once. Linux covers Python
3.10–3.14 and Windows covers the oldest and newest versions; Linux 3.14 owns the `quality` profile
instead of duplicating the portable suite in another job. The full matrix exercises the real
PyVISA-Py VXI-11 INSTR behavior. The quality profile enforces at least 82% branch-aware coverage,
licenses, manifests, and the built wheel. A separate container job queries the image over a real
raw-SCPI socket.

## Release checklist

1. Update the version in `pyproject.toml` and `src/scpi_emulator/__init__.py` together.
2. Update `changelog.md` and any compatibility snapshot whose behavior changed.
3. Review and update the dependency inventory and third-party notice when any dependency changes.
4. Run the local verification commands above from a clean checkout.
5. Tag the verified commit as `v<version>` and publish the wheel and source archive from `dist/`.
6. Keep the manifest model, firmware, source provenance, and known limitations explicit; do not
   describe the growing snapshot as complete VNA coverage.
