# VNA scenario data

The VNA does not have a separate DUT playback engine. It adapts the same immutable scenario
definitions and `ScenarioPlayer` used by scalar instruments, so a bench has one policy model for
queued values, triggers, completed operations, timing, exhaustion, looping, seeds, and reset.

## Mapping traces

A selected measurement first looks for an explicit binding, then for a case-insensitive stream name
matching its measurement name or parameter. For example, an S21 measurement automatically consumes
a trace stream named `S21`. Code can bind a developer-friendly measurement name to another stream:

```python
instrument.attach_scenario(definition)
instrument.pna_data.bind("Gain", "compression-run-1")
```

Each trace sample must contain exactly one complex value per configured sweep point. A missing,
exhausted, early, wrongly shaped, or wrong-length stream produces SCPI error `-230`; it never falls
back silently to unrelated canned data.

## SCPI data views

- `CALC:DATA? SDATA` and `CALC:DATA:SDATA?` return real/imaginary pairs per point.
- `FDATA` transforms the same complex trace through the selected display format. Polar-like formats
  return pairs; scalar formats return one value per point.
- `RDATA` exposes complex selected-measurement data, while `CALC:RDATA? A` resolves a named physical
  receiver stream.
- `CALC:MEAS:DATA:X?` returns the stimulus axis built by the channel sweep state.
- `CALC:DATA:SNP:PORTS? "1,2"` returns the stimulus column followed by S11, S21, S12, and S22 real
  and imaginary columns. Missing matrix elements are zero-filled, matching documented SNP behavior.

`FORM:DATA` and `FORM:BORD` control ASCII versus IEEE definite binary blocks, numeric width, and byte
order for every view. Startup and `*RST` select `ASCII` and normal byte order, matching the VNA;
`*CLS` and Device Clear do not change the selected transfer format.

## Advancement and reset

Read-policy streams advance after a data query. Trigger-policy streams advance when the acquisition
controller accepts a trigger. Operation-policy streams advance only after sweep processing completes,
which keeps OPC/status completion and scenario state aligned. Manual and timed behavior remains owned
by the generic player.

`*CLS` changes status reporting without moving scenario position. `*RST` rewinds the shared player to
its original seed and first sample while restoring the VNA measurement and sweep preset.
