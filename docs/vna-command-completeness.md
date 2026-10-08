# VNA command-completeness audit

The independently inventoried, automation-relevant VNA command surface is
complete. Earlier reports answered two narrower questions correctly but were
easy to misread:

- the base manifest has an implementation key for each of its entries;
- the application inventory has an implementation key for each advertised
  set/query form.

Those project-authored inventories prove registered commands did not disappear.
They do not prove that the inventories contain the commands an ATE program
expects. The core audit closes that gap with a separately maintained command
inventory, explicit product classifications, executable probes, and a CI gate.

## Independent baseline

The core audit retains only command syntax and set/query classifications. No
manual paragraphs, product names, model names, option descriptions, or external
documents are stored in the repository.

The baseline contains 385 core command headers and 613 set/query forms. The
initial runtime matched 96 of the then-unclassified forms and was missing 487.
Every form has now been reviewed: 461 forms are in scope and 152 are explicit
product exclusions.

## Final coverage

Both generic VNA profiles match all 461 in-scope minimum and expanded header
forms. The audit also invokes 590 matching registry forms with generated typed
parameters in both full mnemonic spelling and minimum legal SCPI abbreviation.
All 1,180 runtime probes pass per profile.

Completed automation families include:

| Family | Present forms | In-scope forms |
| --- | ---: | ---: |
| Output | 4 | 4 |
| Source | 35 | 35 |
| Sweep setup | 39 | 39 |
| Segment sweep | 60 | 60 |
| Measurement data and analysis | 130 | 130 |
| Trigger and synchronization | 38 | 38 |
| Display and trace automation | 79 | 79 |
| System and instrument discovery | 47 | 47 |

Run the same executable gate locally with:

```powershell
py tools/vna_core_coverage.py --model vna-2-port
py tools/vna_core_coverage.py --model vna-4-port
```

The command fails if any in-scope minimum or expanded header is absent, or if a
generated full/minimum-mnemonic runtime probe fails. Checked-in reports under
`reports/` make the result reviewable without rerunning the tool.

## Completion rules

A command is not complete merely because a normalized registry key exists. The
gate proves all of the following where applicable:

1. Full mnemonic and minimum legal abbreviation both resolve.
2. Optional explicit headers resolve as well as their omitted form.
3. Normal typed parameters execute without an unexpected error.
4. Stateful set commands can be read back through their queries.
5. Channel, measurement, trace, marker, window, port, and segment addresses are
   existence-checked.
6. Data-producing commands use current deterministic scenario or measurement
   data.
7. Trigger and operation commands preserve OPC, ESR/ESE, STB/SRE, and error
   queue behavior.

Behavior-specific tests separately verify state changes, query results, invalid
parameter errors, deterministic data, and trigger/status interactions.

## Product boundaries

The goal is useful behavioral compatibility for developing ATE software before
hardware is available. It is not a physical-hardware simulator. The audit
records exclusions explicitly instead of implementing misleading no-op
commands:

- calibration and correction math are outside the product's behavioral scope;
- arbitrary filesystem, screenshot, and transfer commands are excluded for
  deterministic sandboxing and security;
- front-panel layouts, annotations, toolbars, touch controls, backlights,
  beepers, processor controls, and other physical-only controls do not affect
  remote automation state;
- identity/persona overrides are excluded so the generic model and
  bench-provided serial identity remain authoritative;
- legacy macro, shortcut, and obsolete interfaces are excluded.

The profile records the reason on every excluded command family, and the report
publishes counts by classification so exclusions cannot be mistaken for
implemented behavior.
