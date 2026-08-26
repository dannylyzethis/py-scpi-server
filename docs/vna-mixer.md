# VNA frequency-offset and converter behavior

The converter layer is a licensed, per-channel processor in the shared VNA data pipeline. It uses
the same scenario trace as ordinary S-parameter reads, then applies frequency-offset, mixer,
segment, source-role, and embedded-LO configuration. It can compose with time-domain processing;
no application owns a private trace generator.

## Supported workflows

- `SENS:FOM:STAT` and numbered `RANG` commands create/delete ranges, set input/output/LO roles,
  and define coherent start/stop axes.
- `SENS:MIX:STAT`, `FREQ:FIX`, `FREQ:LO`, `FREQ:IF`, and `MODE` model up/down conversion.
- `SENS:MIX:CONV:TYPE` distinguishes scalar and vector conversion. Vector mode requires the vector
  converter application license; unsupported combinations report a SCPI error.
- Numbered mixer segments have start/stop frequency, power, point count, add/delete/calculate, and
  catalog-count behavior. Scenario traces are deterministically resampled so returned data and axes
  always contain matching point counts.
- Indexed source roles represent RF, LO, IF, and disabled sources, bounded by the selected model's
  physical source count.
- Embedded-LO state, center, and span produce a repeatable LO estimate that changes translated axes
  and vector phase.
- `SENS:MIX:CAL:STAT?` and `SENS:FOM:CORR:STAT?` always return `0` by product decision. There is no
  calibration state, correction flag, or calibration mathematics in this subsystem.

## State and fidelity

`*CLS` preserves converter configuration. `*RST` restores disabled application defaults. Commands
are unavailable without the appropriate frequency-offset, scalar/vector converter, or embedded-LO
license. Nonexistent channel/measurement and segment/range addresses are rejected before their
handlers execute.

The arithmetic is deterministic behavioral emulation intended to exercise ATE control flow, data
shape, configuration, option branches, and error handling. It does not claim RF conversion or
calibration accuracy.
