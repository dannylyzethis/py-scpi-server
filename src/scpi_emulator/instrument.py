"""Stateful SCPI instrument core independent of transports, dashboards, and the CLI."""

from __future__ import annotations

import logging

from . import EMULATOR_FIRMWARE
from .csv_compat import CSVCommandAdapter
from .scenario import ScenarioController, ScenarioPlayer
from .scpi import (
    AcquisitionController,
    BinaryResponse,
    CommandRegistry,
    DataFormat,
    OperationManager,
    OutputQueue,
    OutputQueueFull,
    SCPICommandError,
    SCPIParseError,
    ScalarScenarioSystem,
    StatusSystem,
    VNAActiveDeviceSystem,
    VNAAdvancedSystem,
    VNACapabilities,
    VNADataSystem,
    VNAMeasurementSystem,
    VNAMixerSystem,
    VNAPulseSystem,
    VNAStateFileStore,
    VNASweepSystem,
    VNATimeDomainSystem,
    detect_vna_model,
    parse_program_message,
    split_program_message_units,
    register_acquisition_commands,
    register_active_device_commands,
    register_advanced_commands,
    register_capability_commands,
    register_common_commands,
    register_format_commands,
    register_measurement_commands,
    register_mixer_commands,
    register_operation_commands,
    register_pulse_commands,
    register_scalar_commands,
    register_state_file_commands,
    register_status_commands,
    register_sweep_commands,
    register_time_domain_commands,
    register_vna_data_commands,
)


logger = logging.getLogger(__name__)


def _is_dmm(name, instrument_id) -> bool:
    identity = f"{name} {instrument_id}".upper()
    return "DMM" in identity


