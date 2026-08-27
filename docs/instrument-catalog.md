# Instrument catalog and bench configuration reference

This is the complete inventory of repository-owned instrument profiles. Version 4.0.0 uses only
generic project-owned selectors and requires bench schema version 2. External JSON and CSV files may
provide their own reported identity without changing the selected behavior profile.

The built-in catalog contains 7 built-in models plus 13 bundled CSV model IDs.

## Built-in drivers

| Instrument | Driver | Model | Default reported model | Firmware |
|---|---|---|---|---|
| Digital multimeter | `virtual-dmm` | `dmm` | `Virtual DMM` | `E.1.0` |
| One-output power supply | `virtual-ps` | `ps-1-output` | `Virtual PS 1 Output` | `E.1.0` |
| Two-output power supply | `virtual-ps` | `ps-2-output` | `Virtual PS 2 Output` | `E.1.0` |
| Three-output power supply | `virtual-ps` | `ps-3-output` | `Virtual PS 3 Output` | `E.1.0` |
| Four-output power supply | `virtual-ps` | `ps-4-output` | `Virtual PS 4 Output` | `E.1.0` |
| Two-port vector network analyzer | `virtual-vna` | `vna-2-port` | `Virtual VNA 2 Port` | `E.1.0` |
| Four-port vector network analyzer | `virtual-vna` | `vna-4-port` | `Virtual VNA 4 Port` | `E.1.0` |

Every built-in driver implements `raw-socket`, `vxi-11`, and `hislip`. Use interactive `catalog`,
`catalog <driver>`, and `catalog <driver> <model>` commands to inspect the same contract.

## Bench schema version 2

Every instrument entry uses these fields:

| Field | Required | Meaning |
|---|---:|---|
| `id` | Yes | Unique runtime instance identifier. |
| `driver` | Yes | Catalog driver ID. |
| `model` | Yes | Generic behavior-profile selector owned by that driver. |
| `resource` | Yes | Implemented `transport`, `host`, and `port`. |
| `name` | No | Dashboard and bench display name. |
| `reported_model` | No | Custom second field returned by `*IDN?`; it does not change behavior. |
| `serial_number` | No | Custom third field returned by `*IDN?`. |
| `firmware` | No | Firmware token; omission uses `E.1.0`. |
| `configuration` | No | Driver-specific profile configuration. |

`reported_model` must be non-empty and cannot contain commas or line breaks. This preserves the
four-field SCPI identity shape. Instance IDs, resource endpoints, and nonblank serial numbers must
be unique within one bench.

```json
{
  "schema_version": 2,
  "name": "identity-example",
  "instruments": [
    {
      "id": "supply1",
      "driver": "virtual-ps",
      "model": "ps-3-output",
      "reported_model": "User Supplied Model",
      "serial_number": "PS-001",
      "resource": {"transport": "raw-socket", "host": "127.0.0.1", "port": 5025}
    }
  ]
}
```

Here `*IDN?` returns `SCPI Emulator,User Supplied Model,PS-001,E.1.0`, while the instrument still
uses the stateful three-output profile.

## Power-supply profiles

The model selector fixes the available output count. Each output independently owns voltage,
current, enable, protection, range, trip, and measurement state. `INST:CAT?` and
`SYST:CHAN:COUN?` reflect the selected profile. `*CLS` preserves output state; `*RST` resets all
available outputs and selects output 1.

## Generic VNA configuration

| Field | Type | Default | Validation |
|---|---|---|---|
| `source_count` | integer | 1 on `vna-2-port`; 2 on `vna-4-port` | 1 or 2 |
| `frequency_minimum_hz` | number | 10000000 | Positive and no greater than maximum |
| `frequency_maximum_hz` | number | 50000000000 | Positive, no less than minimum, and no fixed upper ceiling |
| `hardware_features` | string array | `["all"]` | `all` alone or listed feature IDs |
| `applications` | string array | `["all"]` | `all` alone or compatible application IDs |

Hardware feature IDs:

- `bias_tees`
- `direct_receiver_access`
- `internal_combiner`
- `internal_rf_switches`
- `noise_receiver`
- `pulse_control`
- `receiver_attenuators`
- `source_attenuators`

Application IDs:

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

The complete machine-readable inventory is [instrument-options.json](instrument-options.json).
Omitted configuration enables all capabilities compatible with the chosen topology. Explicit
incompatible selections fail composition before any socket starts.

## Bundled CSV equipment

CSV selectors are normalized from the `Equipment` value. These profiles remain static/experimental;
in particular, Oscilloscope Type A and Type B currently expose the same canned command behavior and
do not claim distinct stateful capabilities.

| Equipment value | Normalized CSV model ID |
|---|---|
| `Virtual DMM Type A` | `virtual_dmm_type_a` |
| `Virtual PS 1 Output` | `virtual_ps_1_output` |
| `Virtual Oscilloscope Type A` | `virtual_oscilloscope_type_a` |
| `Virtual Oscilloscope Type B` | `virtual_oscilloscope_type_b` |
| `Virtual Signal Generator Type A` | `virtual_signal_generator_type_a` |
| `Virtual VNA 2 Port CSV Basic` | `virtual_vna_2_port_csv_basic` |
| `Virtual DMM Type B` | `virtual_dmm_type_b` |
| `Virtual VNA 4 Port CSV Full` | `virtual_vna_4_port_csv_full` |
| `Virtual VNA 2 Port CSV Minimal` | `virtual_vna_2_port_csv_minimal` |
| `Virtual DMM CSV Example` | `virtual_dmm_csv_example` |
| `Debug Test Instrument` | `debug_test_instrument` |
| `Virtual VNA 2 Port CSV Static` | `virtual_vna_2_port_csv_static` |
| `Fixture Controller` | `fixture_controller` |

External CSV equipment names remain user-controlled. When selected through `csv-instruments`, a
bench-level `reported_model` can override only the second `*IDN?` field.

Use `scpi-emulator --load instruments/ --start` for a CSV-only folder or
`scpi-emulator --bench bench.json --start` for a precise mixed bench. See
[Virtual benches](virtual-benches.md) and [CSV loading](csv-loading.md).
