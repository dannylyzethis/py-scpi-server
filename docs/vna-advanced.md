# VNA spectrum, distortion, phase-noise, and I/Q behavior

The advanced-application layer implements deterministic development workflows for Spectrum
Analyzer, Swept IMD, Modulation Distortion, Phase Noise, Differential I/Q, and wideband-I/Q option
branches. It uses the same channel addresses, scenario player, trigger notifications, data format,
error queue, and reset rules as the rest of the VNA emulator.

## Creating an application measurement

Real VNA programs normally create these measurement classes with `CALC:CUST:DEF`. The emulator
supports that entry point, selects the new measurement immediately, and routes every implemented
measurement class to its application engine. For example:

```text
CALC:CUST:DEF 'sa_meas','Spectrum Analyzer','B'
SENS:SA:BAND:RES 100kHz
SENS:SA:DET:FUNC PEAK
CALC:SA:DATA? TRACE
```

The accepted class names cover Spectrum Analyzer, Swept IMD, Intermodulation Distortion,
Modulation Distortion, Phase Noise, Differential I/Q, Wideband I/Q, Gain Compression, Noise
Figure, Scalar Mixer/Converter, and Vector Mixer/Converter workflows. The relevant model option
must be present. A disabled class reports `-113`; an unknown class reports `-224`.

`SENS:<class>:STAT` is also available as an emulator convenience for compact scenario tests. It is
not presented as a replacement for the VNA's documented custom-measurement creation command. A VNA
channel has one active advanced measurement class; activating another class changes that channel's
class instead of layering unrelated applications on one trace.

## Setup and deterministic result families

| Measurement class | Setup root | Scenario trace | Result examples |
| --- | --- | --- | --- |
| Spectrum Analyzer | `SENS:SA` | `spectrum.trace` | `spectrum.<result>` |
| Swept IMD | `SENS:IMD` | `imd.trace` | `imd.im3`, `imd.im5` |
| Modulation Distortion | `SENS:DIST` | `modulation_distortion.trace` | `modulation_distortion.evm` |
| Phase Noise | `SENS:PN` | `phase_noise.trace` | `phase_noise.<result>` |
| Differential I/Q | `SENS:DIQ` | `differential_iq.trace` | `differential_iq.phase` |
| Wideband I/Q | `SENS:IQ` | `wideband_iq.trace` | `wideband_iq.phase` |

The audited setup surface includes SA resolution/video bandwidth, detector and averaging; IMD
sweep, tone, frequency, normalization, port mapping, and IF-bandwidth controls; and
modulation-distortion carrier, filter, correlation, DUT-map, power-sweep, and linear-reference
settings. Phase-noise supports carrier, noise type, offset range, receiver, resolution-bandwidth
ratio, and averaging. DIQ supports frequency-range creation/editing/deletion, coupling metadata,
and project-owned parameter expressions. External configuration files are intentionally not read or
written by these workflows.

`CALC:<class>:DATA? <result>` returns a named scenario result in the current ASCII or binary data
format. Normal `CALC:DATA?` also returns the active application's main trace. A stream must be a
scalar or contain exactly the selected measurement's point count; corrupt types or lengths report
SCPI `-230`. Stable derived values are used when an optional result stream is absent so setup code
can run before a detailed DUT model is authored.

Phase-noise measurements expose a logarithmic offset-frequency X axis. DIQ uses its first configured
frequency range, while wideband-I/Q captures expose a bounded time axis. Swept IMD uses center/span
or start/stop according to its sweep mode. Modulation-distortion power sweeps expose the configured
power range. Application markers can set/query X, query Y, and find the maximum. Marker reads use
scenario `peek`, so inspecting a marker does not consume a queued DUT case.

Wideband I/Q availability is controlled by the selected emulator capability profile. `SENS:IQ` is
a project-defined extension for sample-rate and capture-time scenario control.

## Instrument semantics

- Commands pass through the option/capability gate and selected-measurement existence gate.
- Scenario streams obey the shared read, trigger, operation-complete, pause, step, reset, and
  end-of-stream policies.
- `*CLS` clears status and errors while preserving application configuration.
- `*RST` disables advanced classes and restores all setup defaults.
- Calibration/correction status is the static value `0`; calibration behavior and math are outside
  the product scope.
- Hardware FIFO/shared-memory export, external source discovery, continuous streaming, and
  manufacturer-specific setup files are outside the product scope.
