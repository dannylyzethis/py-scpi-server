# Instrument driver and catalog contract

The driver catalog is the boundary between instrument-family implementations and the future virtual
bench builder. It lets code enumerate available emulators without importing the dashboard or adding
model-specific branches to the bench composer.

## What a driver advertises

Each driver provides an immutable `DriverDescriptor` containing:

- a stable driver ID, display name, version, and maturity level;
- supported instrument models and classes;
- pinned firmware snapshots;
- hardware configurations, hardware add-ons, and application options;
- resource/transport templates and whether each is planned, partial, or implemented;
- supported scenario input shapes and their implementation status;
- model-specific documented-command coverage and report locations.

Support status is explicit. For example, the built-in PNA driver advertises raw TCP sockets as
implemented, VXI-11 and HiSLIP as planned, and PNA trace/scalar scenario inputs as planned until the
PNA adapter is delivered by `scpi-303`. The generic scenario engine exists independently; catalog
presence therefore does not falsely imply that every instrument-specific adapter is complete.

## Creating an instrument

`InstrumentRequest` carries the bench instance ID, model, optional name, pinned firmware, and
driver-specific configuration. The catalog validates that the selected driver advertises the model
before invoking the driver's `create_instrument()` factory.

```python
from scpi_emulator.drivers import InstrumentRequest, build_driver_catalog

catalog = build_driver_catalog(discover_plugins=False)
instrument = catalog.create(
    "keysight-pna",
    InstrumentRequest(
        instrument_id="vna1",
        model="N5242B",
        configuration={
            "mode": "model-faithful",
            "hardware_configuration": "425",
            "application_options": ["S93080B", "S93029B"],
        },
    ),
)
```

The PNA descriptor is generated from the packaged compatibility matrix. Its coverage records are
checked against the versioned reports, and its factory uses the same `PNACapabilities` object that
drives identity, option, license, hardware, and command-availability behavior.

## Adding an external driver

A driver implements only two members:

```python
class MyDriver:
    descriptor = DriverDescriptor(...)

    def create_instrument(self, request: InstrumentRequest) -> object:
        ...
```

It can be registered directly with `DriverCatalog.register()`, or published through the standard
Python entry-point group:

```toml
[project.entry-points."scpi_emulator.drivers"]
my-instrument = "my_instrument.driver:MyDriver"
```

`build_driver_catalog()` loads that group by default. The new family becomes discoverable without
editing the emulator core, dashboard, or future bench composer. Driver IDs must be unique; malformed
metadata and duplicate registration fail before a bench is created.

## Design boundary

The catalog describes and creates instrument emulators. It does not assign ports, start servers,
compose benches, play DUT scenarios, or render a UI. Those consumers depend on this contract instead
of importing PNA-specific code. This separation is what allows `scpi-703` to build one generic
scenario engine and later attach DMM, PNA, and additional driver families to it.
