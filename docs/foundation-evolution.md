# From command responder to instrument emulator

This document explains what changed in the SCPI Instrument Emulator, why those changes matter, and
what the project is intended to become. It is written for both instrument users and software
developers; no knowledge of the internal Python code is required.

## The short version

The original server was useful as a configurable command responder. A CSV file associated command
text with response text, and the server returned those responses over a TCP socket. This was enough
for simple demonstrations, but it did not consistently behave like a real instrument.

The new foundation separates five responsibilities:

```text
Client program
    -> byte-safe SCPI parser
    -> typed command registry and validation
    -> instrument state, operations, status, and capabilities
    -> output queue and binary encoder
    -> raw TCP today; VISA/LXI transports later
```

The important change is not merely that more commands exist. Commands now interact through shared
instrument state. Errors affect the Standard Event Status Register. `*OPC` follows pending
operations. `*ESE` and `*SRE` promote events into the status byte. Trigger commands move acquisition
channels through defined states. `*CLS` clears status without erasing measurement configuration.
PNA option queries agree with the selected physical model and installed licenses.

That shared behavior is what turns a list of canned responses into an instrument emulator.

## What the original design did well

The original project established several useful ideas that remain part of the product:

- Multiple simulated instruments can listen on separate TCP ports.
- CSV and optional XLSX files make simple instruments quick to describe.
- SET/query pairs can retain values such as a voltage or mode.
- Range, enumeration, and boolean rules provide lightweight validation.
- A web dashboard can observe traffic and control local servers.
- Raw sockets make the emulator accessible from many languages and test frameworks.

Those features are still valuable. The work described here preserves them while adding a reliable
standards-oriented core underneath them.

## Why the old core could not support a full instrument

### Commands were mostly isolated callbacks

Each command was primarily a dictionary entry or regular expression. A callback could return a
value, but it had little structured knowledge of the rest of the instrument. This made it difficult
to express rules such as:

- a command exists only when an application license is installed;
- the number of valid ports depends on the hardware configuration;
- a sweep remains pending until a trigger arrives;
- an operation-complete event must update several status layers;
- a query response must remain in the output queue until the client reads it.

Adding more CSV rows would increase command count without solving those relationships.

### `*CLS` had destructive side effects

The most serious example was `*CLS`. Clearing status could rebuild or relink command handlers in a
way that lost live measurement values. Afterward, the instrument could stop answering normally until
the server was restarted.

That is not how a real instrument behaves. `*CLS` means clear status, not reset the measurement or
restart the instrument. The defect also made standard automation handshakes unsafe, because many
programs issue `*CLS` before enabling status events.

### Status and operation commands were placeholders

Commands such as `*ESE`, `*SRE`, `*STB?`, `*OPC`, `*OPC?`, and `*WAI` existed, but their responses did
not come from a complete event/status model. Returning `1` from `*OPC?` is easy; correctly relating an
operation to SESR bit 0, ESB, MSS/RQS, and destructive `*ESR?` reads is the difficult part.

### Text processing could corrupt valid SCPI data

The legacy dispatcher uppercased command text and split command chains at every semicolon. That can
change quoted string data and mistake a semicolon inside a string for a command separator. It also
could not safely carry arbitrary bytes in definite or indefinite binary blocks.

### Static profile rows could mask live behavior

A CSV row such as `SYST:ERR?` could replace the real error-queue handler with a constant “No error”
response. The underlying error was present, but the client could not retrieve it. Core commands now
take precedence through the typed registry, so static profile data cannot silently disable live
status behavior.

### PNA identity was only a label

The original PNA catalog could identify itself as an N5222B while advertising a 50 GHz range and
placeholder firmware. Port count, source count, hardware options, application licenses, and command
availability did not come from a single model definition. This made internally contradictory
instruments possible.

## The new core foundation

### 1. A maintainable Python package

The supported code now lives under `src/scpi_emulator`, with project metadata, a command-line entry
point, optional dependency groups, tests, and CI configuration. Earlier implementations are retained
under `legacy/` for reference instead of competing with the active package.

Configuration loading is transactional and strict. It rejects malformed headers, spilled CSV
fields, invalid ports, duplicate instruments or commands, and unsupported validation rules. If a
reload fails, the currently running configuration remains intact.

