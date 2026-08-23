# PNA emulator compatibility contract

Snapshot date: **2026-08-20**
Profile revision: **1.0**
Reference firmware token: **A.20.25.04**

The machine-readable source of truth is
[`pna_compatibility.v1.json`](../src/scpi_emulator/profiles/pna_compatibility.v1.json).
It is a project-owned emulator contract with no external manufacturer-source metadata.

Application identifiers use the emulator-owned `E9...` namespace as opaque configuration keys.
This documentation intentionally does not map them to commercial products or explain individual
identifiers.

## Initial models

| Model | Class | Frequency | Ports | Sources | Hardware configurations |
| --- | --- | --- | --- | --- | --- |
| N5222B | PNA | 10 MHz to 26.5 GHz | 2 or 4 | 1 on 2-port; 2 on 4-port | 200, 201, 205, 217, 219, 220, 400, 401, 405, 417, 419, 420 |
| N5242B | PNA-X | 10 MHz to 26.5 GHz | 2 or 4 | 1 or 2, configuration-dependent | 201, 205, 217, 219, 222, 224, 401, 417, 419, 422, 423, 425 |

The model tokens select different internal port, source, frequency, and feature shapes. They are
compatibility labels, not a claim that every behavior of physical hardware is reproduced.

## Internal command manifest and coverage

[`pna_commands.v1.json`](../src/scpi_emulator/profiles/pna_commands.v1.json) is the first validated
command contract. Every entry carries syntax, model and firmware applicability, parameter and
response types, defaults, supersession metadata, and the internal `emulator_contract` identifier.

Generate a report against the runtime's typed registry and literal built-in commands with:

```powershell
python tools/pna_manifest.py --model N5222B --firmware A.20.25.04
```

The command exits with status 1 while internal contract gaps remain. Add
`--allow-gaps --output reports/pna-coverage-N5222B-A.20.25.04.json` to refresh a checked-in report.

## Runtime capability profiles

`PNACapabilities.create()` binds a model to one physical hardware configuration, optional hardware
add-ons, installed application licenses, serial number, and firmware. The same immutable profile
drives `*IDN?`, `*OPT?`, `SYSTem:CAPability` frequency and hardware queries, port/source catalogs,
attenuator and receiver-access queries, and license/feature catalogs.

The default profiles are N5222B-200 and N5242B-201. Explicit profiles reject incompatible internal
configurations and unmet capability predicates.

Profiles have two explicit compatibility policies:

- `model-faithful` is the default. Only application licenses named by the user are enabled, and an
  impossible license/hardware combination is rejected.
- `all-applications` creates a developer profile. When a configuration is not specified, it chooses
  an internally compatible shape and enables all project-defined application capabilities.

Both policies feed the typed command registry's capability gates. Unavailable commands report SCPI
`-113, "Command unavailable"`; all capability responses derive from the same immutable profile.
