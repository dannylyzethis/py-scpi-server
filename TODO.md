# SCPI Emulator Development Roadmap

Beads (`bd`) is the source of truth for issue status, priority, ownership, and dependencies. This
file explains where to look; it deliberately does not duplicate open/closed checkboxes that become
stale.

## Current initiative

Repository cleanup and modularization is tracked by epic `scpi-38a`. Its children cover repository
truth, instrument/configuration extraction, runtime and CLI extraction, an offline lifecycle-safe
dashboard, CSV parser correctness, CI consolidation, example organization, generated catalog data,
formatting, and eventual removal of the transitional compatibility facade.

Use these commands for the live view:

```powershell
bd show scpi-38a
bd ready
bd blocked
bd graph scpi-38a
```

## Product destination

The project lets users discover available emulator drivers, assemble reusable virtual ATE benches,
and develop automation before physical equipment, rack space, or laboratory access is available.
The practical target is to complete roughly 80–90% of ordinary driver and test-sequence development
before final validation against real hardware.

Deterministic scenarios supply queued DUT-facing scalar readings, complex traces, tables, events,
and errors. Instrument drivers map those streams into their own SCPI, triggering, status, and data
semantics. Digital DUT behavior and development-board protocols remain a separate companion-project
concern.

## Working agreement

```powershell
bd ready
bd show <issue>
bd update <issue> --claim
# implement and verify the issue
bd close <issue> --reason "Implemented and verified ..."
```

Do not close an issue until its acceptance criteria and relevant tests, coverage, license, manifest,
package, and transport gates pass.
