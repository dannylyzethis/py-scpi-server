# Loading CSV and XLSX instrument definitions

Use `--load` when the instruments you want to emulate are described by the project's five-column
CSV format. It accepts either one definition file or a directory containing CSV files.

The commands below assume the environment used to install the downloaded ZIP or checkout is active.
On Windows, `Get-Command scpi-emulator` should point into that environment's `Scripts` directory;
otherwise a bare command may run an older installation from another location.

## Do I need quotes around the path?

Only when the path contains spaces. The quotes are interpreted by your command shell; they are not
part of the filename.

PowerShell examples:

```powershell
# No spaces: quotes are not needed.
scpi-emulator --load .\instruments --start
scpi-emulator --load .\one-instrument.csv --start

# Spaces: put double quotes around the entire path.
scpi-emulator --load "C:\ATE Projects\instrument definitions" --start
```

Linux and macOS shell examples:

```bash
# No spaces: quotes are not needed.
scpi-emulator --load ./instruments --start
scpi-emulator --load ./one-instrument.csv --start

# Spaces: put quotes around the entire path.
scpi-emulator --load "/opt/ATE Projects/instrument definitions" --start
```

Quotes are also allowed around a path without spaces, but they are unnecessary. Do not type angle
brackets around the path, and do not pass a wildcard such as `instruments/*.csv`; pass the directory
name instead.

## Load one file

```powershell
scpi-emulator --load .\one-instrument.csv --start
```

A single file may be `.csv` or `.xlsx`. XLSX support requires the optional Excel dependency:

```powershell
python -m pip install "scpi-emulator[excel]"
```

`--start` starts the raw TCP instrument servers. On success, the program prints each instrument's
ID and VISA resource string. Without `--start` or `--web`, the file is loaded and validated, but no
long-running server is started.

## Load a directory

```powershell
scpi-emulator --load .\instruments --start
```

Directory loading:

- reads every direct-child file whose extension is `.csv`, in case-insensitive filename order;
- does not search subdirectories;
- does not load XLSX files from the directory;
- combines every `Equipment` block from those files into one running set;
- rejects an empty directory and duplicate equipment names, normalized IDs, commands, or ports;
- reports both filenames when the same equipment name occurs in two files.

Put CSV files that must load first earlier alphabetically if automatic port order matters. Explicit
ports are honored. Blank ports are assigned sequentially beginning at `--port` (5025 by default),
skipping ports already assigned while the files are processed. If a later explicit port conflicts
with an earlier automatic assignment, loading fails instead of silently changing either instrument.

For example:

```powershell
scpi-emulator --load .\instruments --port 6000 --start
```

The first instrument without an explicit port receives port 6000, the next available automatic
port is 6001, and so on.

## CSV file format

The header row must contain these five names exactly (capitalization included):

```csv
Equipment,Port,Command,Response,Validation
```

An `Equipment` value starts a new instrument block. Leave `Equipment` and `Port` empty on following
rows to add more commands to the same instrument:

```csv
Equipment,Port,Command,Response,Validation
Virtual Meter,5025,*IDN?,"SCPI Emulator,Virtual Meter,SN001,E.1.0",
,,MEAS:VOLT?,1.2345,
,,VOLT (.+),OK,"range:0,10"
,,VOLT?,5.0,
```

The columns mean:

| Column | Meaning |
| --- | --- |
| `Equipment` | Instrument display name. A non-empty value starts a new instrument block. |
| `Port` | Optional TCP port on the first row of a block. Leave blank for automatic assignment. |
| `Command` | Exact command or a parameterized command containing `(.+)`. |
| `Response` | Text returned by the command. It may be empty for a write-only command. |
| `Validation` | Optional `bool`, `range:min,max`, or `enum:A,B,C` rule. |

Commands cannot appear before the first equipment declaration. A port is valid only on an equipment
declaration row. A validation rule is valid only on a parameterized command.

Command headers are matched without regard to case, but free-form parameter text retains the exact
case supplied by the client. Enum and boolean values are normalized to their canonical uppercase
forms when stored. A semicolon inside a quoted parameter remains data; only a semicolon outside a
quoted string or binary block separates chained SCPI commands.

## Do CSV values need quotes?

This is separate from quoting the path on the command line. Inside the CSV file, surround a field
with double quotes when the field itself contains a comma, a line break, or a double quote.

For example, an identification response normally contains commas, so it must be one quoted CSV
field:

```csv
Virtual Meter,5025,*IDN?,"SCPI Emulator,Virtual Meter,SN001,E.1.0",
```

The `range` and `enum` validation forms also contain commas and therefore need quotes:

```csv
,,VOLT (.+),OK,"range:0,10"
,,MODE (.+),OK,"enum:DC,AC"
```

Ordinary fields without commas do not need quotes:

```csv
,,MEAS:VOLT?,1.2345,
```

To include a literal double quote inside a quoted CSV field, write it twice. A spreadsheet program
normally handles this escaping when it exports CSV.

## Scenario-backed responses

A response can read from a deterministic scenario stream by using this marker in the existing
`Response` column:

```csv
Equipment,Port,Command,Response,Validation
Queue Reader,5025,VALUE?,{{scenario:dut.value}},
```

The marker itself contains no comma, so CSV quotes are not required. When a scenario player is
attached, each query reads `dut.value` using that stream's advancement policy. Without an attached
scenario, the command safely returns `0`.

## Dashboard and bench mode

The dashboard flags work with either a file or a directory:

```powershell
scpi-emulator --load .\instruments --start --web
scpi-emulator --load .\instruments --start --web --web-host 127.0.0.1 --web-port 8081
```

The CLI prints the dashboard URL after it starts. Use `--bench bench.json` instead when you need a
saved, precisely addressed bench that mixes built-in drivers with CSV-defined instruments. `--load`
and `--bench` are mutually exclusive; choose one for a given invocation.

## Common failures

The loader exits before starting any instruments when it finds a problem. Its error names the file
or directory involved. Common causes are:

- the path does not exist or a path containing spaces was not quoted;
- the directory has no direct-child `.csv` files;
- a CSV header is missing, misspelled, or has extra columns;
- a comma-containing response or validation field was not quoted;
- two equipment blocks normalize to the same ID;
- two instruments request the same explicit port;
- a command is duplicated within one equipment block;
- a validation rule is malformed or placed on a non-parameterized command.

Run `scpi-emulator --create-example` to generate a small working CSV in the current directory.
