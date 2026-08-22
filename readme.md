# SCPI Instrument Emulator

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](MIT%20License.md)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#current-limitations)

A stateful SCPI instrument emulator for developing and testing automation software without
physical instruments. The current alpha provides configurable raw-TCP instruments while a new
standards-oriented SCPI, IEEE 488.2, and PNA/PNA-X engine is developed.

For a detailed, plain-language explanation of the architecture changes and completed-system vision,
see [From command responder to instrument emulator](docs/foundation-evolution.md).
The extension boundary for discoverable instrument families is documented in
[Instrument driver and catalog contract](docs/driver-catalog.md).
The shared queued-data format and playback rules are documented in
[Deterministic scenario and queued-data engine](docs/scenario-engine.md).

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
- A UI-independent, plug-in-extensible instrument driver catalog with explicit model, firmware,
  transport, command-coverage, maturity, and scenario-input metadata.
- A thread-safe deterministic scenario engine for scalar, trace, table, event, and error streams,
  with read/trigger/operation policies, timing, resettable seeds, JSON, and binary containers.
- Optional Flask/Socket.IO monitoring dashboard.
- A packaged CLI plus parser, state-machine, status, profile, and socket integration tests.

## Current limitations

This release is an alpha foundation, not a complete instrument simulation. In particular:

- The transport is raw TCP, so VISA clients must use `::SOCKET` resources.
- VXI-11, HiSLIP, `::INSTR`, serial poll, and asynchronous SRQ are not implemented yet.
- Transport-level serial poll and asynchronous SRQ are not implemented yet, although the internal
  status and request state is modeled.
- Two legacy CSV dispatch paths still uppercase quoted string parameters and split semicolons inside
  quoted strings; typed core commands use the replacement parser.
- PNA commands currently return configured responses; behavioral PNA/PNA-X applications are on
  the tracked roadmap. The current 64-command manifest covers the foundation snapshot, not the
  complete Keysight command tree.
- The web dashboard is intended for trusted local development environments and is not currently
  hardened for untrusted networks.

See [TODO.md](TODO.md) and `bd ready` for the implementation backlog.

## Requirements

- Python 3.10 or newer.
- No third-party runtime dependency for CSV profiles and raw TCP operation.
- `openpyxl` through the `excel` extra for XLSX profiles.
- Flask and Flask-SocketIO through the `web` extra for the dashboard.

The project is tested with Python 3.14 during local development. CI covers the supported Python
versions declared in `pyproject.toml`.

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

## VISA and socket clients

The current server accepts newline-terminated SCPI text over raw TCP. Use VISA SOCKET resource
names, for example:

```text
TCPIP0::localhost::5555::SOCKET
TCPIP0::localhost::5559::SOCKET
TCPIP0::localhost::5560::SOCKET
```

Example with a plain TCP client:

```python
import socket

with socket.create_connection(("localhost", 5555), timeout=2) as client:
    client.sendall(b"*IDN?\n")
    print(client.recv(4096).decode().strip())
```

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
--port, -p PORT       First automatically assigned instrument port; default 5555
--host HOST           Instrument bind host; default localhost
--create-example      Write scpi_instruments_example.csv in the current directory
--interactive, -i     Open the manager shell
--verbose, -v         Enable debug logging
--log-file FILE       Also write logs to FILE
--version             Print the package version
```

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
