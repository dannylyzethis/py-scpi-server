# Triple-output power-supply emulator

The built-in `virtual-triple-psu` driver provides the `E36312A-EMU` model as a stateful generic
triple-output supply. It is separate from the legacy single-context PSU block in
`detailed_instruments.csv`; adding channel-selection rows to that CSV would not create independent
channel state.

## Bench definition

Two instances of the same model use different bench IDs, serial numbers, and resource ports:

```json
{
  "schema_version": 1,
  "name": "two-supply-bench",
  "instruments": [
    {
      "id": "supply1",
      "driver": "virtual-triple-psu",
      "model": "E36312A-EMU",
      "serial_number": "PSU-001",
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5025
      }
    },
    {
      "id": "supply2",
      "driver": "virtual-triple-psu",
      "model": "E36312A-EMU",
      "serial_number": "PSU-002",
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5026
      }
    }
  ]
}
```

`serial_number` changes the third field returned by `*IDN?`; it is independent of the bench `id`
and network port. This lets client software distinguish two instances of the same model.

## Independent outputs

The selected-output context controls which of the three state objects subsequent commands address:

```text
INST:NSEL 1
VOLT 5
CURR 0.5
OUTP ON

INST:NSEL 2
VOLT 12
CURR 1
OUTP ON

INST:NSEL 1
VOLT?                 -> 5.000000E+00
INST:NSEL 2
VOLT?                 -> 1.200000E+01
```

`INST:SEL OUT1`, `OUT2`, or `OUT3` is an equivalent named selector. `INST:CAT?` lists those names,
and `SYST:CHAN:COUN?` returns `3`.

The selected output owns independent values for:

- voltage and current settings;
- output enable;
- voltage and current protection thresholds;
- voltage and current range selection;
- protection-trip state;
- voltage, current, and power measurement queries.

`*CLS` clears status and errors without changing any output configuration. `*RST` disables and
resets all three outputs and selects output 1. The implementation is an emulator-defined generic
behavioral contract; this document does not reproduce any equipment-manufacturer manual text.

See `examples/mixed-bench.json` for two built-in supplies combined with a CSV-defined fixture.