Why this is better: the emulator can evolve without ambiguity about which implementation is active,
and configuration errors fail before partially changing a running system.

### 2. A byte-safe SCPI parser

The parser represents a program message as structured commands, headers, indices, query forms, and
typed parameters. It understands:

- long and short SCPI mnemonics;
- numeric channel suffixes;
- quoted strings and comma-separated parameters;
- numbers with optional units;
- definite and indefinite binary blocks;
- multiple commands in one program message.

The replacement path preserves binary payload bytes instead of decoding and re-encoding them as
text.

Why this is better: handlers receive the data type the command actually declared, and binary
measurement/file transfers are possible without corruption.

Two known legacy paths still uppercase string parameters and split quoted semicolons. They remain as
explicit expected-failure tests until all CSV command dispatch has moved onto the structured parser.

### 3. A typed command registry

Commands can now declare:

- their mixed-case SCPI path and optional numeric indices;
- whether they are a command or query;
- parameter types, ranges, units, defaults, and enumerated values;
- required capabilities or an availability predicate;
- the handler that implements the behavior.

The registry produces deterministic SCPI errors for missing parameters, extra parameters, wrong
types, invalid values, out-of-range values, unavailable commands, and undefined headers.

Why this is better: validation and availability are part of the command definition instead of being
scattered across callbacks and regular expressions.

### 4. A bounded SCPI error queue

Errors are normalized into standard SCPI categories and stored in a finite FIFO queue. Queue
overflow is deterministic, `SYSTem:ERRor?` removes the oldest error, and
`SYSTem:ERRor:COUNt?` reports the live count. Error categories latch the corresponding Standard Event
Status bits.

The live error queries are registered in the typed core, so a static CSV response can no longer mask
queued errors.

Why this is better: test software can use the same error-draining pattern it uses with real
instruments and receive the error that actually occurred.

### 5. IEEE 488.2 status registers

The core now models:

- the Standard Event Status Register and Event Status Enable mask;
- the Service Request Enable mask;
- the status byte, including error, message-available, event-status, operation, and summary bits;
- operation and questionable status groups with condition, event, enable, positive-transition, and
  negative-transition registers;
- destructive event-register reads and nondestructive condition/status-byte reads.

`*CLS` clears queued errors and event latches while preserving ESE/SRE masks, live conditions, and
measurement settings. `*RST` is separate and resets instrument configuration. VISA Device Clear is
also separate: it aborts operations and clears communication state without pretending to be either
`*CLS` or `*RST`.

Why this is better: clear, reset, and transport-clear operations now have distinct meanings matching
the expectations of real automation systems.

### 6. Real OPC, ESE, SRE, and status-byte handshaking

Operations are tracked as pending, running, completed, aborted, or failed work. The synchronization
commands operate on that manager:

- `*OPC` records the operations that came before it and sets SESR bit 0 after they complete.
- `*OPC?` waits for prior operations and returns `1` without creating the OPC event.
- `*WAI` is an execution barrier; it is not treated as an alias for `*OPC`.
- `ABORt` terminates pending acquisition work deterministically.
- `*ESE 1` enables the OPC event to drive the Event Status Bit in the status byte.
- `*SRE 32` allows that event summary to drive the Master Status Summary/Request Service bit.

A traditional handshake can therefore work as follows:

```text
*CLS
*ESE 1
*SRE 32
<start an operation>
*OPC
<poll or serial-poll status>
*ESR?
```

When the operation finishes, SESR OPC is set, ESB appears in status-byte bit 5, and the enabled
summary appears in bit 6. With both bits present, `*STB?` returns 96. Reading `*ESR?` returns the OPC
event and clears it.

Why this is better: older but very reliable event-based handshaking now has real semantics rather
than timing-dependent canned responses. Transport-level asynchronous SRQ delivery is still planned;
the internal request state already exists for that transport work.

### 7. Triggered acquisition state machines

Acquisition channels now move through explicit states such as idle, armed, waiting for trigger,
sweeping, processing, complete, and aborted. The model supports:

- immediate, manual, external, and bus trigger sources;
- continuous, single, group, and hold sweep behavior;
- trigger delay and sweep time;
- averaging and averaging counts;
- operation-status updates while a channel waits, sweeps, or processes data;
- deterministic abort behavior.

