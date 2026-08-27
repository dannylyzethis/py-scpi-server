"""Built-in generic one-through-four-output virtual power-supply driver."""

from scpi_emulator import EMULATOR_FIRMWARE, __version__

from .catalog import (
    CatalogError,
    DriverDescriptor,
    DriverMaturity,
    InstrumentRequest,
    ModelDescriptor,
    SupportLevel,
    TransportDescriptor,
)


POWER_SUPPLY_DRIVER_ID = "virtual-ps"
POWER_SUPPLY_MODELS = {
    f"ps-{output_count}-output": output_count for output_count in range(1, 5)
}


class PowerSupplyDriver:
    descriptor = DriverDescriptor(
        id=POWER_SUPPLY_DRIVER_ID,
        display_name="Virtual power supply",
        version=__version__,
        maturity=DriverMaturity.ALPHA,
        models=tuple(
            ModelDescriptor(
                model=model,
                display_name=f"Virtual PS {output_count} Output",
                instrument_class="PSU",
                firmware_snapshots=(EMULATOR_FIRMWARE,),
            )
            for model, output_count in POWER_SUPPLY_MODELS.items()
        ),
        transports=(
            TransportDescriptor(
                "raw-socket",
                "TCPIP::{host}::{port}::SOCKET",
                SupportLevel.IMPLEMENTED,
            ),
            TransportDescriptor("vxi-11", "TCPIP::{host}::INSTR", SupportLevel.IMPLEMENTED),
            TransportDescriptor(
                "hislip",
                "TCPIP::{host}::hislip0,{port}::INSTR",
                SupportLevel.IMPLEMENTED,
            ),
        ),
        scenario_inputs=(),
        command_coverage=(),
    )

    def create_instrument(self, request: InstrumentRequest) -> object:
        from scpi_emulator.instrument import SCPIInstrument
        from scpi_emulator.scpi.power_supply import (
            PowerSupplySystem,
            register_power_supply_commands,
        )

        model = self.descriptor.model(request.model)
        firmware = request.firmware or model.firmware_snapshots[0]
        if firmware not in model.firmware_snapshots:
            raise CatalogError(
                f"driver {POWER_SUPPLY_DRIVER_ID!r} has no verified "
                f"{request.model} firmware {firmware!r}"
            )
        if request.configuration:
            raise CatalogError("the virtual PSU driver has no hardware configuration options")
        name = request.name or model.display_name
        instrument = SCPIInstrument(
            name,
            request.instrument_id,
            serial_number=request.serial_number,
        )
        instrument.identification = (
            f"SCPI Emulator,{request.reported_model or model.display_name},"
            f"{request.serial_number or request.instrument_id},{firmware}"
        )
        instrument.power_supply = PowerSupplySystem(POWER_SUPPLY_MODELS[model.model])
        register_power_supply_commands(instrument.core_registry, instrument.power_supply)
        return instrument
