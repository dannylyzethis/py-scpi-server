# VNA time-domain and fixture behavior

The time-domain application layer consumes the same deterministic complex trace streams used by
`CALC:DATA? SDAT`, receiver data, and SNP queries. It does not create a private data generator.
That keeps scenario playback, triggering, OPC completion, reset, and exhaustion policies identical
between frequency-domain and application workflows.

## Supported behavior

- `CALC:TRAN:TIME:STAT`, `TYPE`, and `WIND` control an inverse discrete transform, response type,
  and deterministic frequency-domain window. `STAR`, `STOP`, `CENT`, and `SPAN` control the
  returned time axis. `STIM`, `CLIP`, `KBES`, `IMP:WIDT`, `STEP:RTIM`, and marker mode/unit settings
  are stateful and queryable; Kaiser beta changes the deterministic window response. Time values
  accept seconds, milliseconds, microseconds, or nanoseconds (`S`, `MS`, `US`, or `NS`). The
  standard optional-type spelling (`CALC:TRAN:TIME BPAS`) is accepted as well as `:TYPE`.
- `CALC:FILT:TIME:STAT`, `STAR`, `STOP`, `CENT`, `SPAN`, `TYPE`, and `SHAP` configure a band-pass or
  notch time gate. The explicit `CALC:FILT:GATE:TIME:...` spelling is supported as an equivalent
  command path, including the optional-type form `CALC:FILT:GATE:TIME BPAS`. When the time display
  is disabled, the gated response is transformed back to the frequency domain.
- `CALC:FSIM:STAT` enables fixture processing. Per-port `SEND:DEEM:PORT:USER:FIL` and
  `SEND:EMB:PORT:USER:FIL` references and port states round-trip through SCPI; balanced,
  single-ended/balanced, and mixed-mode topology selections change the deterministic correction.
- SDATA, FDATA, physical receiver data, SNP data, and the X-axis pass through the same application
  processor. Point count remains coherent with the selected measurement and sweep.
- Application state is per channel. `*CLS` preserves it, while `*RST` returns it to disabled defaults.
- A missing channel or selected measurement is rejected before the handler runs. Time-domain and
  fixture command families are unavailable unless their corresponding application option is
  installed in the VNA capability profile.

## Deliberate fidelity boundary

This is behavioral emulation for ATE software development, not calibrated metrology. The transform
uses a deterministic discrete Fourier model. Fixture filenames produce stable complex correction
factors so that selecting a different fixture changes results repeatably; the emulator does not
parse external fixture-network files, perform calibration/correction math, or reproduce
manufacturer-specific state formats. The filename and enable workflows exercise configuration,
branching, recall, error handling, and downstream data processing without claiming measurement
accuracy.

The existence-only files behind `MMEM:STOR:STAT` remain intentionally narrow: they do not serialize
time-domain, gate, fixture, sweep, scenario, hardware, or calibration state.
