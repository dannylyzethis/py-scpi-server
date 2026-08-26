#!/usr/bin/env python3
"""SCPI emulator process, transports, dashboard, and configuration loading."""

import csv
import json
import socket
import threading
import time
import argparse
import sys
import re
import logging
import signal
import secrets
from pathlib import Path
from datetime import datetime
from collections import deque
import os
from collections.abc import Sequence

from . import EMULATOR_FIRMWARE, __version__
from .csv_compat import CSVCommandAdapter
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
    StatusSystem,
    VNACapabilities,
    VNAActiveDeviceSystem,
    VNAAdvancedSystem,
    VNAMeasurementSystem,
    VNAMixerSystem,
    VNADataSystem,
    VNAPulseSystem,
    VNASweepSystem,
    VNAStateFileStore,
    VNATimeDomainSystem,
    ScalarScenarioSystem,
    detect_vna_model,
    parse_program_message,
    register_operation_commands,
    register_active_device_commands,
    register_advanced_commands,
    register_acquisition_commands,
    register_format_commands,
    register_capability_commands,
    register_common_commands,
    register_status_commands,
    register_measurement_commands,
    register_mixer_commands,
    register_vna_data_commands,
    register_pulse_commands,
    register_sweep_commands,
    register_scalar_commands,
    register_state_file_commands,
    register_time_domain_commands,
)
from .socket_transport import MessageTooLarge, SocketMessageFramer, SocketTransportConfig
from .scenario import ScenarioControlError, ScenarioController, ScenarioError, loads_scenario

# Flask imports
try:
    from flask import Flask, render_template, jsonify, request
    from flask_socketio import SocketIO
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

logger = logging.getLogger(__name__)


def configure_logging(*, verbose=False, log_file=None):
    """Configure application logging without import-time filesystem writes."""
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.insert(0, logging.FileHandler(log_file))

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True,
    )

