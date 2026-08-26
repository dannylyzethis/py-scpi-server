# Starter scenarios

Scenario files are UTF-8 JSON documents. The extension is only a convenience, so both `.json` and
`.txt` work in the interactive manager and dashboard.

With `examples/virtual-bench.json` loaded, try the JSON DMM queue:

```text
SCPI-MGR> scenario load meter1 examples/scenarios/dmm-voltage.json
SCPI-MGR> scenario start meter1
```

Successive `READ?` commands return `3.3`, `3.1`, `4.8`, then hold at `4.8`. To prove that a text
file works, load `generic-readings.txt` instead; successive `READ?` commands loop through its values.

For the VNA example, load `examples/generic-vna-bench.json`, then:

```text
SCPI-MGR> scenario load vna1 examples/scenarios/vna-s11-traces.json
SCPI-MGR> scenario start vna1
```

Before reading the trace, send these SCPI commands through your client or the dashboard console:

```text
SENS:SWE:POIN 5
FORM:DATA ASC
CALC:DATA? SDAT
```

The first data query returns the first five-point S11 trace and advances to the second trace. The
next query returns the second trace and then holds it. Quotes around paths are needed only when the
path contains spaces.
