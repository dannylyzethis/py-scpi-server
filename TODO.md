# SCPI Emulator Development TODO

Beads (`bd`) is the source of truth for issue status, priority, and dependencies. This file is the compact human-readable roadmap.

## Start here

- [x] `scpi-001` — Create the canonical `src/scpi_emulator` package, project metadata, and CLI.
- [x] `scpi-002` — Characterize current behavior with regression tests before replacing it.
- [x] `scpi-003` — Repair malformed CSV profiles and introduce strict configuration validation.
- [ ] `scpi-201` — Pin the initial N5222B and N5242B models, firmware families, options, and application matrix.

These four tasks can proceed independently and establish the baseline for the rest of the work.

## Milestones

- [x] `scpi-m0` — Maintainable emulator foundation
- [ ] `scpi-m1` — SCPI and IEEE 488.2 core
- [ ] `scpi-m2` — Versioned PNA/PNA-X capability system
- [ ] `scpi-m3` — Base PNA measurement engine
- [ ] `scpi-m4` — All selected PNA application command families
- [ ] `scpi-m5` — VISA/LXI-compatible transports
- [ ] `scpi-m6` — Dashboard, operations, CI, and releases

## Critical path

1. Parser and typed command registry: `scpi-101` → `scpi-102`
2. Errors and status system: `scpi-103` → `scpi-104`
3. OPC and triggering: `scpi-105` → `scpi-106`
4. Output queues and binary blocks: `scpi-107`
5. PNA command manifest and capabilities: `scpi-201` → `scpi-202` → `scpi-203`
6. Base PNA engine: `scpi-301` → `scpi-302` → `scpi-303` → `scpi-304`
7. Application modules: `scpi-401` through `scpi-405`
8. Transports: `scpi-501` → `scpi-502` → `scpi-503`

## Daily workflow

```powershell
bd ready
bd show scpi-001
bd update scpi-001 --claim
# implement and verify the issue
bd close scpi-001
bd ready
```

Useful views:

```powershell
bd status
bd epic status
bd graph
bd blocked
```

Do not mark an issue complete until its acceptance criteria and relevant automated tests pass.