class CommandLogger:
    """Tracks commands and responses for web dashboard"""
    
    def __init__(self, max_entries=1000):
        self.entries = deque(maxlen=max_entries)
        self.stats = {
            'total_commands': 0,
            'commands_per_minute': 0,
            'last_minute_commands': deque(maxlen=60),
            'errors': 0,
            'start_time': time.time()
        }
        self.lock = threading.Lock()
    
    def log_command(self, instrument_name, command, response, error=None):
        """Log a command/response pair"""
        timestamp = time.time()
        
        with self.lock:
            entry = {
                'timestamp': timestamp,
                'time_str': datetime.fromtimestamp(timestamp).strftime('%H:%M:%S'),
                'instrument': instrument_name,
                'command': command,
                'response': response,
                'error': error,
                'is_error': error is not None
            }
            
            self.entries.append(entry)
            self.stats['total_commands'] += 1
            
            if error:
                self.stats['errors'] += 1
            
            # Update commands per minute
            current_minute = int(timestamp // 60)
            self.stats['last_minute_commands'].append(current_minute)
            
            # Calculate commands per minute
            if len(self.stats['last_minute_commands']) > 1:
                minutes_span = max(1, len(set(self.stats['last_minute_commands'])))
                self.stats['commands_per_minute'] = len(self.stats['last_minute_commands']) / minutes_span
    
    def get_recent_entries(self, limit=50):
        """Get recent command entries"""
        with self.lock:
            return list(self.entries)[-limit:]
    
    def get_stats(self):
        """Get system statistics"""
        with self.lock:
            uptime = time.time() - self.stats['start_time']
            return {
                'total_commands': self.stats['total_commands'],
                'commands_per_minute': round(self.stats['commands_per_minute'], 1),
                'errors': self.stats['errors'],
                'uptime': round(uptime),
                'uptime_str': str(datetime.fromtimestamp(uptime) - datetime.fromtimestamp(0)).split('.')[0]
            }

# Global command logger instance
command_logger = CommandLogger()


class ConfigurationError(ValueError):
    """Raised when an instrument definition file is structurally invalid."""


class ExcelReader:
    """Read and structurally validate CSV or XLSX configuration rows."""

    COLUMNS = ("Equipment", "Port", "Command", "Response", "Validation")

    @classmethod
    def _normalize_headers(cls, headers, source):
        normalized = [str(header or '').strip() for header in headers]
        if not normalized or all(not header for header in normalized):
            raise ConfigurationError(f"{source}: missing header row")
        if any(not header for header in normalized):
            raise ConfigurationError(f"{source}: header contains an unnamed column")
        if len(set(normalized)) != len(normalized):
            raise ConfigurationError(f"{source}: header contains duplicate columns")

        missing = [column for column in cls.COLUMNS if column not in normalized]
        unknown = [column for column in normalized if column not in cls.COLUMNS]
        if missing:
            raise ConfigurationError(f"{source}: missing required columns: {missing}")
        if unknown:
            raise ConfigurationError(f"{source}: unsupported columns: {unknown}")
        return normalized

    @classmethod
    def read_excel_as_csv(cls, excel_path):
        """Read the active worksheet of an XLSX definition file."""
        try:
            try:
                import openpyxl
            except ImportError:
                raise ConfigurationError(
                    "XLSX support requires the 'excel' project extra"
                ) from None

            workbook = openpyxl.load_workbook(excel_path, read_only=True)
            worksheet = workbook.active
            headers = cls._normalize_headers(
                [cell.value for cell in worksheet[1]], excel_path
            )

            data = []
            for row in worksheet.iter_rows(min_row=2, values_only=True):
                values = list(row)
                if len(values) > len(headers) and any(values[len(headers):]):
                    raise ConfigurationError(
                        f"{excel_path}: worksheet row has data beyond the declared columns"
                    )
                row_dict = {
                    header: str(values[index]).strip()
                    if index < len(values) and values[index] is not None
                    else ''
                    for index, header in enumerate(headers)
                }
                data.append(row_dict)

            workbook.close()
            return data
        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(f"{excel_path}: unable to read XLSX file: {e}") from e

    @classmethod
    def read_csv(cls, csv_path):
        """Read a CSV file and reject rows whose fields spill past the header."""
        try:
            data = []
            with open(csv_path, 'r', newline='', encoding='utf-8-sig') as csvfile:
                sample = csvfile.read(4096)
                csvfile.seek(0)

                try:
                    delimiter = csv.Sniffer().sniff(sample, delimiters=',;\t').delimiter
                except csv.Error:
                    delimiter = ','

                logger.debug(f"Using delimiter: '{delimiter}'")
                reader = csv.DictReader(csvfile, delimiter=delimiter)
                reader.fieldnames = cls._normalize_headers(reader.fieldnames or [], csv_path)

                for row_num, row in enumerate(reader, 2):
                    extras = row.pop(None, [])
                    if extras:
                        raise ConfigurationError(
                            f"{csv_path}: row {row_num} has fields beyond the declared columns; "
                            "quote values containing commas"
                        )
                    clean_row = {
                        key: str(value or '').strip() for key, value in row.items()
                    }
                    if any(clean_row.values()):
                        data.append(clean_row)

            return data
        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(f"{csv_path}: unable to read CSV file: {e}") from e


def _is_dmm(name, instrument_id) -> bool:
    identity = f"{name} {instrument_id}".upper()
    return "DMM" in identity


class SCPIInstrument:
    """Represents a single SCPI instrument with its command set"""

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
            self.vna_capabilities = VNACapabilities.create(
                model,
            )
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
        """Simulate VISA Device Clear operation"""
        logger.info(f"[VISA-CLR] VISA Device Clear for {self.name}")
        
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
        if self.vna_measurements is not None:
            self.vna_measurements.reset()
        if self.vna_sweeps is not None:
            self.vna_sweeps.reset()
        if self.vna_data is not None:
            self.vna_data.reset()
        if self.vna_active_device is not None:
            self.vna_active_device.reset()
        if self.vna_pulse is not None:
            self.vna_pulse.reset()
        if self.vna_advanced is not None:
            self.vna_advanced.reset()
        if self.vna_time_domain is not None:
            self.vna_time_domain.reset()
        if self.vna_mixer is not None:
            self.vna_mixer.reset()
        if self.scalar_data is not None:
            self.scalar_data.reset()
        if self.power_supply is not None:
            self.power_supply.reset()
        self.status.clear_status()
        self.output_queue.clear()
        return ''

    def begin_operation(self, name):
        """Start overlapped work that participates in OPC, OPC?, WAI, and ABORt."""
        return self.operation_manager.begin(name)

    def external_trigger(self, channel=None):
        """Inject an external trigger edge into one channel or all waiting channels."""
        return self.acquisition.external_trigger(channel)

    def attach_scenario(self, scenario):
        """Attach a shared ScenarioDefinition or ScenarioPlayer to this instrument."""
        from .scenario import ScenarioPlayer

        player = scenario if isinstance(scenario, ScenarioPlayer) else ScenarioPlayer(scenario)
        if self.vna_data is not None:
            self.vna_data.attach(player)
        if self.scalar_data is not None:
            self.scalar_data.attach(player)
        if self.csv_compatibility is not None:
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
                'model': profile.model,
                'instrument_class': profile.instrument_class,
                'firmware': profile.firmware,
                'ports': profile.ports,
                'source_count': profile.source_count,
                'hardware_features': sorted(profile.hardware_features),
                'applications': list(profile.applications),
                'frequency_minimum': profile.frequency_minimum,
                'frequency_maximum': profile.frequency_maximum,
            }
        return {
            'identity': {
                'manufacturer': identity[0],
                'reported_model': identity[1],
                'serial_number': identity[2],
                'firmware': identity[3],
            },
            'status': self.status.inspect(),
            'operations': self.operation_manager.inspect(),
            'acquisition': self.acquisition.inspect(),
            'scenario': self.scenario_control.inspect(),
            'measurements': (
                self.vna_measurements.inspect() if self.vna_measurements is not None else None
            ),
            'scalar': self.scalar_data.inspect() if self.scalar_data is not None else None,
            'power_supply': (
                self.power_supply.inspect() if self.power_supply is not None else None
            ),
            'capabilities': capabilities,
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

    def queue_command_response(self, command, *, termination=b'\n'):
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
                return ''
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
        """Process a SCPI command and return response"""
        self.last_command = (
            command.decode('utf-8', errors='replace') if isinstance(command, bytes) else command
        )
        self.command_count += 1

        command = command.strip()
        if not command:
            return ''

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

        # Byte input remains binary-safe for typed commands. The CSV adapter is
        # intentionally text-only and rejects undecodable input.
        if isinstance(command, bytes):
            return self._process_single_command(command)
        
        # Handle command chains
        if ';' in command:
            responses = []
            for cmd in command.split(';'):
                response = self._process_single_command(cmd.strip())
                if response:
                    responses.append(response)
            return ';'.join(responses) if responses else ''
        
        return self._process_single_command(command)

    def _process_single_command(self, command):
        """Process a single SCPI command"""
        try:
            parsed = parse_program_message(command).commands[0]
            return self.core_registry.dispatch(parsed)
        except SCPIParseError as error:
            self.error_queue.push(-102, str(error))
            return ''
        except SCPICommandError as error:
            if not error.message.startswith("Undefined header"):
                self.error_queue.push(error)
                return ''

        try:
            handled, result = self.csv_compatibility.dispatch(command)
        except Exception:
            self.error_queue.push(-310, f"command execution failed; {command}")
            return ''
        if handled:
            if isinstance(result, (BinaryResponse, bytes, bytearray, memoryview)):
                return result
            return str(result) if result is not None else ''
        self.error_queue.push(-113, command)
        return ''


class SCPIServer:
    """Binary-safe TCP server for a single SCPI instrument session."""

    def __init__(
        self,
        instrument,
        manager,
        host='localhost',
        port=5025,
        *,
        transport_config=None,
    ):
        self.instrument = instrument
        self.manager = manager
        self.host = host
        self.port = port
        self.transport_config = transport_config or SocketTransportConfig()
        self.socket = None
        self.running = False
        self.clients = []
        self.thread = None
        self._client_thread = None
        self._clients_lock = threading.RLock()
        self._session_lock = threading.Lock()
        
    def start(self):
        """Start the TCP server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(self.transport_config.backlog)
            self.socket.settimeout(self.transport_config.accept_poll_interval)
            self.port = self.socket.getsockname()[1]
            self.running = True
            
            self.thread = threading.Thread(target=self._server_loop, daemon=True)
            self.thread.start()
            
            logger.info(f"Started SCPI server for '{self.instrument.name}' on {self.host}:{self.port}")
            self._notify_dashboard_state('server-started')
            return True
            
        except Exception as e:
            logger.error(f"Failed to start server for {self.instrument.name}: {e}")
            if self.socket:
                self.socket.close()
                self.socket = None
            return False

    def stop(self):
        """Stop the TCP server"""
        self.running = False

        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

        with self._clients_lock:
            clients = tuple(self.clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass

        current = threading.current_thread()
        if self._client_thread and self._client_thread is not current:
            self._client_thread.join(timeout=1)
        if self.thread and self.thread is not current:
            self.thread.join(timeout=1)

        logger.info(f"Stopped SCPI server for '{self.instrument.name}'")
        self._notify_dashboard_state('server-stopped')

    def _server_loop(self):
        """Main server loop"""
        while self.running:
            try:
                client_socket, address = self.socket.accept()
                if not self._session_lock.acquire(blocking=False):
                    logger.warning(
                        "Rejected additional client for %s from %s",
                        self.instrument.name,
                        address,
                    )
                    client_socket.close()
                    continue
                logger.info(f"Client connected to {self.instrument.name} from {address}")

                self._client_thread = threading.Thread(
                    target=self._run_client,
                    args=(client_socket, address),
                    daemon=True
                )
                self._client_thread.start()

            except socket.timeout:
                continue
            except (OSError, AttributeError):
                if self.running:
                    logger.error(f"Server socket error for {self.instrument.name}")
                break

    def _run_client(self, client_socket, address):
        try:
            self._handle_client(client_socket, address)
        finally:
            with self._clients_lock:
                if client_socket in self.clients:
                    self.clients.remove(client_socket)
            try:
                client_socket.close()
            except OSError:
                pass
            self._session_lock.release()
            self._notify_dashboard_state('client-disconnected')

    def _handle_client(self, client_socket, address):
        """Receive and execute framed messages for the active session."""
        with self._clients_lock:
            self.clients.append(client_socket)
        self._notify_dashboard_state('client-connected')

        try:
            self.instrument.visa_device_clear()
            config = self.transport_config
            framer = SocketMessageFramer(config)
            poll_timeout = min(0.1, config.accept_poll_interval)
            client_socket.settimeout(poll_timeout)
            last_activity = time.monotonic()

            while self.running:
                try:
                    data = client_socket.recv(config.receive_chunk_size)
                    if not data:
                        break
                    last_activity = time.monotonic()
                    for message in framer.feed(data):
                        if message.strip():
                            self._execute_message(client_socket, message)
                except socket.timeout:
                    now = time.monotonic()
                    if (
                        framer.buffered_bytes
                        and config.idle_frame_timeout is not None
                        and now - last_activity >= config.idle_frame_timeout
                    ):
                        message = framer.flush_unterminated()
                        if message and message.strip():
                            self._execute_message(client_socket, message)
                        last_activity = now
                    if (
                        config.client_idle_timeout is not None
                        and now - last_activity >= config.client_idle_timeout
                    ):
                        break
                except (ConnectionResetError, BrokenPipeError):
                    break
                except MessageTooLarge as exc:
                    self.instrument.error_queue.push(-363, str(exc))
                    logger.warning("Closed oversized SCPI message from %s: %s", address, exc)
                    break
                except Exception as exc:
                    logger.error("Client handling error: %s", exc)
                    break
        except Exception as exc:
            logger.error("Client %s error: %s", address, exc)

    def _execute_message(self, client_socket, message):
        self.instrument.queue_command_response(
            message,
            termination=self.transport_config.write_termination,
        )

        if self.instrument.output_queue:
            client_socket.settimeout(self.transport_config.send_timeout)
            client_socket.sendall(self.instrument.read_output())
            client_socket.settimeout(min(0.1, self.transport_config.accept_poll_interval))

    def _notify_dashboard_state(self, reason):
        dashboard = getattr(self.manager, 'web_dashboard', None)
        if HAS_FLASK and dashboard is not None:
            dashboard.emit_state_changed(reason)

    def execute_control_command(self, command):
        """Serialize dashboard execution with the physical-style TCP session."""
        return self.execute_control_action(
            lambda instrument: instrument.process_command(command)
        )

    def execute_control_action(self, action):
        """Run one dashboard mutation under the instrument session lock."""
        if not self._session_lock.acquire(blocking=False):
            raise RuntimeError("instrument is busy with an active client session")
        try:
            return action(self.instrument)
        finally:
            self._session_lock.release()

def _dashboard_display_response(response):
    if response in (None, ''):
        return '(no response)'
    if isinstance(response, (bytes, bytearray, memoryview)):
        return f"<{len(response)} binary bytes>"
    return str(response)


class WebDashboard:
    """Flask-based web dashboard for SCPI emulator"""
    
    def __init__(self, emulator_manager, host='127.0.0.1', port=8081, *, auth_token=None):
        if not HAS_FLASK:
            logger.error("Flask not available. Web dashboard disabled.")
            return
            
        self.manager = emulator_manager
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.csrf_token = secrets.token_urlsafe(32)
        self._mutation_lock = threading.RLock()
        if host not in ('localhost', '127.0.0.1', '::1') and not auth_token:
            raise ValueError("remote dashboard binding requires an authentication token")
        
        # Create Flask app
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = secrets.token_hex(32)
        self.app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
        self.socketio = SocketIO(self.app)
        self._observed_instruments = set()
        self._attach_instrument_observers()
        
        self._setup_routes()
        self._setup_socketio()
        
    def _setup_routes(self):
        """Setup Flask routes"""

        @self.app.before_request
        def protect_control_plane():
            if not request.path.startswith('/api/'):
                return None
            if self.auth_token:
                supplied = request.headers.get('Authorization', '')
                expected = f'Bearer {self.auth_token}'
                if not secrets.compare_digest(supplied, expected):
                    return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
            if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
                supplied = request.headers.get('X-SCPI-CSRF', '')
                if not secrets.compare_digest(supplied, self.csrf_token):
                    return jsonify({'status': 'error', 'message': 'CSRF validation failed'}), 403
            return None

        @self.app.after_request
        def add_security_headers(response):
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Content-Security-Policy'] = (
                "base-uri 'self'; frame-ancestors 'none'; object-src 'none'"
            )
            if request.path.startswith('/api/'):
                response.headers['Cache-Control'] = 'no-store'
            return response
        
        @self.app.route('/')
        def dashboard():
            return render_template(
                'dashboard.html',
                csrf_token=self.csrf_token,
                auth_required=bool(self.auth_token),
            )
        
        @self.app.route('/api/status')
        def api_status():
            """Get system status"""
            return jsonify(self.status_payload())

        @self.app.route('/api/session')
        def api_session():
            """Return the mutation token to an authenticated API client."""
            return jsonify({'csrf_token': self.csrf_token})
        
        @self.app.route('/api/commands')
        def api_commands():
            """Get recent commands"""
            return jsonify(command_logger.get_recent_entries())

        def scenario_instrument(instrument_id):
            entry = self.manager.instruments.get(instrument_id)
            if entry is None:
                return None
            if isinstance(entry, dict):
                return entry.get('instrument')
            return getattr(entry, 'instrument', None)

        def execute_scenario_action(instrument_id, instrument, action):
            server = self.manager.servers.get(instrument_id)
            if server is None:
                with self._mutation_lock:
                    return action(instrument)
            return server.execute_control_action(action)

        @self.app.route('/api/scenario/<instrument_id>')
        def api_scenario_status(instrument_id):
            instrument = scenario_instrument(instrument_id)
            if instrument is None:
                return jsonify({'status': 'error', 'message': 'Instrument not found'}), 404
            return jsonify({'status': 'success', 'scenario': instrument.scenario_control.inspect()})

        @self.app.route('/api/scenario/<instrument_id>', methods=['PUT'])
        def api_scenario_select(instrument_id):
            instrument = scenario_instrument(instrument_id)
            if instrument is None:
                return jsonify({'status': 'error', 'message': 'Instrument not found'}), 404
            payload = request.get_json(silent=True)
            if not request.is_json or not isinstance(payload, dict):
                return jsonify({'status': 'error', 'message': 'JSON object required'}), 415
            raw = payload.get('scenario', payload)
            if not isinstance(raw, dict):
                return jsonify({'status': 'error', 'message': 'scenario must be an object'}), 400
            try:
                definition = loads_scenario(json.dumps(raw))
                selected = execute_scenario_action(
                    instrument_id,
                    instrument,
                    lambda target: target.scenario_control.select(
                        definition, start=payload.get('start', False) is True
                    ),
                )
            except RuntimeError as exc:
                return jsonify({'status': 'error', 'message': str(exc)}), 409
            except (ScenarioError, ValueError, TypeError) as exc:
                return jsonify({'status': 'error', 'message': str(exc)}), 400
            self.emit_state_changed('scenario-selected', instrument_id)
            return jsonify({'status': 'success', 'scenario': selected})

        @self.app.route('/api/scenario/<instrument_id>/<action>', methods=['POST'])
        def api_scenario_action(instrument_id, action):
            instrument = scenario_instrument(instrument_id)
            if instrument is None:
                return jsonify({'status': 'error', 'message': 'Instrument not found'}), 404
            payload = request.get_json(silent=True) if request.data else {}
            if not isinstance(payload, dict):
                return jsonify({'status': 'error', 'message': 'JSON object required'}), 415
            if action not in {'start', 'pause', 'reset', 'step', 'fault', 'noise'}:
                return jsonify({'status': 'error', 'message': 'Unknown action'}), 404
            try:
                def mutate(target):
                    target_control = target.scenario_control
                    if action == 'start':
                        return target_control.start(
                            reset=payload.get('reset', False) is True,
                            seed=payload.get('seed'),
                        )
                    if action == 'pause':
                        return target_control.pause()
                    if action == 'reset':
                        return target_control.reset(seed=payload.get('seed'))
                    if action == 'step':
                        stream = payload.get('stream')
                        if stream is not None and not isinstance(stream, str):
                            raise ScenarioControlError('stream must be text')
                        return {'positions': target_control.step(stream)}
                    if action == 'fault':
                        return target_control.inject_fault(
                            payload.get('code'), payload.get('message')
                        )
                    if action == 'noise':
                        return target_control.set_noise(
                            payload.get('stream'), payload.get('amplitude')
                        )
                    raise AssertionError('validated scenario action was not handled')

                result = execute_scenario_action(instrument_id, instrument, mutate)
            except RuntimeError as exc:
                return jsonify({'status': 'error', 'message': str(exc)}), 409
            except (ScenarioError, ValueError, TypeError) as exc:
                return jsonify({'status': 'error', 'message': str(exc)}), 400
            self.emit_state_changed(f'scenario-{action}', instrument_id)
            return jsonify({'status': 'success', 'scenario': result})
        
        @self.app.route('/api/restart/<instrument_id>', methods=['POST'])
        def api_restart_instrument(instrument_id):
            """Restart a specific instrument"""
            try:
                if instrument_id in self.manager.servers:
                    server = self.manager.servers[instrument_id]
                    with self._mutation_lock:
                        server.stop()
                        restarted = server.start()

                    if restarted:
                        self.emit_state_changed('server-restarted', instrument_id)
                        return jsonify({'status': 'success', 'message': f'Restarted {instrument_id}'})
                    else:
                        return jsonify({'status': 'error', 'message': f'Failed to restart {instrument_id}'}), 500
                else:
                    return jsonify({'status': 'error', 'message': f'Instrument {instrument_id} not found'}), 404
                    
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500
        
        @self.app.route('/api/stop_all', methods=['POST'])
        def api_stop_all():
            """Stop all instruments"""
            try:
                with self._mutation_lock:
                    self.manager.stop_all_servers()
                self.emit_state_changed('servers-stopped')
                return jsonify({'status': 'success', 'message': 'All servers stopped'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500
        
        @self.app.route('/api/start_all', methods=['POST'])
        def api_start_all():
            """Start all instruments"""
            try:
                with self._mutation_lock:
                    started = self.manager.start_all_servers()
                if started:
                    self.emit_state_changed('servers-started')
                    return jsonify({'status': 'success', 'message': 'All servers started'})
                else:
                    return jsonify({'status': 'error', 'message': 'Failed to start some servers'}), 500
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500
            
        @self.app.route('/api/send_command/<instrument_id>', methods=['POST'])
        def api_send_command(instrument_id):
            try:
                if instrument_id not in self.manager.servers:
                    return jsonify({'status': 'error', 'message': f'Instrument {instrument_id} not found'}), 404
                payload = request.get_json(silent=True)
                if not request.is_json or not isinstance(payload, dict):
                    return jsonify({'status': 'error', 'message': 'JSON object required'}), 415
                command = payload.get('command', '')
                if not isinstance(command, str):
                    return jsonify({'status': 'error', 'message': 'Command must be text'}), 400
                command = command.strip()
                if not command:
                    return jsonify({'status': 'error', 'message': 'No command provided'}), 400
                if len(command.encode('utf-8')) > 1024 * 1024:
                    return jsonify({'status': 'error', 'message': 'Command is too large'}), 413
                server = self.manager.servers[instrument_id]
                try:
                    response = server.execute_control_command(command)
                except RuntimeError as exc:
                    return jsonify({'status': 'error', 'message': str(exc)}), 409
                error = server.instrument.error_queue.last_response()
                return jsonify({'status': 'success', 'message': 'Command sent', 'response': response, 'error': error})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500


    
    def _setup_socketio(self):
        """Setup WebSocket events for real-time updates"""
        
        @self.socketio.on('connect')
        def handle_connect(auth=None):
            if self.auth_token:
                supplied = auth.get('token', '') if isinstance(auth, dict) else ''
                if not secrets.compare_digest(supplied, self.auth_token):
                    return False
            logger.info("Web client connected")
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            logger.info("Web client disconnected")
    
    def _attach_instrument_observers(self):
        for entry in self.manager.instruments.values():
            instrument = entry['instrument']
            marker = id(instrument)
            if marker not in self._observed_instruments:
                instrument.add_command_observer(self._handle_instrument_command)
                instrument.acquisition.add_trigger_listener(
                    lambda channel, instrument_id=instrument.id: self.emit_state_changed(
                        'acquisition-triggered', instrument_id
                    )
                )
                instrument.acquisition.add_completion_listener(
                    lambda channel, instrument_id=instrument.id: self.emit_state_changed(
                        'acquisition-complete', instrument_id
                    )
                )
                self._observed_instruments.add(marker)

    def _handle_instrument_command(self, instrument, command, response, error):
        display_response = _dashboard_display_response(response)
        command_logger.log_command(
            instrument.name,
            command,
            display_response,
            error,
        )
        self.emit_command_update(instrument.name, command, display_response, error)

    def status_payload(self):
        """Build one authoritative non-destructive dashboard snapshot."""
        self._attach_instrument_observers()
        instruments = []
        for inst_id, inst_data in self.manager.instruments.items():
            instrument = inst_data['instrument']
            port = inst_data['port']
            server = self.manager.servers.get(inst_id)
            instruments.append({
                'id': inst_id,
                'name': instrument.name,
                'port': port,
                'running': server is not None and server.running,
                'clients': len(getattr(server, 'clients', ())) if server else 0,
                'commands': instrument.command_count,
                'errors': len(instrument.error_queue),
                'state': dict(instrument.state),
                'snapshot': instrument.inspect_state(),
            })
        return {
            'instruments': instruments,
            'stats': command_logger.get_stats(),
            'system': {
                'total_instruments': len(self.manager.instruments),
                'running_servers': sum(
                    bool(getattr(server, 'running', False))
                    for server in self.manager.servers.values()
                ),
                'timestamp': time.time(),
            },
        }

    def emit_state_changed(self, reason, instrument_id=None):
        """Tell clients to coalesce and fetch a fresh authoritative snapshot."""
        self.socketio.emit('state_changed', {
            'reason': reason,
            'instrument_id': instrument_id,
            'timestamp': time.time(),
        })

    def emit_command_update(self, instrument_name, command, response, error=None):
        """Emit real-time command update to web clients"""
        if hasattr(self, 'socketio'):
            timestamp = time.time()
            self.socketio.emit('command_update', {
                'timestamp': timestamp,
                'time_str': datetime.fromtimestamp(timestamp).strftime('%H:%M:%S'),
                'instrument': instrument_name,
                'command': command,
                'response': response,
                'error': error
            })
            self.emit_state_changed('command')
    def start(self):
        """Start the web dashboard"""
        if not HAS_FLASK:
            logger.warning("Flask not available. Web dashboard not started.")
            return False
            
        try:
            logger.info(f"Starting web dashboard on http://{self.host}:{self.port}")
            
            # Start in a separate thread
            dashboard_thread = threading.Thread(
                target=lambda: self.socketio.run(
                    self.app, 
                    host=self.host, 
                    port=self.port, 
                    debug=False,
                    allow_unsafe_werkzeug=True
                ),
                daemon=True
            )
            dashboard_thread.start()
            
            # Give it a moment to start
            time.sleep(1)
            logger.info(f" Web dashboard started! Open: http://localhost:{self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start web dashboard: {e}")
            return False


def compatibility_instrument_id(name: str) -> str:
    """Return the stable identifier used by legacy Equipment blocks."""
    instrument_id = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    if not instrument_id:
        raise ConfigurationError("equipment name must contain a letter or number")
    return instrument_id


def validate_compatibility_rule(rule: str, row_num: int) -> None:
    """Validate one legacy CSV parameter rule."""
    if not rule or rule == 'bool':
        return
    if rule.startswith('range:'):
        values = rule.split(':', 1)[1].split(',')
        if len(values) != 2:
            raise ConfigurationError(
                f"row {row_num}: range validation requires exactly two bounds"
            )
        try:
            lower, upper = map(float, values)
        except ValueError as error:
            raise ConfigurationError(f"row {row_num}: range bounds must be numeric") from error
        if lower > upper:
            raise ConfigurationError(f"row {row_num}: range lower bound exceeds upper bound")
        return
    if rule.startswith('enum:'):
        values = [value.strip() for value in rule.split(':', 1)[1].split(',')]
        if not values or any(not value for value in values):
            raise ConfigurationError(
                f"row {row_num}: enum validation requires non-empty values"
            )
        return
    raise ConfigurationError(f"row {row_num}: unsupported validation rule '{rule}'")


def load_compatibility_instruments(file_path, port_start=5025, *, reserved_ports=()):
    """Parse and construct legacy CSV/XLSX instruments without mutating a manager."""
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        raise ConfigurationError(f"file not found: {file_path}")
    if file_path_obj.suffix.lower() == '.xlsx':
        data = ExcelReader.read_excel_as_csv(file_path)
    elif file_path_obj.suffix.lower() == '.csv':
        data = ExcelReader.read_csv(file_path)
    else:
        raise ConfigurationError(f"unsupported file type: {file_path_obj.suffix}")
    if not data:
        raise ConfigurationError(f"no data found in file: {file_path}")

    loaded_instruments = {}
    used_ports = set(reserved_ports)
    current_instrument = None
    current_commands = set()
    current_port = port_start
    commands_added = 0

    logger.info(f"Processing {len(data)} rows from {file_path}")
    for row_num, row in enumerate(data, 2):
        equipment_name = row.get('Equipment', '').strip()
        port_text = row.get('Port', '').strip()
        command = row.get('Command', '').strip()
        response = row.get('Response', '').strip()
        validation = row.get('Validation', '').strip()

        if not equipment_name and port_text:
            raise ConfigurationError(
                f"row {row_num}: port is only allowed when declaring equipment"
            )
        if not command and (response or validation):
            raise ConfigurationError(
                f"row {row_num}: response or validation provided without a command"
            )

        if equipment_name:
            instrument_id = compatibility_instrument_id(equipment_name)
            if instrument_id in loaded_instruments:
                raise ConfigurationError(
                    f"row {row_num}: duplicate equipment identifier '{instrument_id}'"
                )
            if port_text:
                try:
                    port = int(port_text)
                except ValueError as error:
                    raise ConfigurationError(
                        f"row {row_num}: port must be an integer"
                    ) from error
            else:
                while current_port in used_ports:
                    current_port += 1
                port = current_port
                current_port += 1
            if not 1 <= port <= 65535:
                raise ConfigurationError(f"row {row_num}: port must be between 1 and 65535")
            if port in used_ports:
                raise ConfigurationError(f"row {row_num}: duplicate port {port}")

            current_instrument = SCPIInstrument(equipment_name, instrument_id)
            loaded_instruments[instrument_id] = {
                'instrument': current_instrument,
                'port': port,
            }
            used_ports.add(port)
            current_commands = set()
            logger.info(f"Row {row_num}: Created instrument: {equipment_name} (Port: {port})")

        if command:
            if current_instrument is None:
                raise ConfigurationError(
                    f"row {row_num}: command appears before any equipment declaration"
                )
            command_key = command.upper()
            if command_key in current_commands:
                raise ConfigurationError(
                    f"row {row_num}: duplicate command '{command}' for "
                    f"'{current_instrument.name}'"
                )
            if validation and '(.+)' not in command and '{value}' not in command:
                raise ConfigurationError(
                    f"row {row_num}: validation requires a parameterized command"
                )
            validate_compatibility_rule(validation, row_num)
            if command_key == "*IDN?":
                current_instrument.identification = response
            if command_key not in {"*IDN?", "*RST", "*TST?", "SYST:VERS?"}:
                current_instrument.csv_compatibility.add_command(command, response, validation)
            current_commands.add(command_key)
            commands_added += 1

    for instrument_data in loaded_instruments.values():
        instrument_data['instrument'].csv_compatibility.link_stateful_commands()
    if not loaded_instruments:
        raise ConfigurationError(f"no valid instruments found in file: {file_path}")
    return loaded_instruments, commands_added


def load_compatibility_directory(directory_path, port_start=5025):
    """Load every CSV in a directory with globally unique IDs and sequential ports."""
    directory = Path(directory_path)
    if not directory.is_dir():
        raise ConfigurationError(f"directory does not exist: {directory}")
    sources = tuple(
        sorted(
            (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == '.csv'),
            key=lambda path: path.name.casefold(),
        )
    )
    if not sources:
        raise ConfigurationError(f"directory contains no CSV files: {directory}")

    loaded_instruments = {}
    equipment_sources: dict[str, Path] = {}
    port_sources: dict[int, Path] = {}
    used_ports: set[int] = set()
    next_port = port_start
    commands_added = 0
    for source in sources:
        rows = ExcelReader.read_csv(source)
        for row in rows:
            equipment_name = row.get('Equipment', '').strip()
            if not equipment_name:
                continue
            equipment_id = compatibility_instrument_id(equipment_name)
            if equipment_id in equipment_sources:
                first_source = equipment_sources[equipment_id]
                raise ConfigurationError(
                    f"duplicate equipment name {equipment_name!r} in "
                    f"{str(first_source)!r} and {str(source)!r}"
                )
        try:
            loaded, count = load_compatibility_instruments(
                source,
                next_port,
                reserved_ports=used_ports,
            )
        except ConfigurationError as error:
            raise ConfigurationError(f"{source}: {error}") from error
        for equipment_id, item in loaded.items():
            port = item['port']
            if port in used_ports:
                first_source = port_sources[port]
                raise ConfigurationError(
                    f"duplicate port {port} in {str(first_source)!r} and {str(source)!r}"
                )
            loaded_instruments[equipment_id] = item
            equipment_sources[equipment_id] = source
            port_sources[port] = source
            used_ports.add(port)
        commands_added += count
        while next_port in used_ports:
            next_port += 1
    return loaded_instruments, commands_added


def load_compatibility_path(path, port_start=5025):
    """Load one compatibility file or every CSV in a directory."""
    source = Path(path)
    if source.is_dir():
        return load_compatibility_directory(source, port_start)
    return load_compatibility_instruments(source, port_start)


class SCPIEmulatorManager:
    """Manages multiple SCPI instrument emulators with web dashboard"""

    def __init__(self):
        self.instruments = {}
        self.servers = {}
        self.running = False
        self.web_dashboard = None
        self._bench_runtime = None
        self._active_source = None
        self._interactive_catalog = None
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("Received shutdown signal, stopping servers...")
        self.stop_active_servers()
        sys.exit(0)

    @staticmethod
    def _instrument_id(name):
        return compatibility_instrument_id(name)

    @staticmethod
    def _validate_rule(rule, row_num):
        validate_compatibility_rule(rule, row_num)

    def load_from_file(self, file_path, port_start=5025):
        """Load instrument definitions from Excel or CSV file"""
        if self.active_running:
            logger.error("Stop the active instruments before loading another configuration")
            return False
        try:
            loaded_instruments, commands_added = load_compatibility_path(file_path, port_start)
            self.instruments = loaded_instruments
            self._bench_runtime = None
            self._active_source = str(Path(file_path).resolve())
            logger.info(
                f"Successfully loaded {len(loaded_instruments)} instruments with "
                f"{commands_added} commands"
            )
            return True
        except ConfigurationError as e:
            logger.error(f"Invalid instrument configuration: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loading file: {e}")
            return False

    def load_bench_file(self, file_path):
        """Compose a precise bench transactionally and make it the interactive runtime."""
        from .bench import BenchComposer, BenchRuntime, load_bench
        from .drivers import build_driver_catalog

        if self.active_running:
            raise ConfigurationError(
                "stop the active instruments before loading another configuration"
            )
        path = Path(file_path).resolve()
        definition = load_bench(path)
        uses_csv = any(
            item.driver.casefold() == 'csv-instruments'
            for item in definition.instruments
        )
        catalog = build_driver_catalog(csv_directory=path.parent if uses_csv else None)
        runtime = BenchRuntime(BenchComposer(catalog).compose(definition))
        self._bench_runtime = runtime
        self._active_source = str(path)
        return runtime

    def use_bench_runtime(self, runtime, source=None):
        """Adopt an already composed runtime, such as one created by the CLI."""
        self._bench_runtime = runtime
        self._active_source = str(Path(source).resolve()) if source is not None else None

    @property
    def active_runtime(self):
        return self._bench_runtime or self

    @property
    def active_running(self):
        return self._bench_runtime.running if self._bench_runtime is not None else self.running

    @property
    def active_instruments(self):
        return (
            self._bench_runtime.instruments
            if self._bench_runtime is not None
            else self.instruments
        )

    def start_active_servers(self):
        if self._bench_runtime is not None:
            self._bench_runtime.start()
            return True
        return self.start_all_servers()

    def stop_active_servers(self):
        if self._bench_runtime is not None:
            self._bench_runtime.stop()
        else:
            self.stop_all_servers()

    def start_active_dashboard(self, host='127.0.0.1', port=8081, *, auth_token=None):
        return self.active_runtime.start_web_dashboard(
            host, port, auth_token=auth_token
        )

    def configured_instruments(self):
        """Return UI-neutral rows for every active configured instrument."""
        rows = []
        if self._bench_runtime is not None:
            for composed in self._bench_runtime.bench.instruments:
                definition = composed.definition
                server = self._bench_runtime.servers.get(definition.id)
                rows.append(
                    _interactive_instrument_row(
                        definition.id,
                        composed.instrument,
                        composed.resource_name(),
                        bool(getattr(server, 'running', False)),
                        model=definition.model,
                        serial=definition.serial_number,
                        reported_model=definition.reported_model,
                    )
                )
            return tuple(rows)
        for instrument_id, item in self.instruments.items():
            server = self.servers.get(instrument_id)
            rows.append(
                _interactive_instrument_row(
                    instrument_id,
                    item['instrument'],
                    f"TCPIP::127.0.0.1::{item['port']}::SOCKET",
                    bool(getattr(server, 'running', False)),
                )
            )
        return tuple(rows)

    def start_all_servers(self, host='localhost'):
        """Start TCP servers for all instruments"""
        success_count = 0
        
        for inst_id, inst_data in self.instruments.items():
            instrument = inst_data['instrument']
            port = inst_data['port']
            
            server = SCPIServer(instrument, self, host, port)
            if server.start():
                self.servers[inst_id] = server
                success_count += 1
            else:
                logger.error(f"Failed to start server for {instrument.name}")
                self.stop_all_servers()
                return False
        
        if success_count == len(self.instruments) and success_count > 0:
            self.running = True
            logger.info(f"Started {success_count} SCPI servers")
            return True
        else:
            logger.error("Failed to start any servers")
            return False

    def stop_all_servers(self):
        """Stop all TCP servers"""
        for server in self.servers.values():
            server.stop()
        
        self.servers.clear()
        self.running = False
        logger.info("All servers stopped")

    def start_web_dashboard(self, host='127.0.0.1', port=8081, *, auth_token=None):
        """Start the web dashboard"""
        if not HAS_FLASK:
            logger.warning("Flask not available. Cannot start web dashboard.")
            return False
            
        self.web_dashboard = WebDashboard(self, host, port, auth_token=auth_token)
        return self.web_dashboard.start()

    def interactive_mode(self):
        """Interactive command-line interface"""
        print(f"\n SCPI Emulator Manager {__version__} - Interactive Mode")
        print("=" * 60)
        print("Commands:")
        print("  load <path>       - Load a CSV/XLSX file or CSV folder")
        print("  load bench <file> - Load a precise bench JSON file")
        print("  bench <file>      - Alias for 'load bench <file>'")
        print("  instruments       - List configured instruments and VISA resources")
        print("  catalog [driver [model]] - Browse available drivers and models")
        print("  catalog csv <folder>     - Include CSV-defined models")
        print("  create bench <file>      - Guided create, validate, save, and load")
        print("  start             - Start the active instruments")
        print("  web               - Start dashboard for the active instruments")
        print("  status            - Show active configuration and server status")
        print("  stop              - Stop the active instruments")
        print("  help              - Show these commands")
        print("  quit              - Exit")
        print("=" * 60)
        
        while True:
            try:
                user_input = input("\nSCPI-MGR> ").strip()
                
                if not user_input:
                    continue
                
                parts = user_input.split(maxsplit=1)
                command = parts[0].lower()
                
                if command == 'quit':
                    if self.active_running:
                        self.stop_active_servers()
                    break
                
                elif command == 'load':
                    if len(parts) < 2:
                        print("Usage: load <path> or load bench <file>")
                        continue
                    argument = parts[1].strip()
                    if argument.casefold().startswith('bench '):
                        file_path = _interactive_path(argument[6:])
                        try:
                            self.load_bench_file(file_path)
                            print(f"[OK] Bench loaded: {file_path}")
                            self._print_configured_instruments()
                        except Exception as error:
                            print(f"[ERROR] Could not load bench {file_path!r}: {error}")
                    else:
                        file_path = _interactive_path(argument)
                        if self.load_from_file(file_path):
                            print(f"[OK] Instruments loaded: {file_path}")
                            self._print_configured_instruments()
                        else:
                            print(f"[ERROR] Could not load instruments from {file_path!r}")

                elif command == 'bench':
                    if len(parts) < 2:
                        print("Usage: bench <file>")
                        continue
                    file_path = _interactive_path(parts[1])
                    try:
                        self.load_bench_file(file_path)
                        print(f"[OK] Bench loaded: {file_path}")
                        self._print_configured_instruments()
                    except Exception as error:
                        print(f"[ERROR] Could not load bench {file_path!r}: {error}")

                elif command == 'instruments':
                    self._print_configured_instruments()

                elif command == 'catalog':
                    self._print_catalog(parts[1] if len(parts) > 1 else "")

                elif command == 'create':
                    if len(parts) < 2 or not parts[1].casefold().startswith('bench '):
                        print("Usage: create bench <file>")
                        continue
                    target = Path(_interactive_path(parts[1][6:])).resolve()
                    try:
                        from .bench import BenchBuildCancelled, BenchRuntime, GuidedBenchBuilder
                        from .drivers import build_driver_catalog

                        csv_directory = (
                            target.parent
                            if target.parent.is_dir() and any(target.parent.glob('*.csv'))
                            else None
                        )
                        catalog = build_driver_catalog(csv_directory=csv_directory)
                        composed = GuidedBenchBuilder(catalog).build_and_save(target)
                        self.use_bench_runtime(BenchRuntime(composed), target)
                        self._interactive_catalog = catalog
                        print(f"[OK] Bench saved and loaded: {target}")
                        self._print_configured_instruments()
                    except BenchBuildCancelled as error:
                        print(f"Bench creation cancelled: {error}")
                    except Exception as error:
                        print(f"[ERROR] Could not create bench {str(target)!r}: {error}")
                
                elif command == 'start':
                    if not self.active_instruments:
                        print("[ERROR] No instruments loaded. Use 'load' or 'load bench' first.")
                        continue
                    try:
                        started = self.start_active_servers()
                    except Exception as error:
                        print(f"[ERROR] Failed to start instruments: {error}")
                        continue
                    if not started:
                        print("[ERROR] Failed to start servers")
                    else:
                        print("[OK] Active instruments started")
                        self._print_configured_instruments()
                
                elif command == 'web':
                    try:
                        started = self.start_active_dashboard()
                    except Exception as error:
                        print(f"[ERROR] Failed to start web dashboard: {error}")
                        continue
                    if not started:
                        print("[ERROR] Failed to start web dashboard")
                    else:
                        print("[OK] Web dashboard started at http://127.0.0.1:8081")
                
                elif command == 'status':
                    source = self._active_source or "none"
                    state = "running" if self.active_running else "stopped"
                    print(f"Active configuration: {source}")
                    print(f"Server state: {state}")
                    self._print_configured_instruments()
                
                elif command == 'stop':
                    self.stop_active_servers()
                    print("[OK] Active instruments stopped")

                elif command == 'help':
                    print("Use load, load bench, create bench, instruments, catalog, start, web, status, stop, or quit.")
                
                else:
                    print(f"[ERROR] Unknown command: {command}")
                    
            except KeyboardInterrupt:
                print("\nShutting down...")
                if self.active_running:
                    self.stop_active_servers()
                break
            except EOFError:
                print("\nGoodbye!")
                if self.active_running:
                    self.stop_active_servers()
                break

    def _print_configured_instruments(self):
        rows = self.configured_instruments()
        if not rows:
            print("No instruments configured.")
            return
        print(f"Configured instruments ({len(rows)}):")
        for row in rows:
            print(
                f"  {row['id']}: {row['model']} | reports {row['reported_model']} | "
                f"serial {row['serial']} | "
                f"{row['state']} | {row['resource']}"
            )

    def _print_catalog(self, argument):
        from .drivers import CatalogError, build_driver_catalog

        argument = argument.strip()
        try:
            if argument.casefold().startswith('csv '):
                directory = _interactive_path(argument[4:])
                self._interactive_catalog = build_driver_catalog(csv_directory=directory)
                print(f"[OK] Included CSV instruments from {directory}")
                argument = 'csv-instruments'
            elif self._interactive_catalog is None:
                self._interactive_catalog = build_driver_catalog()
            selected = argument.split(maxsplit=1) if argument else []
            if not selected:
                print("Driver catalog:")
                for descriptor in self._interactive_catalog.descriptors:
                    print(
                        f"  {descriptor.id}: {descriptor.display_name} | "
                        f"{descriptor.maturity.value} | {len(descriptor.models)} model(s)"
                    )
                return
            driver = self._interactive_catalog.get(selected[0]).descriptor
            if len(selected) == 1:
                print(f"Driver {driver.id}: {driver.display_name} ({driver.maturity.value})")
                for model in driver.models:
                    print(
                        f"  {model.model}: {model.display_name} | "
                        f"firmware {', '.join(model.firmware_snapshots)}"
                    )
                return
            model = driver.model(selected[1])
            print(f"Model {model.model}: {model.display_name}")
            print(f"  Class: {model.instrument_class}")
            print(f"  Firmware: {', '.join(model.firmware_snapshots)}")
            print(
                "  Transports: "
                + ", ".join(
                    f"{item.name} ({item.support.value})" for item in driver.transports
                )
            )
            print(f"  Hardware features: {len(model.available_hardware_features)}")
            print(f"  Applications: {len(model.available_applications)}")
            if model.configuration_fields:
                print("  Configuration fields:")
                for field in model.configuration_fields:
                    default = f", default {field.default}" if field.default is not None else ""
                    choices = f", {len(field.choices)} choice(s)" if field.choices else ""
                    print(
                        f"    {field.name}: {field.value_type.value}{default}{choices} - "
                        f"{field.description}"
                    )
            else:
                print("  Configuration fields: none")
            if driver.scenario_inputs:
                print(
                    "  Scenario inputs: "
                    + ", ".join(
                        f"{item.kind} ({item.support.value})"
                        for item in driver.scenario_inputs
                    )
                )
            coverage = [item for item in driver.command_coverage if item.model == model.model]
            if coverage:
                print(
                    "  Command coverage: "
                    + ", ".join(
                        f"{item.implemented}/{item.documented} ({item.percent}%)"
                        for item in coverage
                    )
                )
        except (CatalogError, ConfigurationError, OSError, ValueError) as error:
            print(f"[ERROR] Could not browse catalog: {error}")


def _interactive_path(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
        return value[1:-1]
    return value


def _interactive_instrument_row(
    instrument_id,
    instrument,
    resource,
    running,
    *,
    model=None,
    serial=None,
    reported_model=None,
):
    identity = str(getattr(instrument, 'identification', '')).split(',', 3)
    model = model or (
        identity[1] if len(identity) == 4 else getattr(instrument, 'name', instrument_id)
    )
    serial = serial or (identity[2] if len(identity) == 4 else instrument_id)
    reported_model = reported_model or (
        identity[1] if len(identity) == 4 else getattr(instrument, 'name', instrument_id)
    )
    return {
        'id': instrument_id,
        'model': model,
        'reported_model': reported_model,
        'serial': serial,
        'state': 'running' if running else 'stopped',
        'resource': resource,
    }


def create_example_csv():
    """Create example CSV with validation examples"""
    data = [
        ['Equipment', 'Port', 'Command', 'Response', 'Validation'],
        ['Virtual DMM', '5555', 'MEAS:VOLT:DC?', '1.234567E+00', ''],
        ['', '', 'VOLT (.+)', 'OK', 'range:0,10'],
        ['', '', 'VOLT?', '5.0', ''],
        ['Debug Test Instrument', '5559', 'TEST_RANGE (.+)', 'Range OK: {value}', 'range:1,10'],
        ['', '', 'TEST_RANGE?', '5', ''],
        ['', '', 'TEST_ENUM (.+)', 'Enum OK: {value}', 'enum:A,B,C'],
        ['', '', 'TEST_ENUM?', 'A', ''],
    ]

    filename = 'scpi_instruments_example.csv'
    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        writer.writerows(data)

    print(f"Created example file: {filename}")


def build_parser():
    """Build the command-line parser without starting the emulator."""
    parser = argparse.ArgumentParser(
        description='Stateful SCPI instrument emulator for automation development and testing'
    )

    definitions = parser.add_mutually_exclusive_group()
    definitions.add_argument(
        '--load',
        '-l',
        metavar='PATH',
        help='Point at one CSV/XLSX file or a folder of CSVs',
    )
    definitions.add_argument(
        '--bench',
        metavar='FILE',
        help='Define a precise multi-instrument bench from JSON',
    )
    parser.add_argument('--start', '-s', action='store_true', help='Start TCP servers immediately')
    parser.add_argument('--web', '-w', action='store_true', help='Start web dashboard')
    parser.add_argument('--web-port', type=int, default=8081, help='Web dashboard port (default: 8081)')
    parser.add_argument('--web-host', default='127.0.0.1', help='Dashboard bind host (default: 127.0.0.1)')
    parser.add_argument('--port', '-p', type=int, default=5025, help='Starting port for instruments (default: 5025)')
    parser.add_argument('--host', default='localhost', help='Server host (default: localhost)')
    parser.add_argument('--create-example', action='store_true', help='Create example CSV file')
    parser.add_argument('--interactive', '-i', action='store_true', help='Start interactive mode')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--log-file', help='Write logs to this file in addition to stderr')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose, log_file=args.log_file)

    print(f"SCPI Instrument Emulator {__version__}")
    print("=" * 60)

    if args.create_example:
        create_example_csv()
        return 0

    # Create the simple compatibility manager. A precise bench replaces the runtime below.
    manager = SCPIEmulatorManager()
    runtime = manager
    resources = {}

    if args.load:
        try:
            loaded_instruments, commands_added = load_compatibility_path(args.load, args.port)
        except ConfigurationError as error:
            print(f"Error: could not load {args.load!r}: {error}", file=sys.stderr)
            return 1
        manager.instruments = loaded_instruments
        logger.info(
            f"Successfully loaded {len(loaded_instruments)} instruments with "
            f"{commands_added} commands"
        )
        resources = {
            instrument_id: f"TCPIP::{args.host}::{item['port']}::SOCKET"
            for instrument_id, item in loaded_instruments.items()
        }
    elif args.bench:
        from .bench import BenchComposer, BenchError, BenchRuntime, load_bench
        from .drivers import CatalogError, build_driver_catalog

        try:
            definition = load_bench(args.bench)
            uses_csv = any(
                item.driver.casefold() == 'csv-instruments'
                for item in definition.instruments
            )
            catalog = build_driver_catalog(
                csv_directory=Path(args.bench).resolve().parent if uses_csv else None
            )
            composed = BenchComposer(catalog).compose(definition)
            runtime = BenchRuntime(composed)
            manager.use_bench_runtime(runtime, args.bench)
            resources = composed.resources()
        except (BenchError, CatalogError, ConfigurationError) as error:
            print(f"Error: could not load bench {args.bench!r}: {error}", file=sys.stderr)
            return 1

    if (args.load or args.bench) and args.start:
        try:
            if args.load:
                started = runtime.start_all_servers(args.host)
            else:
                runtime.start()
                started = True
        except Exception as error:
            print(f"Error: could not start instruments: {error}", file=sys.stderr)
            return 1
        if not started:
            print("Error: could not start all configured instruments", file=sys.stderr)
            return 1

    if (args.load or args.bench) and args.web:
        try:
            dashboard_started = runtime.start_web_dashboard(
                args.web_host,
                args.web_port,
                auth_token=os.environ.get('SCPI_EMULATOR_WEB_TOKEN'),
            )
        except Exception as error:
            print(f"Error: could not start web dashboard: {error}", file=sys.stderr)
            return 1
        if not dashboard_started:
            print("Error: could not start web dashboard", file=sys.stderr)
            return 1

    # Start interactive mode
    if args.interactive or (not args.load and not args.bench and not args.create_example):
        manager.interactive_mode()
    elif (args.load or args.bench) and (args.start or args.web):
        print("\nSCPI Emulator running!")
        if args.start:
            print("VISA resources:")
            for instrument_id, resource in resources.items():
                print(f"   {instrument_id}: {resource}")
        if args.web:
            print(f"Web dashboard: http://{args.web_host}:{args.web_port}")
        print("\nPress Ctrl+C to stop...")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            runtime.stop_all_servers()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
