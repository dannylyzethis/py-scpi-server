# Generic power-supply emulator

The built-in `virtual-ps` driver provides four stateful profiles: `ps-1-output`, `ps-2-output`,
`ps-3-output`, and `ps-4-output`. The selector determines how many independent outputs exist.

## Bench definition

```json
{
  "schema_version": 2,
  "name": "mixed-supply-bench",
  "instruments": [
    {
      "id": "single_supply",
      "driver": "virtual-ps",
      "model": "ps-1-output",
      "serial_number": "PS-001",
      "resource": {"transport": "raw-socket", "host": "127.0.0.1", "port": 5025}
    },
    {
      "id": "four_output_supply",
      "driver": "virtual-ps",
      "model": "ps-4-output",
      "reported_model": "Development Rack Supply",
      "serial_number": "PS-002",
      "resource": {"transport": "raw-socket", "host": "127.0.0.1", "port": 5026}
    }
  ]
}
```

`reported_model` changes only the second `*IDN?` field. `serial_number` changes only the third.
Neither changes the selected output-count profile.

## Independent outputs

The selected-output context controls subsequent commands:

```text
INST:NSEL 1
VOLT 5
CURR 0.5
OUTP ON

INST:NSEL 2
VOLT 12
CURR 1
OUTP ON
```

`INST:SEL OUT<n>` is the named form. `INST:CAT?` lists exactly the outputs available in the profile,
and `SYST:CHAN:COUN?` reports their count. Selecting an unavailable output queues SCPI error `-222`.

Every output independently owns voltage/current settings, enable state, protection thresholds,
range selections, trip state, and voltage/current/power measurements. `*CLS` clears status and
errors without changing configured outputs. `*RST` resets all available outputs and selects output 1.

The `Virtual PS 1 Output` block in `detailed_instruments.csv` is a static compatibility profile; it
does not provide the independent state implemented by the built-in driver.
