# SCPI Instrument Emulator

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE.md)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#current-limitations)
[![CI](https://github.com/dannylyzethis/py-scpi-server/actions/workflows/ci.yml/badge.svg)](https://github.com/dannylyzethis/py-scpi-server/actions/workflows/ci.yml)

A stateful SCPI instrument emulator for developing and testing automation software without
physical instruments. The current alpha provides configurable raw-TCP, VXI-11, and HiSLIP
instruments on a standards-oriented SCPI, IEEE 488.2, and VNA foundation.

For a detailed, plain-language explanation of the architecture changes and completed-system vision,
see [From command responder to instrument emulator](docs/foundation-evolution.md).
The extension boundary for discoverable instrument families is documented in
[Instrument driver and catalog contract](docs/driver-catalog.md).
The shared queued-data format and playback rules are documented in
[Deterministic scenario and queued-data engine](docs/scenario-engine.md).
Multi-instrument selection, addressing, and startup are documented in
[Reusable virtual bench composition](docs/virtual-benches.md).
HiSLIP sessions and optional LXI discovery are documented in
[HiSLIP transport and LXI discovery](docs/hislip-discovery.md).
Dashboard state visibility and scenario/fault controls are documented in
[Dashboard control room](docs/dashboard.md).

## Current capabilities

- Multiple simultaneous raw-TCP instrument endpoints.
- Strict CSV and optional XLSX instrument definitions.
- Static command responses and inferred stateful SET/query pairs.
- A byte-safe SCPI parser and typed command registry with range, unit, enum, and type validation.
- A bounded FIFO error queue connected to IEEE 488.2 event and status registers.
- Active `*CLS`, `*OPC`, `*OPC?`, `*WAI`, ESE/SRE/status-byte, trigger, and acquisition behavior.
- Output queues with MAV, partial reads, query errors, and IEEE binary blocks.
- Project-owned `vna-2-port` and `vna-4-port` profiles with semantic source, hardware,
  application, identity, option, and license reporting. Defaults enable every compatible capability.
- Bench-defined VNA minimum/maximum frequency limits defaulting to 10 MHz–50 GHz, with explicit
  wider ranges supported because no physical product ceiling is imposed.
- Stateful VNA channel, measurement, display-window, trace, format, math, marker, limit, and
  equation workflows with indexed and abbreviated SCPI forms.
- VNA frequency, CW, power, and segment sweep axes with IF-bandwidth/dwell-derived acquisition
  timing, source-port power, and receiver attenuation.
- Scenario-backed VNA SDATA, FDATA, RDATA, SNP, and X-axis data in ASCII or IEEE binary formats,
  using the same deterministic playback engine as scalar instruments.
- Profile-gated VNA time-domain transforms, time gating, and deterministic fixture-removal/topology
  workflows that process those same scenario traces.
- Profile-gated frequency-offset ranges, scalar/vector converters, mixer segments, source roles, and
  embedded-LO behavior with coherent scenario-backed axes and data.
- Profile-gated gain-compression and noise-figure configuration, scenario-backed arrays, threshold
  results, and deterministic trigger behavior in the shared VNA data pipeline.
- Profile-gated basic and Integrated Pulse generators, point-in-pulse/profile traces, time axes, IF
  filters/gates, and shared deterministic trigger playback.
- Application-gated spectrum, swept IMD, modulation distortion, phase noise, differential I/Q, and
  wideband-I/Q workflows with scenario results, application axes, and markers.
- A catalog-visible Virtual DMM whose READ, FETCH, and MEASURE workflows consume
  queued scalar scenarios with function/range configuration and deterministic reset behavior.
- A UI-independent, plug-in-extensible instrument driver catalog with explicit model, firmware,
  transport, command-coverage, maturity, and scenario-input metadata.
- A thread-safe deterministic scenario engine for scalar, trace, table, event, and error streams,
  with read/trigger/operation policies, timing, resettable seeds, JSON, and binary containers.
- Authenticated remote scenario selection, pause/start/step/reset, playback inspection, and fault
  injection through the same SCPI error/status machinery observed by ATE clients.
- Versioned virtual bench files with catalog-backed model/configuration validation, unique resources,
  deployment-host overrides, and rollback-safe raw-TCP, VXI-11, or HiSLIP startup.
- HiSLIP 1.0 paired sessions with Device Clear, trigger, locking, status polling, OPC-driven SRQ,
  real PyVISA-Py interoperability, and optional LXI mDNS discovery.
- Optional Flask/Socket.IO control room with live SCPI registers, acquisition, channel/trace,
  scenario, deterministic-noise, and fault visibility and controls.
- A packaged CLI plus parser, state-machine, status, profile, and socket integration tests.

## Current limitations

This release is an alpha foundation, not a complete instrument simulation. In particular:

- Raw TCP supports VISA `::SOCKET`; VXI-11 and HiSLIP support VISA `::INSTR` resources. Both INSTR
  transports bridge Device Clear, bus trigger, locking, serial poll, and asynchronous SRQ.
- HiSLIP 2.0 TLS/SASL features and automatic discovery from the packaged CLI are not implemented;
  library users can enable standards-shaped `_hislip._tcp` and `_vxi-11._tcp` advertisements.
- CSV compatibility commands use the same quote- and binary-safe program-message boundaries as the
  typed core while preserving the original case of free-form parameters.
- VNA configuration, sweep/stimulus, base trace-data workflows, and the selected profile-gated
  application families are stateful, but complete behavioral coverage of every command in those
  large applications remains iterative. The current command manifest is a growing verified
  snapshot, not exhaustive command coverage.
- The dashboard now requires authentication for non-loopback binds, but it remains a development
  control plane rather than an internet-facing service.

See [TODO.md](TODO.md) and `bd ready` for the implementation backlog.
VNA state semantics are described in [VNA measurement workflows](docs/vna-measurements.md).
Scenario trace mapping is described in [VNA scenario data](docs/vna-scenario-data.md).
Scalar/DMM playback is described in [DMM scenario data](docs/dmm-scenario-data.md).
Time-domain and fixture behavior is described in
[VNA time-domain and fixture behavior](docs/vna-time-domain.md).
Frequency-offset and converter behavior is described in
[VNA frequency-offset and converter behavior](docs/vna-mixer.md).
Gain-compression and noise-figure behavior is described in
[VNA active-device behavior](docs/vna-active-device.md).
Pulse generator and Integrated Pulse behavior is described in
[VNA pulse behavior](docs/vna-pulse.md).
Spectrum, IMD, modulation-distortion, phase-noise, and I/Q behavior is described in
[VNA advanced-application behavior](docs/vna-advanced.md).
The end-to-end bench/scenario workflow is in
[Remote ATE development workflow](docs/remote-ate-workflow.md).

## Requirements

- Python 3.10 or newer.
- No third-party runtime dependency for CSV profiles and raw TCP operation.
- `openpyxl` through the `excel` extra for XLSX profiles.
- Flask and Flask-SocketIO through the `web` extra for the dashboard.
- `zeroconf` through the `discovery` extra for optional LXI mDNS/DNS-SD advertisement.

The project is tested with Python 3.14 during local development. CI covers the oldest and newest
declared Python families on both Linux and Windows.

## Installation

### Downloaded ZIP on Windows

Extract the ZIP completely, open PowerShell in the extracted directory, and create an isolated
environment for that copy:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ".[all]"
Get-Command scpi-emulator | Select-Object Source
scpi-emulator --version
```

`Get-Command` must point inside the extracted directory's `.venv\Scripts` folder. The bare
`scpi-emulator` examples below assume this environment remains activated. Extracting a newer ZIP
does not update an older executable already installed elsewhere on `PATH`.

If PowerShell policy prevents activation, use the environment's executable explicitly:

```powershell
.\.venv\Scripts\python.exe -m pip install ".[all]"
.\.venv\Scripts\scpi-emulator.exe --version
```

### Git checkout

```bash
git clone https://github.com/dannylyzethis/py-scpi-server.git
cd py-scpi-server

# Core CSV/raw-socket emulator
python -m pip install -e .

# All optional runtime features
python -m pip install -e ".[all]"
```

For development:

```bash
python -m pip install -e ".[all,dev]"
```

If this repository was installed before the bench and directory-loading CLI was added, update the
checkout and refresh that same Python environment before using the new flags:

```bash
git switch main
git pull --ff-only origin main
python -m pip install -e ".[all]"
python -m scpi_emulator --help
```

The final command must list `--load PATH`, `--bench FILE`, and `--version`. Using
`python -m scpi_emulator` also prevents a different, older `scpi-emulator` executable elsewhere on
`PATH` from hiding the updated checkout.

For the reproducible CI, Docker, coverage, compatibility-report, and release workflow, see
[Build, verification, and release guide](docs/release.md).

## Quick start

Before creating a bench, see the
[Instrument catalog and bench configuration reference](docs/instrument-catalog.md). It lists every
driver, model, accepted configuration field, hardware token, application-option token, default,
transport, serial rule, and copyable JSON block in one place.

Create the small example configuration:

```bash
scpi-emulator --create-example
```

Choose the startup path that matches the job:

- **Point at a folder of CSVs** for the simplest setup. Every CSV is loaded, ports are assigned
  sequentially, and every instrument starts:

```bash
scpi-emulator --load instruments/ --start
```

- **Define a precise multi-instrument bench** when mixing built-in and CSV drivers, preserving exact
  addresses, or saving a versioned bench definition:

```bash
scpi-emulator --bench examples/virtual-bench.json --start
```

The familiar single-file form is unchanged:

```bash
scpi-emulator --load examples/csv/basic --start
```

Quotes around the `--load` path are required only when the path contains spaces. For example,
`--load .\instruments` needs no quotes, while `--load "C:\ATE Projects\instruments"` does. See
[Loading CSV and XLSX instrument definitions](docs/csv-loading.md) for complete PowerShell and
POSIX examples, directory ordering and port assignment, the exact CSV layout, and CSV field-quoting
rules.

The same dashboard flags work with either startup path:

```bash
scpi-emulator --load instruments/ --start --web
scpi-emulator --bench examples/virtual-bench.json --start --web
```

After startup, the CLI prints every instrument ID and its VISA resource string. With `--web`, it
also prints the dashboard URL. CSV files used by a `--bench` definition are kept beside its JSON
file; this lets the bench mix `csv-instruments` profiles with built-in VNA, DMM, and one-through-four-output PSU
profiles without a
second CSV-directory flag.

Other useful entry forms:

```bash
python -m scpi_emulator --help
scpi-emulator --version
scpi-emulator --interactive
scpi-emulator --load examples/csv/catalog --start --verbose --log-file emulator.log
```

With no flags, the interactive manager can load either simple CSV definitions or a precise bench
and show every configured instrument before it is started:

```text
SCPI-MGR> load bench "C:\ATE Projects\benches\development bench.json"
SCPI-MGR> instruments
SCPI-MGR> catalog
SCPI-MGR> catalog virtual-vna vna-2-port
SCPI-MGR> create bench "C:\ATE Projects\benches\new bench.json"
SCPI-MGR> start
SCPI-MGR> status
SCPI-MGR> scenario load dmm1 examples/remote_ate/dut-cycle.json
SCPI-MGR> scenario start dmm1
SCPI-MGR> web
SCPI-MGR> stop
```

Quotes are required only when the path contains spaces. `bench <file>` is a shorter alias for
`load bench <file>`. The `instruments` output includes the instance ID, model, serial number,
running state, and VISA resource string.

Use `catalog` to list driver families, `catalog <driver>` to list that driver's models, and
`catalog <driver> <model>` to inspect firmware, transports, configuration fields, scenario inputs,
and command coverage. `catalog csv <folder>` temporarily includes every CSV-defined model in that
folder for browsing; quote the folder only when its path contains spaces.

`create bench <file>` starts a guided workflow over that same catalog. Press Enter for safe defaults,
add one or more instruments, preview their VISA resources, and confirm the save. The complete bench
is validated before an atomic write and is loaded immediately. Put any CSV definitions beside the
target JSON so their models are included automatically. Type `cancel` at any prompt to leave without
creating or replacing a file.

Scenario files can be selected while servers remain running. `scenario load <instrument> <file>`
validates and selects a scenario in the paused state; `scenario start`, `pause`, `reset`, `status`,
and `step` control playback. The file may end in `.json` or `.txt`; either way, its contents use the
same schema-1 JSON structure. Starter DMM and VNA files are in `examples/scenarios/`. The dashboard
provides the same per-instrument **Load file** control. Its stream dropdown applies deterministic
noise to an already-loaded stream.

Container quick start:

```bash
docker build -t scpi-emulator .
docker run --rm -p 5555:5555 -p 5559:5559 scpi-emulator
```

## VISA and socket clients

The current server accepts CR, LF, or CRLF-terminated SCPI messages over a bounded, binary-safe raw
TCP connection. Each instrument allows one active client session, matching normal instrument
ownership. VISA resource examples are:

```text
TCPIP0::localhost::5025::SOCKET
TCPIP0::localhost::5029::SOCKET
TCPIP0::localhost::5030::SOCKET
TCPIP0::localhost::inst0::INSTR
TCPIP0::localhost::hislip0::INSTR
```

Example with a plain TCP client:

```python
import socket

with socket.create_connection(("localhost", 5025), timeout=2) as client:
    client.sendall(b"*IDN?\n")
    print(client.recv(4096).decode().strip())
```

VXI-11 uses ONC RPC portmapper port 111 by default. A development server may expose its negotiated
core RPC port directly to PyVISA-Py with
`TCPIP0::127.0.0.1,<core-port>::inst0::INSTR`, which is useful in unprivileged CI environments.
HiSLIP uses TCP port 4880 by default. PyVISA-Py expresses a nonstandard development port as
`TCPIP0::127.0.0.1::hislip0,<port>::INSTR`.

## Configuration format

The complete beginner-oriented loading guide is
[Loading CSV and XLSX instrument definitions](docs/csv-loading.md).

CSV and XLSX definitions must contain exactly these columns:

| Column | Meaning |
| --- | --- |
| `Equipment` | Starts a new instrument; blank rows continue the current instrument. |
| `Port` | TCP port on a new instrument row; blank values are assigned from `--port`. |
| `Command` | Exact or legacy `(.+)` parameterized command. |
| `Response` | Returned text; may be empty for write-only commands. |
| `Validation` | Empty, `bool`, `range:min,max`, or `enum:A,B,C`. |

Quote every response or validation value containing commas:

```csv
Equipment,Port,Command,Response,Validation
Example DMM,5555,*IDN?,"Example Corp,test-dmm-profile,SN123,1.0",
,,VOLT (.+),OK,"range:0,10"
,,VOLT?,5,
```

The loader rejects malformed headers and rows, unknown columns, field overflow, invalid ports,
bad validation rules, and duplicate instrument IDs, ports, or commands. Folder loading also rejects
duplicate equipment names across files and identifies both conflicting files. A failed reload leaves
the active configuration unchanged.

The repository includes:

- `examples/csv/basic/scpi_instruments_example.csv` — two small development instruments.
- `examples/csv/catalog/detailed_instruments.csv` — nine generic static instrument command profiles, including DMM,
  one-output PSU, Oscilloscope Type A/B, signal-generator, and VNA examples.
- `examples/csv/vna/vna-commands.csv` — a static generic two-port VNA CSV catalog.
- `examples/csv/mixed/mixed-bench.json` and its adjacent CSV — a copyable mixed built-in/CSV bench.

## CLI reference

```text
--load, -l PATH       Point at one CSV/XLSX file or a folder of CSVs
--bench FILE          Define a precise multi-instrument bench from JSON
--start, -s           Start configured raw-TCP servers
--web, -w             Start the optional dashboard
--web-port PORT       Dashboard port; default 8081
--web-host HOST       Dashboard bind host; default 127.0.0.1
--port, -p PORT       First automatically assigned instrument port; default 5025
--host HOST           Instrument bind host; default localhost
--create-example      Write scpi_instruments_example.csv in the current directory
--interactive, -i     Open the manager shell
--verbose, -v         Enable debug logging
--log-file FILE       Also write logs to FILE
--version             Print the package version
```

The dashboard is local-only by default and uses only CSS and JavaScript packaged with the emulator;
it does not need a CDN or internet access. To bind it to a remote-facing address, set a strong
`SCPI_EMULATOR_WEB_TOKEN` environment variable and pass `--web-host`; remote API access then requires
that bearer token. Mutating API requests also use a per-process CSRF token rendered into the page.

## Development

Run the automated checks:

```bash
python tools/verify.py test
python tools/verify.py quality
```

The `test` profile is the portable OS/Python behavior suite. The `quality` profile is the complete
local release gate, including formatting, import order, lint, commercial-license review, branch
coverage, manifests, package contents, and installed-wheel CLI smoke tests.

Library users should import instrument, runtime, transport, dashboard, configuration, and CLI types
from their focused modules. See the [library API and migration guide](docs/library-api.md) for exact
paths. The old transitional `scpi_emulator.emulator` facade is not part of the 4.0 API.

Repository layout:

```text
src/scpi_emulator/       Supported package and CLI
tests/                   Regression and integration tests
examples/csv/            Independently loadable CSV examples and a mixed bench
tools/                   Configuration migration utilities
.beads/issues.jsonl      Exported implementation backlog
```

Work is tracked with Beads:

```bash
bd ready
bd show scpi-101
bd update scpi-101 --claim
bd close scpi-101
```

## License

Licensed under the MIT License. See [LICENSE.md](LICENSE.md) and
[third-party dependency notices](THIRD_PARTY_NOTICES.md).