class SCPIInstrument:
    """Represent one stateful SCPI instrument independently of its transport."""

    def __init__(
        self,
        name,
        instrument_id,
        *,
        vna_capabilities=None,
        state_directory=None,
        serial_number=None,
    ):
        self.name = name
        self.id = instrument_id
        self.status = StatusSystem()
        self.error_queue = self.status.error_queue
        self.csv_compatibility = CSVCommandAdapter(self.error_queue)
        self.operation_manager = OperationManager(self.status)
        self.acquisition = AcquisitionController(self.operation_manager, self.status)
        self.data_format = DataFormat()
        self.output_queue = OutputQueue(self.status)
        self.scenario_control = ScenarioController(self)
        model = detect_vna_model(str(name), str(instrument_id))
        self.vna_capabilities = vna_capabilities
        self.vna_measurements = None
        self.vna_sweeps = None
        self.vna_data = None
        self.vna_pulse = None
        self.vna_active_device = None
        self.vna_advanced = None
        self.vna_state_files = None
        self.vna_time_domain = None
        self.vna_mixer = None
        self.scalar_data = None
        self.power_supply = None
        if self.vna_capabilities is None and model is not None:
            self.vna_capabilities = VNACapabilities.create(model)
        registry_capabilities = (
            self.vna_capabilities.command_capabilities
            if self.vna_capabilities is not None
            else ()
        )
        self.core_registry = CommandRegistry(registry_capabilities)
        self.identification = (
            self.vna_capabilities.identification
            if self.vna_capabilities is not None
            else f"SCPI_Emulator,{self.name},{serial_number or self.id},{EMULATOR_FIRMWARE}"
        )
        register_common_commands(self.core_registry, lambda: self.identification, self._reset)
        register_status_commands(self.core_registry, self.status)
        register_operation_commands(self.core_registry, self.operation_manager)
        register_acquisition_commands(self.core_registry, self.acquisition)
        register_format_commands(self.core_registry, self.data_format)
        if self.vna_capabilities is not None:
            register_capability_commands(self.core_registry, self.vna_capabilities)
            self.vna_measurements = VNAMeasurementSystem()
            register_measurement_commands(self.core_registry, self.vna_measurements)
            self.vna_sweeps = VNASweepSystem(
                self.vna_capabilities, self.vna_measurements, self.acquisition
            )
            register_sweep_commands(self.core_registry, self.vna_sweeps)
            self.vna_data = VNADataSystem(
                self.vna_measurements, self.data_format, self.vna_capabilities.ports
            )
            register_vna_data_commands(self.core_registry, self.vna_data)
            self.vna_mixer = VNAMixerSystem(
                self.vna_measurements,
                float(self.vna_capabilities.frequency_minimum),
                float(self.vna_capabilities.frequency_maximum),
                self.vna_capabilities.source_count,
            )
            self.vna_data.add_application(self.vna_mixer)
            register_mixer_commands(self.core_registry, self.vna_mixer)
            self.vna_active_device = VNAActiveDeviceSystem(
                self.vna_measurements, self.data_format
            )
            self.vna_data.add_application(self.vna_active_device)
            register_active_device_commands(self.core_registry, self.vna_active_device)
            self.vna_pulse = VNAPulseSystem(self.vna_measurements)
            self.vna_data.add_application(self.vna_pulse)
            register_pulse_commands(self.core_registry, self.vna_pulse)
            self.vna_advanced = VNAAdvancedSystem(self.vna_measurements, self.data_format)
            self.vna_data.add_application(self.vna_advanced)
            register_advanced_commands(self.core_registry, self.vna_advanced)
            self.vna_time_domain = VNATimeDomainSystem(
                self.vna_measurements, self.vna_capabilities.ports
            )
            self.vna_data.add_application(self.vna_time_domain)
            register_time_domain_commands(self.core_registry, self.vna_time_domain)
            self.vna_state_files = VNAStateFileStore(
                self.vna_measurements, str(instrument_id), state_directory
            )
            register_state_file_commands(self.core_registry, self.vna_state_files)
            self.acquisition.add_trigger_listener(self.vna_data.notify_trigger)
            self.acquisition.add_completion_listener(self.vna_data.notify_complete)
        elif _is_dmm(name, instrument_id):
            self.scalar_data = ScalarScenarioSystem(self.operation_manager)
            register_scalar_commands(self.core_registry, self.scalar_data)
        self.last_command = ""
        self.command_count = 0
        self._command_observers = []

    def set_serial_number(self, serial_number):
        """Override the third field of the instrument's four-field identity response."""
        if not isinstance(serial_number, str) or not serial_number.strip():
            raise ValueError("serial number must be a non-empty string")
        fields = self.identification.split(",", 3)
        if len(fields) != 4:
            raise ValueError("instrument identification must contain four comma-separated fields")
        fields[2] = serial_number.strip()
        self.identification = ",".join(fields)

    def set_reported_model(self, reported_model):
        """Override the second field of the instrument's four-field identity response."""
        if not isinstance(reported_model, str) or not reported_model.strip():
            raise ValueError("reported model must be a non-empty string")
        if any(character in reported_model for character in ",\r\n"):
            raise ValueError("reported model cannot contain commas or line breaks")
        fields = self.identification.split(",", 3)
        if len(fields) != 4:
            raise ValueError("instrument identification must contain four comma-separated fields")
        fields[1] = reported_model.strip()
        self.identification = ",".join(fields)

    def visa_device_clear(self):
        """Simulate VISA Device Clear without resetting configured instrument state."""
        logger.info("[VISA-CLR] VISA Device Clear for %s", self.name)
        self.operation_manager.abort()
        self.status.clear_status()
        self.output_queue.clear()
        self.last_command = ""
        self.command_count = 0
        self.csv_compatibility.link_stateful_commands()

    def _reset(self):
        self.operation_manager.abort()
        self.csv_compatibility.reset()
        self.data_format.reset()
        for component in (
            self.vna_measurements,
            self.vna_sweeps,
            self.vna_data,
            self.vna_active_device,
            self.vna_pulse,
            self.vna_advanced,
            self.vna_time_domain,
            self.vna_mixer,
            self.scalar_data,
            self.power_supply,
        ):
            if component is not None:
                component.reset()
        self.status.clear_status()
        self.output_queue.clear()
        return ""

    def begin_operation(self, name):
        """Start overlapped work that participates in OPC, OPC?, WAI, and ABORt."""
        return self.operation_manager.begin(name)

    def external_trigger(self, channel=None):
        """Inject an external trigger edge into one channel or all waiting channels."""
        return self.acquisition.external_trigger(channel)

    def attach_scenario(self, scenario):
        """Attach a shared ScenarioDefinition or ScenarioPlayer to this instrument."""
        player = scenario if isinstance(scenario, ScenarioPlayer) else ScenarioPlayer(scenario)
        if self.vna_data is not None:
            self.vna_data.attach(player)
        if self.scalar_data is not None:
            self.scalar_data.attach(player)
        self.csv_compatibility.attach(player)
        self.scenario_control.attach(player)
        return player

    def inspect_state(self):
        """Return a non-destructive snapshot of instrument-owned runtime state."""
        identity = self.identification.split(",", 3)
        identity.extend("" for _ in range(4 - len(identity)))
        capabilities = None
        if self.vna_capabilities is not None:
            profile = self.vna_capabilities
            capabilities = {
                "model": profile.model,
                "instrument_class": profile.instrument_class,
                "firmware": profile.firmware,
                "ports": profile.ports,
                "source_count": profile.source_count,
                "hardware_features": sorted(profile.hardware_features),
                "applications": list(profile.applications),
                "frequency_minimum": profile.frequency_minimum,
                "frequency_maximum": profile.frequency_maximum,
            }
        return {
            "identity": {
                "manufacturer": identity[0],
                "reported_model": identity[1],
                "serial_number": identity[2],
                "firmware": identity[3],
            },
            "status": self.status.inspect(),
            "operations": self.operation_manager.inspect(),
            "acquisition": self.acquisition.inspect(),
            "scenario": self.scenario_control.inspect(),
            "measurements": (
                self.vna_measurements.inspect() if self.vna_measurements is not None else None
            ),
            "scalar": self.scalar_data.inspect() if self.scalar_data is not None else None,
            "power_supply": (
                self.power_supply.inspect() if self.power_supply is not None else None
            ),
            "capabilities": capabilities,
        }

    def add_command(self, command, response, validation=None):
        """Compatibility wrapper for programmatic five-column CSV commands."""
        self.csv_compatibility.add_command(command, response, validation)

    def add_binary_query(self, command, data, *, definite=True):
        """Add a byte-preserving binary query response to the active instrument."""
        self.csv_compatibility.add_binary_query(command, data, definite=definite)

    def add_command_observer(self, observer):
        """Observe completed commands without coupling transports to a dashboard."""
        if observer not in self._command_observers:
            self._command_observers.append(observer)

    def remove_command_observer(self, observer):
        """Stop observing completed commands."""
        if observer in self._command_observers:
            self._command_observers.remove(observer)

    def queue_command_response(self, command, *, termination=b"\n"):
        """Execute a program message and leave any response in the output queue."""
        if self.output_queue:
            self.output_queue.clear()
            self.error_queue.push(-410)
        response = self.process_command(command)
        if response:
            try:
                self.output_queue.enqueue(
                    response,
                    terminate=bool(termination),
                    termination=termination,
                )
            except OutputQueueFull:
                self.output_queue.clear()
                self.error_queue.push(-430)
                return ""
        return response

    def read_output(self, maximum=None):
        """Read queued response bytes, preserving MAV until fully drained."""
        return self.output_queue.read(maximum)

    def link_stateful_commands(self):
        """Compatibility wrapper for linking CSV SET/QUERY pairs."""
        self.csv_compatibility.link_stateful_commands()

    @property
    def state(self):
        """Compatibility view of state owned by the CSV adapter."""
        return self.csv_compatibility.state

    def process_command(self, command):
        """Process a SCPI command and return its immediate response."""
        self.last_command = (
            command.decode("utf-8", errors="replace") if isinstance(command, bytes) else command
        )
        self.command_count += 1
        command = command.strip()
        if not command:
            return ""
        response = self._process_program_message(command)
        error = self.error_queue.last_response() if self.error_queue else None
        for observer in tuple(self._command_observers):
            try:
                observer(self, self.last_command, response, error)
            except Exception:
                logger.exception("Instrument command observer failed")
        return response

    def _process_program_message(self, command):
        """Dispatch one complete byte or text program message."""
        try:
            units = split_program_message_units(command)
        except SCPIParseError as error:
            self.error_queue.push(-102, str(error))
            return ""
        if len(units) == 1:
            return self._process_single_command(units[0])
        responses = []
        for unit in units:
            response = self._process_single_command(unit)
            if response:
                responses.append(response)
        return ";".join(responses) if responses else ""

    def _process_single_command(self, command):
        """Process one command through the typed registry, then CSV compatibility."""
        try:
            parsed = parse_program_message(command).commands[0]
            return self.core_registry.dispatch(parsed)
        except SCPIParseError as error:
            self.error_queue.push(-102, str(error))
            return ""
        except SCPICommandError as error:
            if not error.message.startswith("Undefined header"):
                self.error_queue.push(error)
                return ""

        try:
            handled, result = self.csv_compatibility.dispatch(command)
        except Exception:
            self.error_queue.push(-310, f"command execution failed; {command}")
            return ""
        if handled:
            if isinstance(result, (BinaryResponse, bytes, bytearray, memoryview)):
                return result
            return str(result) if result is not None else ""
        self.error_queue.push(-113, command)
        return ""
