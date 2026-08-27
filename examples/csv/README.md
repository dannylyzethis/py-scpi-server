# CSV examples

These files are supported, copyable starting points for the five-column compatibility format:

- `basic/scpi_instruments_example.csv` contains two small instruments and is the Docker image default.
- `catalog/detailed_instruments.csv` contains nine generic static instrument profiles.
- `vna/vna-commands.csv` contains one static generic two-port VNA profile.
- `mixed/mixed-bench.json` combines two built-in power supplies with its adjacent CSV controller.

From the repository root, start one file or the complete folder:

```powershell
scpi-emulator --load .\examples\csv\basic\scpi_instruments_example.csv --start
scpi-emulator --load .\examples\csv\catalog --start
```

The complete mixed bench uses the JSON definition instead:

```powershell
scpi-emulator --bench .\examples\csv\mixed\mixed-bench.json --start
```

Each subfolder is independently loadable; the parent `examples/csv` folder is only an organizer.
Do not combine `--load` and `--bench`. See [the CSV loading guide](../../docs/csv-loading.md) for the
schema, quoting rules, directory behavior, automatic ports, and scenario-response markers.
