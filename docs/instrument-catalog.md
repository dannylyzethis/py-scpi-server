# Instrument catalog and bench configuration reference

This document is the user-facing inventory for instruments that can be selected in a JSON bench.
The built-in catalog currently contains 4 built-in models plus 10 bundled CSV model IDs. A CSV
folder supplied beside a bench may add more models without changing the bench schema.

## Built-in drivers

| Instrument | Driver | Model | Firmware |
|---|---|---|---|
| Digital multimeter | `virtual-3446x` | `34461A-EMU` | `E.1.0` |
| Triple-output power supply | `virtual-triple-psu` | `E36312A-EMU` | `E.1.0` |
| Two-port vector network analyzer | `virtual-vna` | `VNA-2PORT-EMU` | `E.1.0` |
| Four-port vector network analyzer | `virtual-vna` | `VNA-4PORT-EMU` | `E.1.0` |

Each built-in driver implements `raw-socket`, `vxi-11`, and `hislip` transports. The interactive
command `catalog` lists drivers, `catalog virtual-vna` lists its models, and
`catalog virtual-vna VNA-4PORT-EMU` prints the driver-owned configuration contract.

## Bench fields

Every item in `instruments` uses the same fields:

| Field | Required | Meaning |
|---|---:|---|
| `id` | Yes | Unique instance name used by the emulator and dashboard. |
| `driver` | Yes | Catalog driver ID. |
| `model` | Yes | Model ID advertised by that driver. |
| `resource` | Yes | Object containing `transport`, `host`, and `port`. |
| `name` | No | Human-readable display name. |
| `serial_number` | No | Per-instance serial returned by `*IDN?`; use a unique value for duplicate models. |
| `firmware` | No | Firmware token; blank inherits the driver default. |
| `configuration` | No | Driver-specific object; blank inherits safe driver defaults. |

The `id`, resource endpoint, and nonblank serial numbers must be unique within one bench.

## Generic VNA configuration

The VNA driver has two project-owned models. Port count is part of the model and cannot be changed
through `configuration`.

| Field | Type | Default | Validation |
|---|---|---|---|
| `source_count` | integer | 1 on `VNA-2PORT-EMU`; 2 on `VNA-4PORT-EMU` | 1 or 2 |
| `frequency_minimum_hz` | number | 10000000 | Positive and no greater than the maximum |
| `frequency_maximum_hz` | number | 50000000000 | Positive and no less than the minimum; no fixed upper ceiling |
| `hardware_features` | array of strings | `["all"]` | `all` alone, or listed semantic feature IDs |
| `applications` | array of strings | `["all"]` | `all` alone, or listed semantic application IDs |

The frequency values are emulated instrument limits. They also initialize ordinary sweep start and
stop values. They are not merely an initial sweep request, so later SCPI sweep commands remain
bounded by them.

The semantic hardware IDs are:

- `bias_tees`
- `direct_receiver_access`
- `internal_combiner`
- `internal_rf_switches`
- `noise_receiver`
- `pulse_control`
- `receiver_attenuators`
- `source_attenuators`

The semantic application IDs are stored in [instrument-options.json](instrument-options.json).
They include:

- `active_hot_parameters`
- `arbitrary_waveform_generation`
- `basic_pulsed_rf`
- `differential_iq`
- `embedded_lo`
- `enhanced_time_domain`
- `fast_cw`
- `fixture_removal`
- `frequency_converter`
- `frequency_offset`
- `gain_compression`
- `integrated_pulsed_rf`
- `intermodulation_distortion`
- `measurement_uncertainty`
- `modulation_distortion`
- `n_port`
- `noise_figure`
- `performance_test`
- `phase_noise`
- `scalar_mixer`
- `source_phase_control`
- `spectrum_analysis`
- `time_domain`
- `true_mode_stimulus`
- `wideband_iq`

Four-port-only applications are omitted from the two-port model. Applications that require two
sources or a named hardware feature are enabled only when that capability exists. Explicit
application selections automatically include their software dependencies. An incompatible explicit
selection fails bench composition with a plain-language error.

The easiest all-capability VNA entry needs no configuration at all:

