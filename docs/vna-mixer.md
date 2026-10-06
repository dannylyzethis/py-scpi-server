# VNA frequency-offset and converter behavior

The converter layer is an option-enabled, per-channel processor in the shared VNA data pipeline. It uses
the same scenario trace as ordinary S-parameter reads, then applies frequency-offset, mixer,
segment, source-role, and embedded-LO configuration. It can compose with time-domain processing;
no application owns a private trace generator.

Create, select, and activate converter measurements through the normal custom-measurement path:

```text
CALC1:CUST:DEF 'scalar_mix','Scalar Mixer/Converter','S21'
CALC1:CUST:DEF 'vector_mix','Vector Mixer/Converter','S21'
```

The scalar form selects scalar conversion; the vector form selects vector conversion and requires
the frequency-converter application capability.

## Supported workflows

- `SENS:FOM` / `SENS:FOM:STAT` and numbered `RANG` commands create/delete ranges, name and
  select them, set input/output/LO roles, and define linear, logarithmic, CW, coupled, or segmented
  axes. FOM segments store bandwidth, frequency, per-port power, point-count, and sweep-time
  settings; enabled segments determine the returned axis and data length.
- The standard `SENS:MIX:INPUT`, `LO`, `IF`, and `OUTPUT` trees store fixed or swept frequency,
  fractional multiplier, sideband, and power settings. `STAGE` enables one or two independently
  addressed LOs; `PMAP`, `PHASE`, `REVERSE`, `NORMALIZE:POINT`, and `XAXIS` round-trip their
  behavioral setup. `APPLY` enables the configured mixer and makes the selected axis drive data.
- The earlier compact `SENS:MIX:STAT`, `FREQ:FIX`, `FREQ:LO`, `FREQ:IF`, and `MODE` commands remain
  supported and use the same state.
- `SENS:MIX:CONV:TYPE` distinguishes scalar and vector conversion. Vector mode requires the vector
  converter application option; unsupported combinations report a SCPI error.
- Numbered mixer segments have start/stop frequency, power, point count, add/delete/calculate, and
  catalog-count behavior. Scenario traces are deterministically resampled so returned data and axes
  always contain matching point counts.
- Indexed source roles represent RF, LO, IF, and disabled sources, bounded by the selected model's
  physical source count.
- Embedded-LO state, center, span, delta, normalization point, and tuning settings round-trip.
  Delta/span settings produce a repeatable LO estimate and deterministic vector-phase change;
  reset commands restore tuning defaults.
- `SENS:MIX:CAL:STAT?` and `SENS:FOM:CORR:STAT?` always return `0` by product decision. There is no
  calibration state, correction flag, or calibration mathematics in this subsystem.

## State and fidelity

`*CLS` preserves converter configuration. `*RST` restores disabled application defaults. Commands
are unavailable without the appropriate frequency-offset, scalar/vector converter, or embedded-LO
option. Nonexistent channel/measurement and segment/range addresses are rejected before their
handlers execute.

The arithmetic is deterministic behavioral emulation intended to exercise ATE control flow, data
shape, configuration, option branches, and error handling. It does not claim RF conversion or
calibration accuracy. External-source discovery/role assignment, hardware diagnostics, calibration
math, and vendor-specific mixer files are intentionally outside this product scope.
