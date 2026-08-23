# PNA time-domain and fixture behavior

The time-domain application layer consumes the same deterministic complex trace streams used by
`CALC:DATA? SDAT`, receiver data, and SNP queries. It does not create a private data generator.
That keeps scenario playback, triggering, OPC completion, reset, and exhaustion policies identical
between frequency-domain and application workflows.

## Supported behavior

- `CALC:TRAN:TIME:STAT`, `TYPE`, and `WIND` control an inverse discrete transform, response type,
  and deterministic frequency-domain window.
- `CALC:FILT:TIME:STAT`, `STAR`, `STOP`, and `TYPE` apply band-pass or notch time gates. When the
  time display is disabled, the gated response is transformed back to the frequency domain.
- `CALC:FSIM:STAT` enables fixture processing. Per-port `SEND:DEEM:PORT:USER:FIL` and
  `SEND:EMB:PORT:USER:FIL` references and port states round-trip through SCPI; balanced,
  single-ended/balanced, and mixed-mode topology selections change the deterministic correction.
- SDATA, FDATA, physical receiver data, SNP data, and the X-axis pass through the same application
  processor. Point count remains coherent with the selected measurement and sweep.
- Application state is per channel. `*CLS` preserves it, while `*RST` returns it to disabled defaults.
- A missing channel or selected measurement is rejected before the handler runs. Time-domain and
  fixture command families are unavailable unless their corresponding application option is
  installed in the PNA capability profile.

## Deliberate fidelity boundary

This is behavioral emulation for ATE software development, not calibrated metrology. The transform
uses a deterministic discrete Fourier model. Fixture filenames produce stable complex correction
factors so that selecting a different fixture changes results repeatably; the emulator does not yet
parse Touchstone fixture networks or reproduce Keysight's proprietary fixture algorithms. The
filename and enable workflows are realistic enough to exercise configuration, branching, recall,
error handling, and downstream data processing without claiming measurement accuracy.

The existence-only files behind `MMEM:STOR:STAT` remain intentionally narrow: they do not serialize
time-domain, gate, fixture, sweep, scenario, hardware, or calibration state.
