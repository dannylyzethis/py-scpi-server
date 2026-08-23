# SCPI Instrument Emulator

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](MIT%20License.md)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#current-limitations)
[![CI](https://github.com/dannylyzethis/py-scpi-server/actions/workflows/ci.yml/badge.svg)](https://github.com/dannylyzethis/py-scpi-server/actions/workflows/ci.yml)

A stateful SCPI instrument emulator for developing and testing automation software without
physical instruments. The current alpha provides configurable raw-TCP instruments while a new
standards-oriented SCPI, IEEE 488.2, and PNA/PNA-X engine is developed.

For a detailed, plain-language explanation of the architecture changes and completed-system vision,
see [From command responder to instrument emulator](docs/foundation-evolution.md).
The extension boundary for discoverable instrument families is documented in
[Instrument driver and catalog contract](docs/driver-catalog.md).
The shared queued-data format and playback rules are documented in
[Deterministic scenario and queued-data engine](docs/scenario-engine.md).
Multi-instrument selection, addressing, and startup are documented in
[Reusable virtual bench composition](docs/virtual-benches.md).

## Current capabilities

- Multiple simultaneous raw-TCP instrument endpoints.
- Strict CSV and optional XLSX instrument definitions.
- Static command responses and inferred stateful SET/query pairs.
- A byte-safe SCPI parser and typed command registry with range, unit, enum, and type validation.
- A bounded FIFO error queue connected to IEEE 488.2 event and status registers.
- Active `*CLS`, `*OPC`, `*OPC?`, `*WAI`, ESE/SRE/status-byte, trigger, and acquisition behavior.
- Output queues with MAV, partial reads, query errors, and IEEE binary blocks.
- Versioned N5222B PNA and N5242B PNA-X hardware, option, license, and capability profiles,
  including model-faithful and all-applications developer modes.
- Stateful PNA channel, measurement, display-window, trace, format, math, marker, limit, and
  equation workflows with indexed and abbreviated SCPI forms.
- PNA frequency, CW, power, and segment sweep axes with IF-bandwidth/dwell-derived acquisition
  timing, source-port power, and receiver attenuation.
- Scenario-backed PNA SDATA, FDATA, RDATA, SNP, and X-axis data in ASCII or IEEE binary formats,
  using the same deterministic playback engine as scalar instruments.
- Licensed PNA time-domain transforms, time gating, and deterministic fixture-removal/topology
  workflows that process those same scenario traces.
- A catalog-visible Keysight 34461A reference DMM whose READ, FETCH, and MEASURE workflows consume
  queued scalar scenarios with function/range configuration and deterministic reset behavior.
- A UI-independent, plug-in-extensible instrument driver catalog with explicit model, firmware,
  transport, command-coverage, maturity, and scenario-input metadata.
- A thread-safe deterministic scenario engine for scalar, trace, table, event, and error streams,
  with read/trigger/operation policies, timing, resettable seeds, JSON, and binary containers.
- Versioned virtual bench files with catalog-backed model/configuration validation, unique resources,
  deployment-host overrides, and rollback-safe multi-instrument socket startup.
- Optional Flask/Socket.IO monitoring dashboard.
- A packaged CLI plus parser, state-machine, status, profile, and socket integration tests.

## Current limitations

This release is an alpha foundation, not a complete instrument simulation. In particular:

- Raw TCP supports VISA `::SOCKET` resources; VXI-11 supports standard `::INSTR` resources,
  Device Clear, bus trigger, locking, serial poll, abort, and asynchronous SRQ.
- HiSLIP and network discovery are not implemented yet.
- Two legacy CSV dispatch paths still uppercase quoted string parameters and split semicolons inside
  quoted strings; typed core commands use the replacement parser.
- PNA configuration, sweep/stimulus, base trace-data workflows, and existence-only named state
  recall are stateful, but behavioral PNA/PNA-X applications remain on the roadmap. The
  current command manifest is a growing verified snapshot, not the complete Keysight tree.
- The dashboard now requires authentication for non-loopback binds, but it remains a development
  control plane rather than an internet-facing service.

See [TODO.md](TODO.md) and `bd ready` for the implementation backlog.
PNA state semantics are described in [PNA measurement workflows](docs/pna-measurements.md).
Scenario trace mapping is described in [PNA scenario data](docs/pna-scenario-data.md).
Scalar/DMM playback is described in [DMM scenario data](docs/dmm-scenario-data.md).
Time-domain and fixture behavior is described in
[PNA time-domain and fixture behavior](docs/pna-time-domain.md).

## Requirements

- Python 3.10 or newer.
- No third-party runtime dependency for CSV profiles and raw TCP operation.
- `openpyxl` through the `excel` extra for XLSX profiles.
- Flask and Flask-SocketIO through the `web` extra for the dashboard.

The project is tested with Python 3.14 during local development. CI covers the oldest and newest
declared Python families on both Linux and Windows.

## Installation

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

For the reproducible CI, Docker, coverage, compatibility-report, and release workflow, see
[Build, verification, and release guide](docs/release.md).

## Quick start

Create the small example configuration:

```bash
scpi-emulator --create-example
```

Start its instrument servers:

```bash
scpi-emulator --load scpi_instruments_example.csv --start
```

Enable the optional dashboard:

```bash
scpi-emulator --load scpi_instruments_example.csv --start --web
```

Other useful entry forms:

```bash
python -m scpi_emulator --help
scpi-emulator --version
scpi-emulator --interactive
scpi-emulator --load detailed_instruments.csv --start --verbose --log-file emulator.log
```

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

## Configuration format

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
Example DMM,5555,*IDN?,"Example Corp,DMM1000,SN123,1.0",
,,VOLT (.+),OK,"range:0,10"
,,VOLT?,5,
```

The loader rejects malformed headers and rows, unknown columns, field overflow, invalid ports,
bad validation rules, and duplicate instrument IDs, ports, or commands. A failed reload leaves the
active configuration unchanged.

The repository includes:

- `scpi_instruments_example.csv` — two small development instruments.
- `detailed_instruments.csv` — eight legacy instrument command catalogs.
- `pna-commands.csv` — the current static N5222B PNA catalog.

## CLI reference

```text
--load, -l FILE       Load a .csv or .xlsx definition
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

The dashboard is local-only by default. To bind it to a remote-facing address, set a strong
`SCPI_EMULATOR_WEB_TOKEN` environment variable and pass `--web-host`; remote API and WebSocket access
then requires that bearer token. Mutating API requests also use a per-process CSRF token rendered
into the dashboard page.

## Development

Run the automated checks:

```bash
python -m pytest -ra
ruff check src tests tools
```

The strict expected failures in `tests/test_instrument_behavior.py` document known legacy parser
defects. They should become passing tests as the replacement parser and IEEE core land.

Repository layout:

```text
src/scpi_emulator/       Supported package and CLI
tests/                   Regression and integration tests
tools/                   Configuration migration utilities
legacy/                  Archived pre-package implementations
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

Licensed under the MIT License. See [MIT License.md](MIT%20License.md).
