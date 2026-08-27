# SCPI Instrument Emulator Roadmap

## Current baseline

Version 4.x supports Python 3.10 and newer. The current alpha includes:

- generic built-in DMM, power-supply, and two-/four-port VNA behavior profiles;
- catalog-visible CSV instruments and mixed version-2 virtual benches;
- raw socket, VXI-11, and HiSLIP transports;
- stateful SCPI/IEEE 488.2 errors, status, synchronization, triggering, and output queues;
- deterministic scalar, trace, table, event, and error scenarios;
- interactive and dashboard scenario control;
- audited royalty-free commercial/enterprise dependency licensing;
- Linux and Windows CI, coverage, packaging, manifest, PyVISA-Py, and container checks.

The command manifest is a verified project snapshot, not a claim to implement every command found on
every physical instrument.

## Near-term direction

The active repository-cleanup initiative is tracked by Beads epic `scpi-38a`. It will split the
current composition root into focused modules, make the dashboard self-contained and lifecycle-safe,
repair the two remaining CSV parser limitations, consolidate CI, and remove stale repository
material without changing supported instrument behavior.

After that foundation is simplified, feature work should prioritize:

1. additional generic instrument drivers and scenario adapters;
2. better bench creation and inspection through the interactive manager and dashboard;
3. coherent fault, timeout, overload, and recovery scenarios across instrument families;
4. broader verified command snapshots where real automation use cases require them;
5. repeatable remote and CI orchestration for complete virtual benches.

## Product boundary

The emulator is intended to move automation development earlier, not replace final validation on
physical equipment. Electrical accuracy, undocumented quirks, calibration correlation, and final
timing validation remain hardware responsibilities. Real calibration mathematics and proprietary
instrument state-file formats are explicitly out of scope.

Digital DUT behavior, firmware-facing registers, buses, and development-board protocols belong in a
separate companion project. The projects may later share scenario timing and orchestration without
coupling their cores.

## Live backlog

Beads is authoritative; this document intentionally contains no target dates or duplicated issue
status.

```powershell
bd ready
bd show scpi-38a
bd epic status
```
