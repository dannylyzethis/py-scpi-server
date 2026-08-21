# PNA compatibility baseline

Snapshot date: **2026-08-20**  
Matrix schema: **1**  
Keysight help family: **A.20.25.xx**  
Reference firmware: **A.20.25.04**, released 2026-07-15

The machine-readable source of truth is
[`pna_compatibility.v1.json`](../src/scpi_emulator/profiles/pna_compatibility.v1.json).
It describes Keysight capability, not the emulator's current implementation coverage.

## Initial models

| Model | Class | Frequency | Ports | Sources | Hardware configurations |
| --- | --- | --- | --- | --- | --- |
| N5222B | PNA | 10 MHz to 26.5 GHz | 2 or 4 | 1 on 2-port; 2 on 4-port | 200, 201, 205, 217, 219, 220, 400, 401, 405, 417, 419, 420 |
| N5242B | PNA-X | 10 MHz to 26.5 GHz | 2 or 4 | 1 or 2, configuration-dependent | 201, 205, 217, 219, 222, 224, 401, 417, 419, 422, 423, 425 |

The PNA intentionally lacks PNA-X internal RF switches, combiner, noise receiver, and rear-panel RF
path access. Those differences must affect both option queries and command availability.

## Application coverage target

The matrix inventories the application families to be represented by the capability system:
fixture removal; time-domain and enhanced time-domain; measurement uncertainty; pulsed RF; noise
figure; wideband IQ; phase noise; modulation distortion; waveform generation; frequency offset;
scalar and vector converter measurements; embedded-LO; gain compression; intermodulation distortion;
source phase control; differential/IQ; spectrum analysis; Fast CW; active/hot parameters; true-mode
stimulus; N-port; and performance test.

Application presence is conditional. A software option is not usable merely because its SCPI command
exists in the firmware. Model restrictions, port/source count, prerequisite software, hardware
configuration, synthesizer revision, and serial prefix remain capability gates.

## Firmware compatibility policy

- Exact version `A.20.25.04` is the initial verified documentation target.
- Other `A.20.25.xx` releases share this documentation family but are unverified until tested.
- Older firmware is unverified; its manifest must be pinned separately rather than silently inheriting
  newer commands.
- A newer help or firmware family requires a new dated snapshot and compatibility review. Existing
  snapshots are immutable except for factual corrections.
- Real-hardware conformance results take precedence over inferred behavior and are recorded against
  model, hardware configuration, installed options, and exact firmware.

## Official sources

- [N52xxB core firmware](https://www.keysight.com/us/en/lib/software-detail/instrument-firmware-software/n52xxb-pna-series-network-analyzer-firmware.html)
- [N52xxB programming command finder](https://helpfiles.keysight.com/csg/N52xxB/Programming/GP-IB_Command_Finder/Command_Finder.htm)
- [PNA/PNA-X configurations and options](https://helpfiles.keysight.com/csg/N52xxB/Support/Configurations.htm)
- [N5222B product page](https://www.keysight.com/us/en/product/N5222B/pna-microwave-network-analyzer-900-hz-10-mhz-26-5-ghz.html)
- [N5242B product page](https://www.keysight.com/us/en/product/N5242B/pna-x-microwave-network-analyzer-900-hz-10-mhz-26-5-ghz.html)

These sources are vendor-controlled and can change. The snapshot fields preserve the context used to
build the matrix, and the command manifest retains command-level provenance.

## Command manifest and coverage

[`pna_commands.v1.json`](../src/scpi_emulator/profiles/pna_commands.v1.json) is the first validated
command snapshot. Every entry carries documented syntax, model and firmware applicability, parameter
and response types, defaults, supersession metadata, and an official source identifier.

Generate a report against the runtime's typed registry and literal built-in commands with:

```powershell
python tools/pna_manifest.py --model N5222B --firmware A.20.25.04
```

The command exits with status 1 while documented gaps remain, making it suitable for an intentional
CI coverage gate. Add `--allow-gaps --output reports/pna-coverage-N5222B-A.20.25.04.json` to refresh a
checked-in report while preserving known gaps.

## Runtime capability profiles

`PNACapabilities.create()` binds a model to one physical hardware configuration, optional hardware
add-ons, installed application licenses, serial number, and firmware. The same immutable profile
drives `*IDN?`, `*OPT?`, `SYSTem:CAPability` frequency and hardware queries, port/source catalogs,
attenuator and receiver-access queries, and license/feature catalogs.

The default profiles are N5222B-200 and N5242B-201. Explicit profiles reject model-incompatible
hardware, missing application prerequisites, excluded configurations, and port/source constraints.
`*OPT?` reports traditional three-digit option aliases while `SYST:CAP:LIC:CAT?` reports installed
product-qualified licenses, matching the distinction made by Keysight firmware.
