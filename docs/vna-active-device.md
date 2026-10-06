# VNA gain-compression and noise-figure behavior

The active-device layer models the parts of gain-compression and noise-figure applications that
ATE software normally controls: configuration, acquisition-shaped data arrays, summary results,
option gating, and SCPI errors. It is another processor in the shared VNA scenario pipeline; it does
not create a separate VNA-specific data source.

## Gain compression

Create and activate a gain-compression measurement through the normal custom-measurement path:

```text
CALC1:CUST:DEF 'gain_comp','Gain Compression','S21'
```

The standard setup hierarchy is `SENS<channel>:GCS...` (`GCSetup`). It configures acquisition mode,
compression method, port mapping, power limits, safe/smart sweep controls, and sweep point counts.
For example:

```text
SENS1:GCS:PMAP 1,2
SENS1:GCS:POW:STAR:LEV -20
SENS1:GCS:POW:STOP:LEV 0
SENS1:GCS:SWE:POW:POIN 101
SENS1:GCS:COMP:LEV 1
```

The older project-owned `SENS:GC...` compatibility tree remains available and uses the same
per-channel state. For example, `SENS:GCS:COMP:LEV 1` and `SENS:GC:COMP:DB 1` configure the same
threshold. The extra hierarchy `SENS:GAIN:GCS...` is not accepted; it returns undefined-header
error `-113`.

When gain compression is enabled, the configured power sweep becomes the selected trace's X axis
and the normal `CALC:DATA?` path uses the same gain data as the application. Settings that determine
the power axis, compression threshold, result selection, and port topology affect the deterministic
result. Safe/smart controls without a physical hardware equivalent are retained as validated,
queryable state so automation can configure and inspect them consistently.

`CALC:GC:DATA?` returns input power, output power, gain, or compression arrays. `CALC:GC:RES?`
queries return input power, output power, gain, and compression at the configured threshold, and
`CALC:GC:STAT?` reports whether the threshold was reached.

Use scenario streams named `gain_compression.gain` and optionally
`gain_compression.output_power`. A stream must contain either one scalar value or exactly the
configured compression-sweep point count. A wrong shape produces SCPI error `-230` instead of
silently inventing or truncating data.

## Noise figure

`CALC1:CUST:DEF 'noise','Noise Figure','NF'` creates, selects, and activates a noise-figure
measurement through the same application-aware path.

`SENS:NOIS` commands configure source power, measurement bandwidth, averaging count, temperature,
and application state. `CALC:NOIS:DATA?` returns noise figure, gain, Y-factor, or effective
temperature arrays. The scalar result queries return average noise figure or gain.

The behavioral setup surface also includes receiver selection and gain, compression checking,
impedance count, narrowband compensation, port mapping, source-pull state, ambient/source
temperature controls, and a deterministic sweep-time estimate. External hardware discovery,
external macro execution, calibration math, and hardware-specific file formats remain outside the
emulator scope.

Scenario streams can be named `noise_figure.nf`, `noise_figure.gain`,
`noise_figure.yfactor`, and `noise_figure.teffective`. Each trace must match the selected
measurement's point count. If an optional result stream is absent, the emulator derives a stable
fallback from the selected trace so basic control programs can still run.

## Instrument semantics

- The same read, trigger, operation-complete, end-of-stream, and reset policies used by all other
  scenarios apply to active-device results.
- Commands are available only when the model and selected application options permit them.
- Channel-addressed commands pass through the registry existence gate before execution.
- `*CLS` clears status and errors but preserves application configuration and scenario data.
- `*RST` restores disabled application defaults.
- Calibration/correction status always returns `0`. Calibration standards, ECal, correction math,
  and real VNA calibration state are intentionally outside this emulator's product scope.

The objective is behavioral parity for software development: an ATE program can configure the
application, trigger deterministic DUT cases, read realistic result shapes, and exercise its error
paths before physical hardware is available.
