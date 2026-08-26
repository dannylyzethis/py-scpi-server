# Generic VNA capability profiles

The built-in VNA driver is based entirely on project-owned capability metadata in
[`vna_capabilities.v1.json`](../src/scpi_emulator/profiles/vna_capabilities.v1.json).
It models observable automation behavior rather than a commercial product configuration.

## Models

| Model | Ports | Default sources | Default frequency limits |
|---|---:|---:|---:|
| `VNA-2PORT-EMU` | 2 | 1 | 10 MHz to 50 GHz |
| `VNA-4PORT-EMU` | 4 | 2 | 10 MHz to 50 GHz |

Both models use firmware identity `E.1.0`. Bench JSON may choose one or two sources and may replace
either frequency endpoint with any positive finite value, including ranges above 50 GHz. The
configured range becomes the instrument capability and the initial sweep range.

## Hardware and applications

Hardware is selected with `hardware_features`; applications are selected with `applications`.
Both fields default to `["all"]`, so a newly created VNA enables every capability compatible with
its port and source topology. An empty application array creates a core-only profile for testing
software behavior when application commands are unavailable.

Use the stable names listed in [instrument-options.json](instrument-options.json). These names are
functional emulator identifiers, not commercial option codes. Application dependencies are included
automatically. Explicit incompatible requests fail before the instrument starts.

## Identity and discovery

For this bench entry:

```json
{
  "id": "vna1",
  "driver": "virtual-vna",
  "model": "VNA-4PORT-EMU",
  "serial_number": "EMU-VNA-001",
  "configuration": {
    "frequency_minimum_hz": 100000,
    "frequency_maximum_hz": 67000000000,
    "source_count": 2,
    "hardware_features": ["all"],
    "applications": ["all"]
  },
  "resource": {"transport": "raw-socket", "host": "127.0.0.1", "port": 5025}
}
```

`*IDN?` returns `SCPI Emulator,VNA-4PORT-EMU,EMU-VNA-001,E.1.0`. `*OPT?` and the
capability/license catalogs return readable semantic tokens derived from the same immutable profile.
The dashboard reports the same model, source count, hardware features, applications, and range.

## Command coverage

The project-owned command manifest is
[`vna_commands.v1.json`](../src/scpi_emulator/profiles/vna_commands.v1.json). Regenerate generic
model reports with:

```powershell
python tools/vna_manifest.py --model VNA-2PORT-EMU --firmware E.1.0
python tools/vna_manifest.py --model VNA-4PORT-EMU --firmware E.1.0
```

Coverage describes implemented emulator behavior. It is not a claim of physical-product fidelity.