Why this is better: `INITiate`, `TRIGger`, `*TRG`, `ABORt`, `*WAI`, and `*OPC` now describe one
coherent acquisition process.

### 8. Output queues and binary data

Query responses can be queued as bytes and read partially. The Message Available bit remains set
until the response has been fully consumed. The core also models:

- definite and indefinite IEEE binary blocks;
- ASCII and REAL32/REAL64 data formats;
- normal and swapped byte order;
- query-interrupted and query-deadlocked errors;
- bounded output queues;
- large socket transfers without truncation.

Why this is better: clients can exercise the same read loops, binary decoding, MAV polling, and query
error handling they use against physical instruments.

### 9. Versioned PNA and PNA-X capabilities

The initial compatibility baseline pins Keysight N5222B PNA and N5242B PNA-X behavior to the
A.20.25 documentation family, with A.20.25.04 as the reference firmware. Machine-readable profiles
describe physical frequency limits, hardware configurations, ports, sources, hardware features,
add-ons, application options, and prerequisites.

An immutable runtime profile drives all related queries, including:

- `*IDN?` and `*OPT?`;
- frequency minimum and maximum;
- port, source-port, source, and receiver counts;
- receiver access, low-frequency extension, and attenuator capabilities;
- installed-license and enabled-feature catalogs.

Impossible combinations are rejected when the profile is created. For example, a PNA cannot be
given the PNA-X noise receiver, a four-port application cannot be installed on a two-port profile,
and software prerequisites cannot be omitted.

The default model-faithful mode enables only explicitly installed application licenses. The
all-applications developer mode selects a capable physical configuration when one is not supplied
and enables every application compatible with that model and hardware. Both modes feed the same
typed command-availability gates, so an unlicensed application command is unavailable in strict
mode without making hardware or option queries contradictory in developer mode.

Keysight's traditional `*OPT?` aliases and modern license product numbers are modeled separately.
For example, an installed S93010B license is represented by option `010` in `*OPT?` while the license
catalog retains the product-qualified identifier.

Why this is better: model identity is no longer cosmetic. A selected model and option set determine
what the instrument says it contains and, as application modules are completed, which commands it
allows.

### 10. A documented-command manifest and coverage reports

The versioned PNA command manifest records syntax, model and firmware applicability, parameters,
responses, defaults, supersession metadata, and official documentation provenance. A coverage tool
compares that snapshot with the active typed registry and produces model-specific reports.

The current reports show 393 of 393 commands implemented for the expanded foundation, PNA
measurement-lifecycle, and sweep snapshot. This is not a claim that all PNA commands are complete. The
snapshot includes common synchronization, acquisition, identity, option and capability commands,
plus the first stateful channel, measurement, display, format, math, marker, limit, and equation
workflows, plus linear, logarithmic, CW, power, and segment sweep configuration. Named MMEM state
files now save and restore composition existence through a path-safe per-instrument JSON store;
they deliberately do not serialize sweep, hardware, scenario, or calibration state. Licensed
time-domain, gating, fixture-simulation, gain-compression, noise-figure, and Integrated Pulse
commands now process the same generic scenario traces.
The snapshot will continue expanding with applications.

Why this is better: “supported” becomes measurable against a named model, firmware family, and
documentation snapshot rather than being inferred from a collection of CSV rows.

## Before and now

| Area | Earlier behavior | Foundation behavior | Benefit |
| --- | --- | --- | --- |
| Command parsing | Uppercase/split strings and regex matching | Structured byte-safe parsing and typed parameters | Valid SCPI data is preserved |
| Errors | Loosely connected queue/static responses | Bounded FIFO tied to standard event bits | Realistic error handling |
| `*CLS` | Could erase state and break responses | Clears status while preserving configuration | Safe standard initialization |
| OPC | Immediate placeholder responses | Tracks prior asynchronous operations | Reliable completion handshakes |
| Status byte | Mostly independent values | Derived from queues, events, enables, and operations | Polling behaves like an instrument |
| Triggering | Canned command responses | Acquisition state machines | Commands affect real pending work |
| Query output | Direct strings | Byte queue, MAV, partial reads, binary blocks | Realistic client read behavior |
| PNA identity | Static model string | Validated model/configuration/license profile | Internally consistent capabilities |
| Coverage | Number of configured rows | Versioned documentation-to-code report | Gaps are visible and testable |

