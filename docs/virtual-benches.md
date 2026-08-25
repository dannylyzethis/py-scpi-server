# Reusable virtual bench composition

A virtual bench file selects instrument drivers and models from the catalog, supplies their physical
and application configuration, and assigns client resource addresses. The same versioned file can be
loaded on a developer computer, a remote machine, or a CI worker.

For the complete list of driver IDs, model IDs, configuration fields, defaults, hardware/add-on
tokens, application-option tokens, and copyable instrument objects, start with the
[Instrument catalog and bench configuration reference](instrument-catalog.md).

The catalog always includes the built-in VNA, DMM, and triple-output PSU drivers. Legacy five-column
CSV instruments can be added as another driver family by passing their directory when building the
catalog:

```python
catalog = build_driver_catalog(csv_directory="instrument-definitions")
```

Each `Equipment` block becomes a model under the experimental `csv-instruments` driver and can be
selected in a bench with the `raw-socket` transport. Equipment names use the same normalized IDs as
the legacy `--load file.csv` path—for example, `Bench Relay` becomes `bench_relay`. The directory is
configuration supplied by the host; it is not added to the versioned bench schema.

Bench composition and server startup are separate phases:

1. Parse and validate the entire bench document.
2. Resolve every driver, model, firmware, and transport through the driver catalog.
3. Create all instrument objects into private composition state.
4. Start sockets only after composition succeeds.

From the CLI, use the precise bench path directly:

```bash
scpi-emulator --bench examples/virtual-bench.json --start
```

If that bench selects models from the `csv-instruments` driver, place their CSV files beside
`bench.json`; the CLI catalogs that directory before composition. This advanced path can mix those
CSV models with the built-in VNA, DMM, and PSU drivers. For a simple folder containing only CSV-defined
instruments, use the equally supported `scpi-emulator --load instruments/ --start` path instead.
Both modes reuse `--web`, `--web-host`, and `--web-port` and print their client resource strings at
startup.

For file and directory behavior, path quoting, port assignment, and the exact five-column format,
see [Loading CSV and XLSX instrument definitions](csv-loading.md).

An invalid model, option set, transport, address, or driver configuration therefore cannot partially
replace a running bench. If a later socket fails to bind during startup, all sockets started for that
attempt are stopped and the runtime returns to a clean state.

## Bench JSON schema

Schema version 1 uses one entry per instrument instance:

```json
{
  "schema_version": 1,
  "name": "two-vna-development-bench",
  "description": "Model-faithful VNA plus fully configured VNA-EXTENDED.",
  "metadata": {"team": "ATE"},
  "instruments": [
    {
      "id": "pna1",
      "name": "Input VNA",
      "driver": "virtual-vna",
      "model": "N5222B-EMU",
      "firmware": "E.1.0",
      "serial_number": "VNA-001",
      "configuration": {
        "mode": "model-faithful",
        "hardware_configuration": "200",
        "frequency_maximum_hz": 20000000000
      },
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5025
      }
    },
    {
      "id": "pnax1",
      "driver": "virtual-vna",
      "model": "N5242B-EMU",
      "configuration": {
        "hardware_configuration": "425",
        "mode": "all-applications"
      },
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5026
      }
    }
  ]
}
```

Instrument IDs and resource endpoints must be unique. The optional `serial_number` is the per-instance
serial returned in the third field of `*IDN?`; use it when a bench contains two instances of one
model. Ports must be in the range 1–65535. A selected
transport must be advertised as implemented by the selected driver; catalog entries marked planned
or partial cannot accidentally be started.

Configuration contents are driver-specific. For the built-in VNA driver they include compatibility
mode, hardware configuration, hardware add-ons, application options, and optional minimum/maximum
frequency capability overrides. Omitted frequency endpoints inherit the model defaults. The driver
performs the same prerequisite and physical-coherence validation used by direct VNA profiles.
CSV models do not advertise additional configuration or scenario-input guarantees because their
available commands depend on their source files.

## Scenario-backed CSV responses

The existing CSV schema is unchanged. A command can consume a named deterministic scenario stream by
placing `{{scenario:<stream_name>}}` in its existing `Response` column. For example:

```csv
Equipment,Port,Command,Response,Validation
Queue Reader,5025,VALUE?,{{scenario:dut.value}},
```

After `attach_scenario()` supplies a shared player, every `VALUE?` query calls
`player.read("dut.value")`, including the stream's configured advancement and end policies. Without
an attached scenario the marker returns `0`; ordinary canned responses remain unchanged. This keeps
existing CSV instruments operational while allowing selected responses to use the same deterministic
engine as the VNA and DMM drivers.

## Mixed built-in and CSV example

`examples/mixed-bench.json` composes two instances of the built-in `virtual-triple-psu` driver and
one `csv-instruments` model declared by `examples/mixed-bench.csv`. The two supplies share the same
model but have unique `id`, `serial_number`, and `resource.port` values. Because the CSV is beside
the JSON, the CLI catalogs its `Fixture Controller` equipment block as model `fixture_controller`.

Validate the composition without opening ports, or start all three instruments:

```powershell
scpi-emulator --bench .\examples\mixed-bench.json
scpi-emulator --bench .\examples\mixed-bench.json --start
```

See [Triple-output power-supply emulator](power-supply.md) for its independent-output state and
selected-channel commands.

## Loading, composing, and starting

```python
from scpi_emulator.bench import BenchComposer, load_bench
from scpi_emulator.drivers import build_driver_catalog

definition = load_bench("two-vna-development-bench.json")
catalog = build_driver_catalog()
bench = BenchComposer(catalog).compose(definition)

# Bind exactly as written in the file.
runtime = bench.start()
try:
    print(bench.resources())
finally:
    runtime.stop()
```

`bench.resources()` renders VISA-style client resource names from each driver's transport template.
For the example it returns `TCPIP::127.0.0.1::5025::SOCKET` and
`TCPIP::127.0.0.1::5026::SOCKET`.

## Local, remote, and CI deployment

The file describes logical instruments and stable ports. A deployment can override only the network
host without changing the instrument selection or configuration:

```python
# On the machine hosting the emulators, listen on all interfaces.
runtime = bench.start(bind_host="0.0.0.0")

# Tell remote clients to use the actual DNS name.
client_resources = bench.resources(host="ate-emulator.example.net")
```

The override is preflighted across the full bench. Two addresses that were distinct only because
they used different hosts cannot collapse onto the same host/port silently. This allows the same
checked-in bench file to run locally, on a remote development host, or in CI without rewriting model
and option selections.

Raw sockets, VXI-11, and HiSLIP are startable transports. For VXI-11, the resource port in a bench
definition is the local ONC RPC portmapper port—normally 111—and the rendered VISA resource is
`TCPIP::<host>::INSTR`. Multiple VXI-11 instruments normally use distinct host addresses, as real
network instruments do. For HiSLIP, the resource port is the listener port and the rendered resource
is `TCPIP::<host>::hislip0,<port>::INSTR`.

## DUT scenario control

The bench and DUT scenario remain independent, so one scenario can be reused across multiple bench
compositions and one bench can run many scenarios without restarting its sockets. A running
`BenchRuntime` can start the secure web/control API with `start_web_dashboard()`. The API selects,
starts, pauses, steps, resets, and inspects scenarios per instrument. See
[Remote ATE development workflow](remote-ate-workflow.md).
