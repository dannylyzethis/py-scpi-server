# DMM scenario data

The built-in `keysight-3446x` driver provides a reference Keysight 34461A DMM for scalar DUT
scenarios. It uses the same `ScenarioPlayer` as PNA traces; there is no DMM-specific queue or random
generator.

## Stream mapping

Standard measurement functions look for these scalar stream names:

| Function | Default stream |
| --- | --- |
| DC/AC voltage | `voltage.dc` / `voltage.ac` |
| DC/AC current | `current.dc` / `current.ac` |
| 2-wire/4-wire resistance | `resistance` / `fresistance` |
| Capacitance | `capacitance` |
| Frequency/period | `frequency` / `period` |

A generic stream named `reading` is the fallback. An explicit binding can give a test-specific stream
to a function: `instrument.scalar_data.bind("VOLTage:DC", "dut-rail-3v3")`.

## Command behavior

- `READ?` performs a synchronous scalar operation and caches the completed value.
- `FETCh?` returns that cached value without advancing the scenario.
- `MEAS:VOLT:DC?`, `MEAS:CURR:DC?`, and related forms select a function and perform a reading.
- Optional MEASure or CONFigure range/resolution arguments are retained; a value exceeding the
  configured absolute range reports SCPI `-222` and the next queued sample remains available.

Read-policy streams advance per measurement. Trigger- and operation-policy streams advance after
the corresponding synchronous measurement event. Explicit exhaustion becomes SCPI `-230`.
`*CLS` clears status without changing playback; `*RST` restores DC-voltage configuration and rewinds
the shared player and deterministic seed.

This supports sequences such as nominal, drift, limit failure, and recovery through ordinary DMM
commands, allowing ATE logic to exercise its normal error handling before hardware is available.
