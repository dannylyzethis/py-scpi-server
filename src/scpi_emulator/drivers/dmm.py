"""Built-in reference virtual DMM driver."""

from scpi_emulator import EMULATOR_FIRMWARE, __version__

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


DMM_DRIVER_ID = "virtual-dmm"


class DMMDriver:
    descriptor = DriverDescriptor(
        id=DMM_DRIVER_ID,
        display_name="Virtual DMM",
        version=__version__,
        maturity=DriverMaturity.ALPHA,
        models=(
            ModelDescriptor(
                model="dmm",
                display_name="Virtual DMM",
                instrument_class="DMM",
                firmware_snapshots=(EMULATOR_FIRMWARE,),
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
        from scpi_emulator.instrument import SCPIInstrument

        model = self.descriptor.model(request.model)
        firmware = request.firmware or model.firmware_snapshots[0]
        if firmware not in model.firmware_snapshots:
            raise CatalogError(
                f"driver {DMM_DRIVER_ID!r} has no verified {request.model} firmware {firmware!r}"
            )
        if request.configuration:
            raise CatalogError("the virtual DMM driver has no configurable hardware options")
        name = request.name or model.display_name
        instrument = SCPIInstrument(
            name,
            request.instrument_id,
            serial_number=request.serial_number,
        )
        instrument.set_reported_model(request.reported_model or model.display_name)
        return instrument
