# Reusable virtual bench composition

A virtual bench file selects instrument drivers and models from the catalog, supplies their physical
and application configuration, and assigns client resource addresses. The same versioned file can be
loaded on a developer computer, a remote machine, or a CI worker.

Bench composition and server startup are separate phases:

1. Parse and validate the entire bench document.
2. Resolve every driver, model, firmware, and transport through the driver catalog.
3. Create all instrument objects into private composition state.
4. Start sockets only after composition succeeds.

An invalid model, option set, transport, address, or driver configuration therefore cannot partially
replace a running bench. If a later socket fails to bind during startup, all sockets started for that
attempt are stopped and the runtime returns to a clean state.

## Bench JSON schema

Schema version 1 uses one entry per instrument instance:

```json
{
  "schema_version": 1,
  "name": "two-vna-development-bench",
  "description": "Model-faithful PNA plus fully configured PNA-X.",
  "metadata": {"team": "ATE"},
  "instruments": [
    {
      "id": "pna1",
      "name": "Input PNA",
      "driver": "keysight-pna",
      "model": "N5222B",
      "firmware": "A.20.25.04",
      "configuration": {
        "mode": "model-faithful",
        "hardware_configuration": "200",
        "application_options": ["S93010B"]
      },
      "resource": {
        "transport": "raw-socket",
        "host": "127.0.0.1",
        "port": 5025
      }
    },
    {
      "id": "pnax1",
      "driver": "keysight-pna",
      "model": "N5242B",
      "configuration": {
        "hardware_configuration": "425",
        "application_options": ["S93080B", "S93029B"]
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

Instrument IDs and resource endpoints must be unique. Ports must be in the range 1–65535. A selected
transport must be advertised as implemented by the selected driver; catalog entries marked planned
or partial cannot accidentally be started.

Configuration contents are driver-specific. For the built-in PNA driver they include compatibility
mode, hardware configuration, hardware add-ons, application options, and serial number. The driver
performs the same prerequisite and physical-coherence validation used by direct PNA profiles.

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
