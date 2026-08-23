"""Built-in reference Keysight 34461A DMM driver."""

from scpi_emulator import __version__

from .catalog import (
    CatalogError,
    DriverDescriptor,
    DriverMaturity,
    InstrumentRequest,
    ModelDescriptor,
    ScenarioInputDescriptor,
    SupportLevel,
    TransportDescriptor,
)


DMM_DRIVER_ID = "keysight-3446x"


class DMMDriver:
    descriptor = DriverDescriptor(
        id=DMM_DRIVER_ID,
        display_name="Keysight Truevolt DMM",
        version=__version__,
        maturity=DriverMaturity.ALPHA,
        models=(
            ModelDescriptor(
                model="34461A",
                display_name="Keysight 34461A Truevolt DMM",
                instrument_class="DMM",
                firmware_snapshots=("3.0",),
            ),
        ),
        transports=(
            TransportDescriptor(
                "raw-socket", "TCPIP::{host}::{port}::SOCKET", SupportLevel.IMPLEMENTED
            ),
            TransportDescriptor("vxi-11", "TCPIP::{host}::INSTR", SupportLevel.IMPLEMENTED),
            TransportDescriptor(
                "hislip",
                "TCPIP::{host}::hislip0,{port}::INSTR",
                SupportLevel.IMPLEMENTED,
            ),
        ),
        scenario_inputs=(
            ScenarioInputDescriptor(
                "scalar-reading",
                SupportLevel.IMPLEMENTED,
                "Sequential voltage, current, resistance, capacitance, frequency, or period values.",
            ),
        ),
        command_coverage=(),
    )

    def create_instrument(self, request: InstrumentRequest) -> object:
        from scpi_emulator.emulator import SCPIInstrument

        model = self.descriptor.model(request.model)
        firmware = request.firmware or model.firmware_snapshots[0]
        if firmware not in model.firmware_snapshots:
            raise CatalogError(
                f"driver {DMM_DRIVER_ID!r} has no verified {request.model} firmware {firmware!r}"
            )
        if request.configuration:
            raise CatalogError("the reference DMM driver has no configurable hardware options")
        name = request.name or f"Keysight {model.model} DMM"
        return SCPIInstrument(name, request.instrument_id)
