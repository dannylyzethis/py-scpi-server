# PNA gain-compression and noise-figure behavior

The active-device layer models the parts of gain-compression and noise-figure applications that
ATE software normally controls: configuration, acquisition-shaped data arrays, summary results,
licensing, and SCPI errors. It is another processor in the shared PNA scenario pipeline; it does
not create a separate PNA-specific data source.

## Gain compression

`SENS:GC` commands configure a per-channel input-power sweep, point count, compression threshold,
reference, and application state. When enabled, the power sweep becomes the selected trace's X
axis and the normal `CALC:DATA?` path uses the same gain data as the application.

`CALC:GC:DATA?` returns input power, output power, gain, or compression arrays. `CALC:GC:RES?`
queries return input power, output power, gain, and compression at the configured threshold, and
`CALC:GC:STAT?` reports whether the threshold was reached.

Use scenario streams named `gain_compression.gain` and optionally
`gain_compression.output_power`. A stream must contain either one scalar value or exactly the
configured compression-sweep point count. A wrong shape produces SCPI error `-230` instead of
silently inventing or truncating data.

## Noise figure

`SENS:NOIS` commands configure source power, measurement bandwidth, averaging count, temperature,
and application state. `CALC:NOIS:DATA?` returns noise figure, gain, Y-factor, or effective
temperature arrays. The scalar result queries return average noise figure or gain.

Scenario streams can be named `noise_figure.nf`, `noise_figure.gain`,
`noise_figure.yfactor`, and `noise_figure.teffective`. Each trace must match the selected
measurement's point count. If an optional result stream is absent, the emulator derives a stable
fallback from the selected trace so basic control programs can still run.

## Instrument semantics

- The same read, trigger, operation-complete, end-of-stream, and reset policies used by all other
  scenarios apply to active-device results.
- Commands are available only when the model and selected application licenses permit them.
- Channel-addressed commands pass through the registry existence gate before execution.
- `*CLS` clears status and errors but preserves application configuration and scenario data.
- `*RST` restores disabled application defaults.
- Calibration/correction status always returns `0`. Calibration standards, ECal, correction math,
  and real PNA calibration state are intentionally outside this emulator's product scope.

The objective is behavioral parity for software development: an ATE program can configure the
application, trigger deterministic DUT cases, read realistic result shapes, and exercise its error
paths before physical hardware is available.
