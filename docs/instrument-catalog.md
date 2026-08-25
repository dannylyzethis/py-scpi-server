# Instrument catalog and bench configuration reference

This is the user-facing source of truth for instruments that can be created in a bench. You should
not need to read Python source or internal profile JSON to discover a driver name, model name, or
accepted configuration field.

## Fastest choices

Use one of these driver/model pairs:

| Instrument | `driver` | `model` | Configuration required? |
| --- | --- | --- | --- |
| Reference DMM | `virtual-3446x` | `34461A-EMU` | No |
| Triple-output power supply | `virtual-triple-psu` | `E36312A-EMU` | No |
| VNA | `virtual-vna` | `N5222B-EMU` | Optional |
| Extended VNA | `virtual-vna` | `N5242B-EMU` | Optional |
| Instrument declared by CSV | `csv-instruments` | Normalized `Equipment` name | No |

All built-in models currently use firmware `E.1.0` and support `raw-socket`, `vxi-11`, and `hislip`.
For a first bench, use `raw-socket`.

To create the extended VNA with every compatible modeled application capability, use:

```json
{
  "id": "vna1",
  "name": "Development VNA",
  "driver": "virtual-vna",
  "model": "N5242B-EMU",
  "serial_number": "VNA-001",
  "configuration": {
    "mode": "all-applications"
  },
  "resource": {
    "transport": "raw-socket",
    "host": "127.0.0.1",
    "port": 5025
  }
}
```

`all-applications` chooses a coherent developer hardware profile and enables one compatible current
option token for every application capability that can coexist. It does not install mutually
incompatible hardware or both alternative tokens for the same capability.

## Common bench fields

Every object in the bench's `instruments` array uses these fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | Yes | Unique bench instance ID. Use letters, numbers, `_`, `-`, or `.`; no spaces. |
| `driver` | Yes | Driver ID from the table above. |
| `model` | Yes | Model ID advertised by that driver. |
| `resource` | Yes | Transport, host, and port used by clients. |
| `name` | No | Human-readable instance name; spaces are allowed. |
| `serial_number` | No | Per-instance serial returned as the third field of `*IDN?`. |
| `firmware` | No | Pinned firmware token. Omit it to use the model default. |
| `configuration` | Driver-specific | Configuration object documented under the selected driver below. |

Every `id` and every transport/host/port combination must be unique. Use a different
`serial_number` when creating two instances of the same model.

## `virtual-vna`

Models:

- `N5222B-EMU`
- `N5242B-EMU`

Accepted `configuration` fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `mode` | string | `model-faithful` | `model-faithful` or `all-applications`. |
| `hardware_configuration` | string | Model/mode default | One configuration token listed below. |
| `hardware_addons` | array of strings | Mode default | Zero or more add-on tokens listed below. |
| `application_options` | array of strings | Empty or automatic | Zero or more application-option tokens listed below. |

Use top-level `serial_number` for new bench files. The older driver-specific
`configuration.serial` field remains accepted for compatibility, but do not specify both with
different values.

### Compatibility modes

`model-faithful` is the default. It uses the default hardware configuration, no hardware add-ons,
and only application tokens explicitly listed in `application_options`.

`all-applications` is the easiest development setting. When hardware fields are omitted, it uses:

| Model | Hardware configuration | Hardware add-ons |
| --- | --- | --- |
| `N5222B-EMU` | `419` | `021` |
| `N5242B-EMU` | `425` | `021` |

You may explicitly choose hardware while using `all-applications`; the driver then enables every
application token compatible with that chosen shape. Invalid combinations fail before any bench
server starts.

### Hardware configuration tokens

| Model | Default in `model-faithful` | Accepted values |
| --- | --- | --- |
| `N5222B-EMU` | `200` | `200`, `201`, `205`, `217`, `219`, `220`, `400`, `401`, `405`, `417`, `419`, `420` |
| `N5242B-EMU` | `201` | `201`, `205`, `217`, `219`, `222`, `224`, `401`, `417`, `419`, `422`, `423`, `425` |

### Hardware add-on tokens

| Model | Accepted values |
| --- | --- |
| `N5222B-EMU` | `020`, `021`, `022`, `UNY` |
| `N5242B-EMU` | `020`, `021`, `022`, `UNY`, `XSB` |

These are emulator-owned opaque compatibility identifiers. This project intentionally does not map
them to commercial products or reproduce manufacturer option descriptions.

### Application option tokens

The complete per-model token lists are in
[`instrument-options.json`](instrument-options.json). That structured document also repeats mode
defaults, hardware configurations, and add-ons so it can be read by a person or tooling without
scraping Markdown.

Application tokens are intentionally opaque. Some require particular hardware or another option;
the driver validates those relationships. Copy selected values into the `application_options`
array of a `model-faithful` configuration. Use `all-applications` when you do not need to test a
specific installed-option combination.

## `virtual-3446x`

Model: `34461A-EMU`

This driver accepts no `configuration` fields. Omit `configuration` or use an empty object.

```json
{
  "id": "meter1",
  "name": "Development DMM",
  "driver": "virtual-3446x",
  "model": "34461A-EMU",
  "serial_number": "DMM-001",
  "resource": {
    "transport": "raw-socket",
    "host": "127.0.0.1",
    "port": 5025
  }
}
```

## `virtual-triple-psu`

Model: `E36312A-EMU`