## What is deliberately not finished

The project is still an alpha. The foundation makes the remaining work possible, but it does not
replace that work.

The largest unfinished areas are:

1. Named-file state save/recall and measurement file operations. Real calibration math and internal
   calibration state are deliberately out of scope; correction status remains static.
2. Time domain, fixture simulation, mixers, embedded LO, gain compression, noise figure, pulse,
   spectrum analysis, IMD, modulation distortion, phase noise, and differential-IQ applications.
3. HiSLIP and discovery compatibility after the completed VXI-11 `INSTR` foundation.
4. Virtual front panels, fault injection, packaging, and release hardening.

Raw TCP and VXI-11 work today. VXI-11 bridges Device Clear, trigger, locking, abort, serial poll, and
the internal request state to asynchronous SRQ callbacks. HiSLIP and discovery remain future work.

## What the completed emulator should achieve

The finished system is intended to be a deterministic virtual test instrument, not merely a mock
server. A user should be able to choose a model, firmware snapshot, physical hardware configuration,
installed options, and fault scenario, then connect existing automation software with minimal or no
special cases.

At the product level, users should be able to browse the instrument families for which an emulator
driver is available and assemble them into a reusable virtual bench. A bench definition will name
each instrument, model, options, resource address, and scenario. The same definition should run on a
developer's computer, a remote host, or a CI worker. This lets ATE software development begin before
equipment is procured, before rack space is ready, and without requiring every developer to be
physically near the lab.

The practical target is to get ordinary driver and test-sequence development roughly 80–90% of the
way to completion. Physical instruments will still be required for final validation, timing,
electrical behavior, undocumented quirks, and measurement correlation. The emulator's value is to
move most software work earlier, make it parallel with hardware procurement, and make failures easy
to reproduce.

### Scenario-driven measurement data

The emulator will support deterministic DUT scenarios rather than returning only constants or
procedurally generated data. A scenario contains named streams of values and defines when each stream
advances. For example:

- a DMM can return queued readings representing nominal voltage, gradual drift, a limit failure, and
  recovery;
- a power meter or supply can return ordered scalar measurements and status changes;
- a base PNA measurement can return successive complex traces with their stimulus axes;
- PNA applications such as gain compression can return coherent traces, scalar summaries, markers,
  and status for each scenario step;
- a scenario can inject an error, timeout, overload, unlock, or other instrument-visible condition at
  a defined point.

The shared scenario engine will support scalar values, vectors/traces, tables, events, and errors.
Each stream will have an explicit consumption policy, such as advance on read, advance after a
triggered operation, hold the final value, loop, or report exhaustion. Playback position, reset,
timing, and random seeds will be controlled so the same automation run can be reproduced.

Instrument drivers remain responsible for instrument semantics. For example, a DMM adapter maps a
scenario value into `READ?`, `FETCh?`, and status behavior, while a PNA adapter maps trace data into
the selected channel, measurement, format, byte order, trigger model, and OPC handshake. This keeps
scenario data generic without weakening the behavior of each emulated instrument.

### Scope boundary: instrument versus DUT emulation

This repository emulates test equipment and what that equipment observes. A separate companion
project can emulate a DUT's digital behavior, development-board interfaces, registers, buses, and
firmware-facing protocols. Keeping these as separate systems prevents either core from becoming tied
to one DUT or one bench, while still allowing future orchestration to start both systems with a
shared scenario and timeline.

For PNA and PNA-X profiles, completion means:

- identity, options, licenses, ports, sources, receivers, and command availability agree;
- sweeps and applications produce deterministic but physically meaningful data;
- synchronization, errors, status registers, and service requests behave consistently under load;
- ASCII and binary transfers exercise production parsing and read logic;
- calibration and file workflows retain state across realistic command sequences;
- unsupported commands fail exactly because of model, firmware, hardware, or license constraints;
- model-faithful mode exposes only valid capabilities;
- all-applications mode gives developers a deliberate superset for testing software branches;
- compatibility reports state exactly what has been implemented and validated;
- optional fault injection can reproduce timeouts, unlocks, overloads, bad calibrations, and other
  conditions that are difficult or expensive to create on real hardware.

