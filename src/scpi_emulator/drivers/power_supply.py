"""Built-in generic triple-output virtual power-supply driver."""

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


POWER_SUPPLY_DRIVER_ID = "virtual-triple-psu"


class TripleOutputPowerSupplyDriver:
    descriptor = DriverDescriptor(
        id=POWER_SUPPLY_DRIVER_ID,
        display_name="Virtual triple-output power supply",
        version=__version__,
        maturity=DriverMaturity.ALPHA,
        models=(
            ModelDescriptor(
                model="E36312A-EMU",
                display_name="Virtual E36312A-EMU triple-output power supply",
                instrument_class="PSU",
                firmware_snapshots=(EMULATOR_FIRMWARE,),
            ),
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
        from scpi_emulator.emulator import SCPIInstrument
        from scpi_emulator.scpi.power_supply import (
            TripleOutputPowerSupply,
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
            raise CatalogError("the triple-output PSU driver has no hardware configuration options")
        name = request.name or f"Virtual {model.model} power supply"
        instrument = SCPIInstrument(
            name,
            request.instrument_id,
            serial_number=request.serial_number,
        )
        instrument.identification = (
            f"SCPI Emulator,{model.model},{request.serial_number or request.instrument_id},{firmware}"
        )
        instrument.power_supply = TripleOutputPowerSupply()
        register_power_supply_commands(instrument.core_registry, instrument.power_supply)
        return instrument
