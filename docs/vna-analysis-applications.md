# Uncertainty and performance-test applications

The virtual VNA includes deterministic, project-owned analysis workflows intended for test
program development rather than metrology or conformance certification.

Enable uncertainty analysis with `SENS:UNC:STAT ON`. `CALC:UNC:DATA?` reads the scenario stream
`measurement_uncertainty.trace` when present. Without it, the emulator derives repeatable
per-point values from the selected measurement, `SENS:UNC:CONF`, and `SENS:UNC:FLOO`. Choose
absolute values or relative percentages with `SENS:UNC:MODE ABS|REL`, and apply a numeric display
factor with `SENS:UNC:SCAL`. `CALC:UNC:MIN?`, `CALC:UNC:MAX?`, and `CALC:UNC:MEAN?` return scalar
summaries of the same result data.

Enable performance testing with `SENS:PERF:STAT ON`. `CALC:PERF:DATA?` reads
`performance_test.trace` when present or derives magnitude results from the selected trace.
Configure inclusive limits with `SENS:PERF:LIM:LOW` and `SENS:PERF:LIM:UPP`, then query
`CALC:PERF:PASS?` for `1` when every result is within limits or `0` otherwise. Limit evaluation can
be enabled or disabled with `SENS:PERF:LIM:STAT`; a disabled limit set passes and reports zero
failures. `CALC:PERF:FAIL:COUN?` returns the number of failing points, while `CALC:PERF:MIN?`,
`CALC:PERF:MAX?`, and `CALC:PERF:MEAN?` provide scalar summaries. Pass/fail evaluation uses the
numeric result values directly and therefore behaves the same for ASCII and binary data formats.

Both applications are capability-gated, preserve configuration across `*CLS`, reset with
`*RST`, and report missing/invalid scenario data through the normal SCPI error queue.
