# VNA pulse and Integrated Pulse behavior

The pulse layer implements project-defined pulsed-RF generator controls and integrated pulse
measurement setup. Availability comes from the selected emulator capability profile; individual
application identifiers are intentionally not explained in this documentation.

The emulator implements these command groups:

- `SENS:PULS<n>` configures internal generators 0 through 4, including state, delay, width, delay
  increment, polarity inversion, period, trigger type/polarity, subpoint triggering, and pulse-4 ADC
  indication.
- `SENS:SWE:PULS` selects standard point-in-pulse or pulse-profile operation and configures automatic
  timing/detection/drive/IF-gain/PRF choices, software gating, wideband mode, master timing, and the
  profile time window.
- `SENS:IF` exposes the pulse-relevant automatic/manual IF path, capture mode, IF frequency, and
  stage-3 pulse-window filter settings.
- `SENS:PATH:CONF:ELEM` stores pulse modulation and IF-gate routing such as `IFGateA` to `Pulse2`.

The emulator accepts delay-plus-width values that exceed the configured period as part of its own
compatibility contract. It rejects configured numeric-range violations and internally inconsistent
master/profile timing with SCPI `-222` errors.

## Scenario results

Integrated Pulse is another processor in the shared VNA data pipeline. It does not own a separate
queue or trigger engine.

- Standard mode (`SENS:SWE:PULS:MODE STD`) consumes `pulse.point` complex trace streams.
- Profile mode (`... MODE PROF`) consumes `pulse.profile` complex trace streams and changes the
  selected measurement X-axis to the configured profile start/stop time.
- The scenario trace length must match the selected channel point count. A mismatch reports `-230`.
- Read, trigger, operation, pause, manual-step, reset, and end-of-stream policies come from the same
  `ScenarioPlayer` used by base S-parameters, active-device results, and DMM readings.

When a pulse result stream is absent, standard mode preserves the underlying measurement trace.
Profile mode can derive a deterministic gate envelope from pulse generator 1, allowing setup code to
run before a detailed DUT trace has been authored.

`*CLS` clears errors and status but preserves pulse configuration. `*RST` returns generators,
Integrated Pulse mode, IF configuration, and routing to their disabled/default state. Pulse
calibration status is a static `0`; calibration behavior and math remain outside product scope.