This driver accepts no `configuration` fields. Each instance contains three independent outputs.
See [Triple-output power-supply emulator](power-supply.md) for its selected-output commands and
reset behavior.

```json
{
  "id": "supply1",
  "name": "Primary Power Supply",
  "driver": "virtual-triple-psu",
  "model": "E36312A-EMU",
  "serial_number": "PSU-001",
  "resource": {
    "transport": "raw-socket",
    "host": "127.0.0.1",
    "port": 5026
  }
}
```

## `csv-instruments`

When a bench contains `driver: "csv-instruments"`, the CLI scans every `.csv` file beside the bench
JSON. Every CSV `Equipment` block becomes an available model. No separate catalog file or CSV flag
is required.

The model ID is the equipment name converted to lowercase with punctuation replaced by underscores:

| CSV `Equipment` | Bench `model` |
| --- | --- |
| `Fixture Controller` | `fixture_controller` |
| `Power Supply A` | `power_supply_a` |
| `Virtual DMM #1` | `virtual_dmm_1` |

CSV instruments accept no `configuration` fields and only advertise `raw-socket`. The resource port
comes from the bench JSON; leave the CSV `Port` blank. A top-level `serial_number` overrides the
third field of `*IDN?`; a custom CSV identity must contain four comma-separated fields.

```json
{
  "id": "fixture1",
  "name": "Fixture Controller",
  "driver": "csv-instruments",
  "model": "fixture_controller",
  "serial_number": "FIXTURE-001",
  "resource": {
    "transport": "raw-socket",
    "host": "127.0.0.1",
    "port": 5027
  }
}
```

See [Loading CSV and XLSX instrument definitions](csv-loading.md) for the five-column CSV format.

### Bundled CSV model inventory

The repository root currently contains 11 CSV `Equipment` blocks representing 10 unique normalized
model IDs. These are legacy/experimental command catalogs; they do not claim the same stateful
fidelity as built-in drivers.

`detailed_instruments.csv` contains eight models:

| CSV equipment name | Bench `model` |
| --- | --- |
| `Virtual 34461A-EMU DMM` | `virtual_34461a_emu_dmm` |
| `Virtual Generic Single-Output PSU-EMU` | `virtual_generic_single_output_psu_emu` |
| `Virtual TDS2024B-EMU Scope` | `virtual_tds2024b_emu_scope` |
| `Virtual 33220A-EMU Generator` | `virtual_33220a_emu_generator` |
| `Virtual N5222B-EMU VNA` | `virtual_n5222b_emu_vna` |
| `Virtual 8846A-EMU DMM` | `virtual_8846a_emu_dmm` |
| `Virtual E5071C-EMU VNA` | `virtual_e5071c_emu_vna` |
| `Virtual 8753D-EMU VNA` | `virtual_8753d_emu_vna` |

`pna-commands.csv` contains one model:

| CSV equipment name | Bench `model` |
| --- | --- |
| `Virtual VNA N5222B-EMU` | `virtual_vna_n5222b_emu` |

`scpi_instruments_example.csv` contains two example models:

| CSV equipment name | Bench `model` |
| --- | --- |
| `Virtual 34461A-EMU DMM` | `virtual_34461a_emu_dmm` |
| `Debug Test Instrument` | `debug_test_instrument` |

The example DMM duplicates the same equipment/model ID in `detailed_instruments.csv`. Do not put
both files beside one CSV-backed bench; duplicate equipment is a deliberate hard error. Use a
dedicated bench directory containing only the CSV files that bench needs.

Counting unique selectable IDs across the project gives 4 built-in models plus 10 bundled CSV model
IDs, or 14. A bench directory containing only `detailed_instruments.csv` exposes the 4 built-ins plus
its 8 CSV models, or 12 catalog entries.

## Complete bench and validation

Wrap one or more instrument objects in the bench root:

```json
{
  "schema_version": 1,
  "name": "my-bench",
  "description": "DMM, power supply, and all-applications VNA.",
  "instruments": [
    {
      "id": "meter1",
      "driver": "virtual-3446x",
      "model": "34461A-EMU",
      "serial_number": "DMM-001",
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5025
      }
    },
    {
      "id": "supply1",
      "driver": "virtual-triple-psu",
      "model": "E36312A-EMU",
      "serial_number": "PSU-001",
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5026
      }
    },
    {
      "id": "vna1",
      "driver": "virtual-vna",
      "model": "N5242B-EMU",
      "serial_number": "VNA-001",
      "configuration": {
        "mode": "all-applications"
      },
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5027
      }
    }
  ]
}
```

This is valid JSON and can be copied directly. Use `examples/virtual-bench.json` for a smaller file
or `examples/mixed-bench.json` for a complete built-in/CSV composition.

Validate without opening ports, then start the bench:

```powershell
scpi-emulator --bench .\my-bench.json
scpi-emulator --bench .\my-bench.json --start
```

Composition fails before startup if a driver/model/firmware/transport is unknown, an option set is
incoherent, IDs or resource endpoints are duplicated, or a driver receives unsupported
configuration fields.

## Where the runtime catalog comes from

The catalog is assembled at runtime; it is not a second JSON file that users must maintain.
Built-in driver descriptors live in `src/scpi_emulator/drivers/`. CSV model descriptors are created
from the files beside a bench. The VNA's project-owned compatibility tokens are stored in
`src/scpi_emulator/profiles/pna_compatibility.v1.json` and are checked against this document by the
test suite.
