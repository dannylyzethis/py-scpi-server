# Instrument catalog and bench configuration reference

This is the complete inventory of repository-owned instrument profiles. Version 4.0.0 uses only
generic project-owned selectors and requires bench schema version 2. External JSON and CSV files may
provide their own reported identity without changing the selected behavior profile.

<!-- BEGIN GENERATED BUILT-IN CATALOG -->

## Built-in runtime catalog

This section is generated from the runtime driver descriptors. Do not edit it by hand; run
`python tools/generate_catalog.py --write` after changing a descriptor.

The built-in catalog contains 7 models across 3 drivers.

| Driver | Model | Class | Default reported model | Firmware | Maturity |
|---|---|---|---|---|---|
| `virtual-dmm` | `dmm` | DMM | `Virtual DMM` | `E.1.0` | `alpha` |
| `virtual-ps` | `ps-1-output` | PSU | `Virtual PS 1 Output` | `E.1.0` | `alpha` |
| `virtual-ps` | `ps-2-output` | PSU | `Virtual PS 2 Output` | `E.1.0` | `alpha` |
| `virtual-ps` | `ps-3-output` | PSU | `Virtual PS 3 Output` | `E.1.0` | `alpha` |
| `virtual-ps` | `ps-4-output` | PSU | `Virtual PS 4 Output` | `E.1.0` | `alpha` |
| `virtual-vna` | `vna-2-port` | VNA | `Virtual VNA 2 Port` | `E.1.0` | `alpha` |
| `virtual-vna` | `vna-4-port` | VNA | `Virtual VNA 4 Port` | `E.1.0` | `alpha` |

### `virtual-dmm` — Virtual DMM

Driver version: `4.0.0`.

Transports:

| Name | Support | Resource template |
|---|---|---|
| `raw-socket` | `implemented` | `TCPIP::{host}::{port}::SOCKET` |
| `vxi-11` | `implemented` | `TCPIP::{host}::INSTR` |
| `hislip` | `implemented` | `TCPIP::{host}::hislip0,{port}::INSTR` |

Scenario inputs:

| Kind | Support | Meaning |
|---|---|---|
| `scalar-reading` | `implemented` | Sequential voltage, current, resistance, capacitance, frequency, or period values. |

#### `dmm` options

Hardware features: none.

Applications: none.

Configuration fields: none.


### `virtual-ps` — Virtual power supply

Driver version: `4.0.0`.

Transports:

| Name | Support | Resource template |
|---|---|---|
| `raw-socket` | `implemented` | `TCPIP::{host}::{port}::SOCKET` |
| `vxi-11` | `implemented` | `TCPIP::{host}::INSTR` |
| `hislip` | `implemented` | `TCPIP::{host}::hislip0,{port}::INSTR` |

Scenario inputs:

None guaranteed by this driver descriptor.

#### `ps-1-output` options

Hardware features: none.

Applications: none.

Configuration fields: none.


#### `ps-2-output` options

Hardware features: none.

Applications: none.

Configuration fields: none.


#### `ps-3-output` options

Hardware features: none.

Applications: none.

Configuration fields: none.


#### `ps-4-output` options

Hardware features: none.

Applications: none.

Configuration fields: none.


### `virtual-vna` — Virtual Vector Network Analyzer

Driver version: `4.0.0`.

Transports:

| Name | Support | Resource template |
|---|---|---|
| `raw-socket` | `implemented` | `TCPIP::{host}::{port}::SOCKET` |
| `vxi-11` | `implemented` | `TCPIP::{host}::INSTR` |
| `hislip` | `implemented` | `TCPIP::{host}::hislip0,{port}::INSTR` |

Scenario inputs:

| Kind | Support | Meaning |
|---|---|---|
| `complex-trace` | `implemented` | Complex receiver or corrected trace samples with an optional stimulus axis. |
| `scalar-result` | `implemented` | Application summaries such as gain-compression results. |
| `event` | `planned` | Deterministic trigger, status, and fault events. |

#### `vna-2-port` options

Hardware features:

- `bias_tees`
- `direct_receiver_access`
- `internal_combiner`
- `internal_rf_switches`
- `noise_receiver`
- `pulse_control`
- `receiver_attenuators`
- `source_attenuators`

Applications:

- `arbitrary_waveform_generation`
- `basic_pulsed_rf`
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
- `spectrum_analysis`
- `time_domain`
- `wideband_iq`

Configuration fields:

| Field | Type | Default | Choices/range | Meaning |
|---|---|---|---|---|
| `source_count` | `integer` | `1` | minimum `1`; maximum `2` | Number of independent stimulus sources. |
| `hardware_features` | `string-list` | `["all"]` | `all`, `bias_tees`, `direct_receiver_access`, `internal_combiner`, `internal_rf_switches`, `noise_receiver`, `pulse_control`, `receiver_attenuators`, `source_attenuators` | Project-owned simulated hardware capabilities. |
| `applications` | `string-list` | `["all"]` | `all`, `arbitrary_waveform_generation`, `basic_pulsed_rf`, `embedded_lo`, `enhanced_time_domain`, `fast_cw`, `fixture_removal`, `frequency_converter`, `frequency_offset`, `gain_compression`, `integrated_pulsed_rf`, `intermodulation_distortion`, `measurement_uncertainty`, `modulation_distortion`, `n_port`, `noise_figure`, `performance_test`, `phase_noise`, `scalar_mixer`, `spectrum_analysis`, `time_domain`, `wideband_iq` | Project-owned functional application capabilities. |
| `frequency_minimum_hz` | `number` | `10000000` | minimum `1e-12` | Emulated instrument minimum frequency in hertz. |
| `frequency_maximum_hz` | `number` | `50000000000` | minimum `1e-12` | Emulated instrument maximum frequency in hertz. |

#### `vna-4-port` options

Hardware features:

- `bias_tees`
- `direct_receiver_access`
- `internal_combiner`
- `internal_rf_switches`
- `noise_receiver`
- `pulse_control`
- `receiver_attenuators`
- `source_attenuators`

Applications:

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

Configuration fields:

| Field | Type | Default | Choices/range | Meaning |
|---|---|---|---|---|
| `source_count` | `integer` | `2` | minimum `1`; maximum `2` | Number of independent stimulus sources. |
| `hardware_features` | `string-list` | `["all"]` | `all`, `bias_tees`, `direct_receiver_access`, `internal_combiner`, `internal_rf_switches`, `noise_receiver`, `pulse_control`, `receiver_attenuators`, `source_attenuators` | Project-owned simulated hardware capabilities. |
| `applications` | `string-list` | `["all"]` | `all`, `active_hot_parameters`, `arbitrary_waveform_generation`, `basic_pulsed_rf`, `differential_iq`, `embedded_lo`, `enhanced_time_domain`, `fast_cw`, `fixture_removal`, `frequency_converter`, `frequency_offset`, `gain_compression`, `integrated_pulsed_rf`, `intermodulation_distortion`, `measurement_uncertainty`, `modulation_distortion`, `n_port`, `noise_figure`, `performance_test`, `phase_noise`, `scalar_mixer`, `source_phase_control`, `spectrum_analysis`, `time_domain`, `true_mode_stimulus`, `wideband_iq` | Project-owned functional application capabilities. |
| `frequency_minimum_hz` | `number` | `10000000` | minimum `1e-12` | Emulated instrument minimum frequency in hertz. |
| `frequency_maximum_hz` | `number` | `50000000000` | minimum `1e-12` | Emulated instrument maximum frequency in hertz. |
Command coverage:

| Model | Firmware | Implemented/documented | Report |
|---|---|---:|---|
| `vna-2-port` | `E.1.0` | 393/393 | `reports/vna-coverage-vna-2-port-E.1.0.json` |
| `vna-4-port` | `E.1.0` | 393/393 | `reports/vna-coverage-vna-4-port-E.1.0.json` |

<!-- END GENERATED BUILT-IN CATALOG -->

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

The complete generated machine-readable inventory is
[instrument-options.json](instrument-options.json). Omitted configuration enables all capabilities
compatible with the chosen topology. Explicit incompatible selections fail composition before any
socket starts. The `frequency_maximum_hz` field has no fixed upper ceiling; it must be positive and
no lower than `frequency_minimum_hz`.

## Bundled CSV equipment

CSV selectors are normalized from the `Equipment` value. These profiles remain static/experimental;
in particular, Oscilloscope Type A and Type B currently expose the same canned command behavior and
do not claim distinct stateful capabilities. The repository includes 13 bundled CSV model IDs.

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