The goal is not to replace physical measurement validation. It is to let teams develop automation,
drivers, user interfaces, data pipelines, recovery logic, and CI tests without requiring every
developer or build agent to have a costly instrument attached.

## Principles guiding the remaining work

- **Behavior before command count.** A smaller coherent subsystem is more useful than hundreds of
  unrelated canned responses.
- **One source of truth.** Identity, hardware, licenses, availability, and query results must derive
  from the same profile.
- **Real distinctions matter.** `*CLS`, `*RST`, Device Clear, `*OPC`, `*OPC?`, and `*WAI` are not
  interchangeable.
- **Determinism is a feature.** The same profile, state, trigger sequence, and seed should produce the
  same result unless a test intentionally injects variation.
- **Documented compatibility.** Every claim should name a model, firmware snapshot, option set, and
  source.
- **Legacy use remains possible.** CSV instruments continue to provide a quick way to emulate simple
  equipment while core and PNA behavior migrate to typed modules.
- **Tests protect instrument semantics.** Regressions are checked at parser, registry, state-machine,
  status, output-queue, profile, and socket levels.

## Current verification snapshot

As of 2026-08-22:

- 295 automated tests pass, with 2 expected failures documenting legacy CSV parser limitations.
- Two known legacy-parser limitations are retained as explicit expected failures.
- The N5222B and N5242B manifests each report 393/393 documented commands in the current snapshot.
- The maintainable foundation and IEEE/SCPI core milestones are complete.
- The versioned PNA capability milestone is complete.
- The first PNA measurement lifecycle is stateful: channels own uniquely named measurements,
  display traces feed those measurements, selected context remains coherent, and `*RST` restores
  the preset while `*CLS` and Device Clear preserve configuration.
- Channel stimulus produces coherent linear, logarithmic, CW, power, and segment axes; point count,
  IF bandwidth, and dwell determine the acquisition duration used by OPC/status handshakes.
- Selected measurements and receiver/SNP queries consume named complex traces from the generic
  scenario player with shared read, trigger, operation, end, seed, timing, and reset policies.
- Named MMEM save/recall files persist only channel, measurement, window, and trace existence.
  Registry predicates reject nonexistent addressed objects before their handlers can run, while
  malformed files are rejected before any live composition is changed.
- Licensed time-domain transforms, time gates, fixture file references, per-port de-embedding, and
  balanced topology settings alter the shared deterministic trace and X-axis pipeline.
- Frequency-offset ranges, scalar/vector conversion, mixer segments, source roles, and embedded-LO
  estimation compose with that same trace pipeline; calibration/correction status remains static 0.
- Gain-compression power sweeps and noise-figure arrays and summaries consume shared scenario
  streams with license, address-existence, trigger-policy, and malformed-data enforcement.
- Basic and Integrated Pulse applications model five generators, point/profile operation, IF
  filters and gate routing, time axes, timing constraints, and shared scenario trigger policies.
- The catalog-visible 34461A reference DMM consumes scalar streams through the same player, proving
  nominal, drift, range-failure, recovery, exhaustion, trigger/operation, fetch, and reset behavior
  on a second instrument shape.
- The instrument-driver/catalog contract can enumerate built-in and third-party emulator families
  without coupling them to the dashboard or future bench composer.
- The generic deterministic scenario engine provides shared scalar, trace, table, event, and error
  playback for future DMM, PNA, and additional instrument adapters.
- Raw socket transport now provides bounded binary-aware framing, one active session per instrument,
  configurable termination, timeouts, and clean shutdown on the standard port 5025.
- Versioned virtual bench files can select catalog instruments, validate configurations and resource
  conflicts, and start equivalent local, remote-hosted, or CI socket benches transactionally.
- Authenticated scenario controls can select, start, pause, manually step, reset, and inspect DUT
  playback per instrument; injected faults flow through the normal SCPI error/status registers. A
  runnable remote bench/client example reproduces the complete workflow over TCP and HTTP.

See [PNA compatibility baseline](pna-compatibility.md) for model and firmware details, and
[TODO.md](../TODO.md) or `bd ready` for the live implementation backlog.