```json
{
  "id": "vna1",
  "driver": "virtual-vna",
  "model": "VNA-4PORT-EMU",
  "serial_number": "EMU-VNA-001",
  "configuration": {},
  "resource": {
    "transport": "raw-socket",
    "host": "127.0.0.1",
    "port": 5025
  }
}
```

An explicit 67 GHz two-port instrument with a selected application subset looks like this:

```json
{
  "id": "wideband_vna",
  "driver": "virtual-vna",
  "model": "VNA-2PORT-EMU",
  "serial_number": "EMU-VNA-067",
  "configuration": {
    "frequency_minimum_hz": 100000,
    "frequency_maximum_hz": 67000000000,
    "source_count": 2,
    "hardware_features": ["direct_receiver_access", "noise_receiver"],
    "applications": ["time_domain", "noise_figure"]
  },
  "resource": {
    "transport": "hislip",
    "host": "127.0.0.1",
    "port": 4880
  }
}
```

`*IDN?` reports the selected generic model. `*OPT?` reports readable topology, hardware, and
application tokens such as `PORTS-4`, `HW-NOISE-RECEIVER`, and `APP-TIME-DOMAIN`.

## Mixed built-in and CSV bench

CSV models become catalog-visible when their folder is supplied to the bench loader. The `model`
is the normalized Equipment name from the CSV. This example mixes a built-in DMM, a generic VNA,
and a CSV relay:

```json
{
  "schema_version": 1,
  "name": "mixed-ate-bench",
  "description": "Built-in and CSV instruments on one virtual bench.",
  "metadata": {},
  "instruments": [
    {
      "id": "meter1",
      "driver": "virtual-3446x",
      "model": "34461A-EMU",
      "serial_number": "EMU-DMM-001",
      "configuration": {},
      "resource": {"transport": "raw-socket", "host": "127.0.0.1", "port": 5025}
    },
    {
      "id": "vna1",
      "driver": "virtual-vna",
      "model": "VNA-4PORT-EMU",
      "serial_number": "EMU-VNA-001",
      "configuration": {},
      "resource": {"transport": "raw-socket", "host": "127.0.0.1", "port": 5026}
    },
    {
      "id": "relay1",
      "driver": "csv-instruments",
      "model": "bench_relay",
      "serial_number": "EMU-RELAY-001",
      "configuration": {},
      "resource": {"transport": "raw-socket", "host": "127.0.0.1", "port": 5027}
    }
  ]
}
```

## Bundled CSV equipment

| Equipment value | Normalized CSV model ID |
|---|---|
| `Virtual 34461A-EMU DMM` | `virtual_34461a_emu_dmm` |
| `Virtual Generic Single-Output PSU-EMU` | `virtual_generic_single_output_psu_emu` |
| `Virtual TDS2024B-EMU Scope` | `virtual_tds2024b_emu_scope` |
| `Virtual 33220A-EMU Generator` | `virtual_33220a_emu_generator` |
| `Virtual VNA-2PORT-EMU CSV Basic` | `virtual_vna_2port_emu_csv_basic` |
| `Virtual 8846A-EMU DMM` | `virtual_8846a_emu_dmm` |
| `Virtual VNA-4PORT-EMU CSV Full` | `virtual_vna_4port_emu_csv_full` |
| `Virtual VNA-2PORT-EMU CSV Minimal` | `virtual_vna_2port_emu_csv_minimal` |
| `Debug Test Instrument` | `debug_test_instrument` |
| `Virtual VNA-2PORT-EMU CSV Static` | `virtual_vna_2port_emu_csv_static` |

`Virtual 34461A-EMU DMM` occurs in two bundled files and therefore counts once among the 10 unique
CSV model IDs. Loading both files in one directory is intentionally rejected because duplicate
Equipment names are ambiguous.

Use `scpi-emulator --bench bench.json --start` for a precise saved composition. Use
`scpi-emulator --load instruments/ --start` when every instrument comes from CSV files and sequential
ports are sufficient. See [Virtual benches](virtual-benches.md) and [CSV loading](csv-loading.md).
The ready-to-run [`generic-vna-bench.json`](../examples/generic-vna-bench.json) demonstrates a
four-port 67 GHz configuration with all compatible applications.
