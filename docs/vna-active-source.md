# Active-source and multi-source applications

The virtual four-port VNA can compose three project-owned application behaviors with its
ordinary trace pipeline. These controls are intended for early automation development; they
do not reproduce a particular commercial instrument or its internal algorithms.

- `SENS:AHP:STAT ON` enables active hot-parameter data. When a scenario contains an
  `active_hot_parameters.trace` stream, that complex trace feeds both `CALC:AHP:DATA?` and
  normal `CALC:DATA?` reads. `SENS:AHP:BIAS:VOLT` and `SENS:AHP:BIAS:CURR` store deterministic
  bias settings for test-program round trips.
- `SOUR1:PHAS:STAT ON` and `SOUR1:PHAS:ANGL 90` rotate returned complex data by the enabled
  source phase. Each configured source contributes to the total rotation.
- `SENS:TMST:STAT ON` enables true-mode stimulus. `SENS:TMST:MODE DIFF|COMM` selects polarity,
  and `SENS:TMST:AMPL:RAT` applies an amplitude ratio to the returned trace.

All three command families are capability-gated. They remain configured across `*CLS`, while
`*RST` restores their defaults. The phase and true-mode controls compose with scenario-backed
active hot-parameter data and with the existing VNA data modifiers.

The standard `SENS<channel>:ACTive...` setup hierarchy is also available. It covers display
interpolation, trace input-power selection, port mapping, phase-point count, power sweep
start/stop/steps, sweep type, and tuning-tone mode/levels. A `POWer` sweep changes
`CALC:MEAS:DATA:X?` to the configured power axis and resamples ordinary trace data to the configured
step count. These commands share the same active-hot-parameter channel state as `SENS:AHP...`.
