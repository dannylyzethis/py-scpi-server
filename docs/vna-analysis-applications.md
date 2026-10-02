# Uncertainty and performance-test applications

The virtual VNA includes deterministic, project-owned analysis workflows intended for test
program development rather than metrology or conformance certification.

Enable uncertainty analysis with `SENS:UNC:STAT ON`. `CALC:UNC:DATA?` reads the scenario stream
`measurement_uncertainty.trace` when present. Without it, the emulator derives repeatable
per-point values from the selected measurement, `SENS:UNC:CONF`, and `SENS:UNC:FLOO`.

Enable performance testing with `SENS:PERF:STAT ON`. `CALC:PERF:DATA?` reads
`performance_test.trace` when present or derives magnitude results from the selected trace.
Configure inclusive limits with `SENS:PERF:LIM:LOW` and `SENS:PERF:LIM:UPP`, then query
`CALC:PERF:PASS?` for `1` when every result is within limits or `0` otherwise.

Both applications are capability-gated, preserve configuration across `*CLS`, reset with
`*RST`, and report missing/invalid scenario data through the normal SCPI error queue.
