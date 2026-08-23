# PNA spectrum, distortion, phase-noise, and I/Q behavior

The advanced-application layer implements deterministic development workflows for Spectrum
Analyzer, Swept IMD, Modulation Distortion, Phase Noise, Differential I/Q, and wideband-I/Q option
branches. It uses the same channel addresses, scenario player, trigger notifications, data format,
error queue, and reset rules as the rest of the PNA emulator.

## Creating an application measurement

Real PNA programs normally create these measurement classes with `CALC:CUST:DEF`. The emulator
supports that entry point and selects the new measurement immediately. For example:

```text
CALC:CUST:DEF 'sa_meas','Spectrum Analyzer','B'
SENS:SA:BAND:RES 100kHz
SENS:SA:DET:FUNC PEAK
CALC:SA:DATA? TRACE
```

The accepted class names are `Spectrum Analyzer`, `Swept IMD`, `Intermodulation Distortion`,
`Modulation Distortion`, `Modulation Distortion Converters`, `Phase Noise`, `Differential I/Q`, and
`Wideband I/Q`. The relevant model option must be present. An unlicensed class reports `-113`; an
unknown class reports `-224`.

`SENS:<class>:STAT` is also available as an emulator convenience for compact scenario tests. It is
not presented as a replacement for the PNA's documented custom-measurement creation command. A PNA
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

The documented setup subset includes SA resolution/video bandwidth, detector and averaging; IMD
sweep, tone, frequency, and IF-bandwidth controls; modulation-distortion carrier and symbol-rate
controls; phase-noise carrier, noise type, offsets, and averaging; and DIQ frequency-range creation,
editing, counting, and deletion.

`CALC:<class>:DATA? <result>` returns a named scenario result in the current ASCII or binary data
format. Normal `CALC:DATA?` also returns the active application's main trace. A stream must be a
scalar or contain exactly the selected measurement's point count; corrupt types or lengths report
SCPI `-230`. Stable derived values are used when an optional result stream is absent so setup code
can run before a detailed DUT model is authored.

Phase-noise measurements expose a logarithmic offset-frequency X axis. DIQ and wideband-I/Q
captures expose a time axis. Swept IMD uses its configured center and span. Application markers can
set/query X, query Y, and find the maximum. Marker reads use scenario `peek`, so inspecting a marker
does not consume a queued DUT case.

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
