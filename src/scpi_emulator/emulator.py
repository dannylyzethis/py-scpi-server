#!/usr/bin/env python3
"""
SCPI Equipment Emulator - VERSION 2.3
ADDED: Web Dashboard with real-time monitoring and control

New Features:
- Web dashboard on http://localhost:8081
- Real-time command/response monitoring
- Remote instrument control
- Performance metrics visualization
- Configuration upload via web interface
"""

import csv
import socket
import threading
import time
import argparse
import sys
import re
import logging
import signal
from pathlib import Path
from datetime import datetime
from collections import deque
import os
from collections.abc import Sequence

from . import __version__
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
    PNACapabilities,
    detect_pna_model,
    parse_program_message,
    register_operation_commands,
    register_acquisition_commands,
    register_format_commands,
    register_capability_commands,
    register_status_commands,
)
from .socket_transport import MessageTooLarge, SocketMessageFramer, SocketTransportConfig

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


class SCPIInstrument:
    """Represents a single SCPI instrument with its command set"""

    def __init__(self, name, instrument_id, *, pna_capabilities=None):
        self.name = name
        self.id = instrument_id
        self.commands = {}
        self.state = {}
        self.status = StatusSystem()
        self.error_queue = self.status.error_queue
        self.operation_manager = OperationManager(self.status)
        self.acquisition = AcquisitionController(self.operation_manager, self.status)
        self.data_format = DataFormat()
        self.output_queue = OutputQueue(self.status)
        model = detect_pna_model(str(name), str(instrument_id))
        self.pna_capabilities = pna_capabilities
        if self.pna_capabilities is None and model is not None:
            self.pna_capabilities = PNACapabilities.create(model)
        registry_capabilities = (
            self.pna_capabilities.command_capabilities
            if self.pna_capabilities is not None
            else ()
        )
        self.core_registry = CommandRegistry(registry_capabilities)
        register_status_commands(self.core_registry, self.status)
        register_operation_commands(self.core_registry, self.operation_manager)
        register_acquisition_commands(self.core_registry, self.acquisition)
        register_format_commands(self.core_registry, self.data_format)
        if self.pna_capabilities is not None:
            register_capability_commands(self.core_registry, self.pna_capabilities)
        self.last_command = ""
        self.command_count = 0
        
        # Store validation info separately to survive device clear
        self.validation_rules = {}
        self.default_values = {}
        
        # Commands not yet owned by the typed registry remain on the legacy
        # dispatcher for CSV compatibility.
        self._add_legacy_fallback_commands()

    def _add_legacy_fallback_commands(self):
        """Register only core commands not yet implemented by typed modules."""
        self.commands.update({
            '*IDN?': lambda: f"SCPI_Emulator,{self.name},{self.id},2.3.0",
            '*RST': self._reset,
            '*TST?': self._self_test,
            'SYST:VERS?': lambda: '1999.0',
        })

    def visa_device_clear(self):
        """Simulate VISA Device Clear operation"""
        logger.info(f"[VISA-CLR] VISA Device Clear for {self.name}")
        
        self.operation_manager.abort()
        self.status.clear_status()
        self.output_queue.clear()
        self.last_command = ""
        self.command_count = 0
        
        self.link_stateful_commands()

    def _reset(self):
        self.operation_manager.abort()
        self.state.clear()
        self.status.clear_status()
        self.output_queue.clear()
        return ''

    def _self_test(self):
        return '0'

    def begin_operation(self, name):
        """Start overlapped work that participates in OPC, OPC?, WAI, and ABORt."""
        return self.operation_manager.begin(name)

    def external_trigger(self, channel=None):
        """Inject an external trigger edge into one channel or all waiting channels."""
        return self.acquisition.external_trigger(channel)

    def add_command(self, command, response, validation=None):
        """Add a command-response pair"""
        command = command.strip().upper()
        
        if '(.+)' in command or '{value}' in command:
            pattern = command.replace('{value}', r'(.+)')
            if '(.+)' not in pattern:
                pattern = pattern.replace('(.+)', r'(.+)')
            
            if validation:
                self.validation_rules[pattern] = validation
            
            self.commands[pattern] = self._create_parameterized_response(response, validation)
        else:
            self.commands[command] = lambda resp=response: str(resp)

    def add_binary_query(self, command, data, *, definite=True):
        """Add a byte-preserving binary query response to the active instrument."""
        command = command.strip().upper()
        if not command.endswith('?'):
            raise ValueError("binary response commands must be queries")
        payload = bytes(data)
        self.commands[command] = lambda: BinaryResponse(payload, definite=definite)

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

    def _create_parameterized_response(self, response_template, validation=None):
        """Create a function for parameterized responses"""
        def parameterized_response(*args, template=response_template, val=validation):
            if args and val:
                error = self._validate_generic(args[0], val)
                if error:
                    self.error_queue.push(error[0], error[1])
                    return ''
            
            response = template
            for i, arg in enumerate(args, 1):
                response = response.replace(f'{{param{i}}}', str(arg))
                response = response.replace('{value}', str(arg))
            
            return response
        
        parameterized_response._validation = validation
        return parameterized_response
    
    def _validate_generic(self, param, validation):
        """Generic validation"""
        if not validation:
            return None
        
        if validation.startswith('range:'):
            try:
                _, range_str = validation.split(':', 1)
                min_val, max_val = map(float, range_str.split(','))
                value = float(param)
                if not (min_val <= value <= max_val):
                    return -222, f"expected {min_val} to {max_val}, got {value}"
            except ValueError:
                return -104, f"cannot convert '{param}' to number"
        elif validation.startswith('enum:'):
            try:
                _, enum_str = validation.split(':', 1)
                valid_values = [v.strip().upper() for v in enum_str.split(',')]
                if param.upper() not in valid_values:
                    return -108, f"expected one of {valid_values}, got '{param}'"
            except Exception:
                return -108, "invalid enum format in validation rule"
        elif validation == 'bool':
            if param.upper() not in ['ON', 'OFF', '1', '0']:
                return -108, f"expected ON/OFF/1/0, got '{param}'"
        
        return None

    def link_stateful_commands(self):
        """Link SET/QUERY pairs"""
        command_groups = {}
        
        for cmd in self.commands.keys():
            if cmd.startswith('*') or cmd.startswith('SYST:'):
                continue
                
            if '(.+)' in cmd:
                base_name = cmd.replace(' (.+)', '').replace('(.+)', '')
                if base_name not in command_groups:
                    command_groups[base_name] = {}
                command_groups[base_name]['set'] = cmd
                validation = self.validation_rules.get(cmd)
                command_groups[base_name]['validation'] = validation
                
            elif cmd.endswith('?'):
                base_name = cmd[:-1]
                if base_name not in command_groups:
                    command_groups[base_name] = {}
                command_groups[base_name]['query'] = cmd
        
        for base_name, group in command_groups.items():
            if 'set' in group and 'query' in group:
                set_cmd = group['set']
                query_cmd = group['query']
                validation = group.get('validation')
                
                if base_name in self.default_values:
                    default_value = self.default_values[base_name]
                else:
                    original_query_handler = self.commands[query_cmd]
                    try:
                        default_value = original_query_handler() if callable(original_query_handler) else "0"
                        self.default_values[base_name] = default_value
                    except Exception:
                        default_value = "0"
                        self.default_values[base_name] = default_value
                
                self.commands[set_cmd] = self._create_stateful_set(base_name, validation)
                self.commands[query_cmd] = self._create_stateful_query(base_name, default_value)

    def _create_stateful_set(self, base_name, validation=None):
        """Create SET command that stores value"""
        def set_value(*args):
            if args:
                key = f'{base_name}_VALUE'
                
                if validation:
                    error = self._validate_generic(args[0], validation)
                    if error:
                        self.error_queue.push(error[0], error[1])
                        return ''
                
                self.state[key] = args[0]
                return 'OK'
            return ''
        return set_value

    def _create_stateful_query(self, base_name, default_value):
        """Create QUERY command that returns stored or default value"""
        def get_value():
            key = f'{base_name}_VALUE'
            value = self.state.get(key, default_value)
            return str(value)
        return get_value

    def process_command(self, command):
        """Process a SCPI command and return response"""
        self.last_command = (
            command.decode('utf-8', errors='replace') if isinstance(command, bytes) else command
        )
        self.command_count += 1
        
        command = command.strip()
        if not command:
            return ''

        # Byte input is used for binary-safe typed commands. The legacy fallback
        # remains text-only until it is retired under scpi-604.
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

        if isinstance(command, bytes):
            try:
                command = command.decode('utf-8')
            except UnicodeDecodeError:
                self.error_queue.push(-102, "binary data is not valid for a legacy command")
                return ''
        command_upper = command.upper()
        
        # Try exact match first
        if command_upper in self.commands:
            try:
                handler = self.commands[command_upper]
                result = handler()
                if isinstance(result, (BinaryResponse, bytes, bytearray, memoryview)):
                    return result
                return str(result) if result is not None else ''
            except Exception:
                self.error_queue.push(-310, f"command execution failed; {command}")
                return ''
        
        # Try regex matching for parameterized commands
        for pattern, handler in self.commands.items():
            try:
                if '(' in pattern:
                    regex = "(.+)".join(
                        re.escape(literal) for literal in pattern.split("(.+)")
                    )
                    match = re.fullmatch(regex, command_upper)
                    if match:
                        args = match.groups()
                        result = handler(*args)
                        return str(result) if result is not None else ''
            except Exception as e:
                logger.error(f"Error in regex matching for '{command}': {e}")
                continue
        
        # Command not found
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

    def _handle_client(self, client_socket, address):
        """Receive and execute framed messages for the active session."""
        with self._clients_lock:
            self.clients.append(client_socket)

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
        response = self.instrument.queue_command_response(
            message,
            termination=self.transport_config.write_termination,
        )
        command_text = message.decode('utf-8', errors='replace')
        if len(command_text) > 512:
            command_text = f"{command_text[:509]}..."
        error = None
        if self.instrument.error_queue:
            error = self.instrument.error_queue.last_response()

        command_logger.log_command(
            self.instrument.name,
            command_text,
            response or '(no response)',
            error,
        )

        if HAS_FLASK and getattr(self.manager, 'web_dashboard', None):
            timestamp = time.time()
            self.manager.web_dashboard.socketio.emit('command_update', {
                'timestamp': timestamp,
                'time_str': datetime.fromtimestamp(timestamp).strftime('%H:%M:%S'),
                'instrument': self.instrument.name,
                'command': command_text,
                'response': response or '(no response)',
                'error': error,
            })

        if self.instrument.output_queue:
            client_socket.settimeout(self.transport_config.send_timeout)
            client_socket.sendall(self.instrument.read_output())
            client_socket.settimeout(min(0.1, self.transport_config.accept_poll_interval))

class WebDashboard:
    """Flask-based web dashboard for SCPI emulator"""
    
    def __init__(self, emulator_manager, host='0.0.0.0', port=8081):
        if not HAS_FLASK:
            logger.error("Flask not available. Web dashboard disabled.")
            return
            
        self.manager = emulator_manager
        self.host = host
        self.port = port
        
        # Create Flask app
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'scpi_emulator_secret_key'
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")
        
        self._setup_routes()
        self._setup_socketio()
        
    def _setup_routes(self):
        """Setup Flask routes"""
        
        @self.app.route('/')
        def dashboard():
            return render_template('dashboard.html')
        
        @self.app.route('/api/status')
        def api_status():
            """Get system status"""
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
                    'clients': len(server.clients) if server else 0,
                    'commands': instrument.command_count,
                    'errors': len(instrument.error_queue),
                    'state': dict(instrument.state)
                })
            
            return jsonify({
                'instruments': instruments,
                'stats': command_logger.get_stats(),
                'system': {
                    'total_instruments': len(self.manager.instruments),
                    'running_servers': len(self.manager.servers),
                    'timestamp': time.time()
                }
            })
        
        @self.app.route('/api/commands')
        def api_commands():
            """Get recent commands"""
            return jsonify(command_logger.get_recent_entries())
        
        @self.app.route('/api/restart/<instrument_id>', methods=['POST'])
        def api_restart_instrument(instrument_id):
            """Restart a specific instrument"""
            try:
                if instrument_id in self.manager.servers:
                    server = self.manager.servers[instrument_id]
                    server.stop()
                    time.sleep(0.5)
                    
                    if server.start():
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
                self.manager.stop_all_servers()
                return jsonify({'status': 'success', 'message': 'All servers stopped'})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500
        
        @self.app.route('/api/start_all', methods=['POST'])
        def api_start_all():
            """Start all instruments"""
            try:
                if self.manager.start_all_servers():
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
                command = request.json.get('command', '').strip()
                if not command:
                    return jsonify({'status': 'error', 'message': 'No command provided'}), 400
                server = self.manager.servers[instrument_id]
                response = server.instrument.process_command(command)
                error = server.instrument.error_queue.last_response()
                self.manager.web_dashboard.emit_command_update(server.instrument.name, command, response or '(no response)', error)
                return jsonify({'status': 'success', 'message': 'Command sent', 'response': response, 'error': error})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500


    
    def _setup_socketio(self):
        """Setup WebSocket events for real-time updates"""
        
        @self.socketio.on('connect')
        def handle_connect():
            logger.info("Web client connected")
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            logger.info("Web client disconnected")
    
    def emit_command_update(self, instrument_name, command, response, error=None):
        """Emit real-time command update to web clients"""
        if hasattr(self, 'socketio'):
            self.socketio.emit('command_update', {
                'timestamp': time.time(),
                'instrument': instrument_name,
                'command': command,
                'response': response,
                'error': error
            })
    
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


class SCPIEmulatorManager:
    """Manages multiple SCPI instrument emulators with web dashboard"""

    def __init__(self):
        self.instruments = {}
        self.servers = {}
        self.running = False
        self.web_dashboard = None
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("Received shutdown signal, stopping servers...")
        self.stop_all_servers()
        sys.exit(0)

    @staticmethod
    def _instrument_id(name):
        instrument_id = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
        if not instrument_id:
            raise ConfigurationError("equipment name must contain a letter or number")
        return instrument_id

    @staticmethod
    def _validate_rule(rule, row_num):
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
                raise ConfigurationError(
                    f"row {row_num}: range bounds must be numeric"
                ) from error
            if lower > upper:
                raise ConfigurationError(
                    f"row {row_num}: range lower bound exceeds upper bound"
                )
            return
        if rule.startswith('enum:'):
            values = [value.strip() for value in rule.split(':', 1)[1].split(',')]
            if not values or any(not value for value in values):
                raise ConfigurationError(
                    f"row {row_num}: enum validation requires non-empty values"
                )
            return
        raise ConfigurationError(f"row {row_num}: unsupported validation rule '{rule}'")

    def load_from_file(self, file_path, port_start=5025):
        """Load instrument definitions from Excel or CSV file"""
        try:
            file_path_obj = Path(file_path)
            
            if not file_path_obj.exists():
                logger.error(f"File not found: {file_path}")
                return False
            
            # Read data based on file type
            if file_path_obj.suffix.lower() == '.xlsx':
                data = ExcelReader.read_excel_as_csv(file_path)
            elif file_path_obj.suffix.lower() == '.csv':
                data = ExcelReader.read_csv(file_path)
            else:
                logger.error(f"Unsupported file type: {file_path_obj.suffix}")
                return False
            
            if not data:
                logger.error("No data found in file")
                return False
            
            loaded_instruments = {}
            used_ports = set()
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
                    instrument_id = self._instrument_id(equipment_name)
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
                        raise ConfigurationError(
                            f"row {row_num}: port must be between 1 and 65535"
                        )
                    if port in used_ports:
                        raise ConfigurationError(f"row {row_num}: duplicate port {port}")

                    current_instrument = SCPIInstrument(equipment_name, instrument_id)
                    loaded_instruments[instrument_id] = {
                        'instrument': current_instrument,
                        'port': port
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
                    self._validate_rule(validation, row_num)
                    current_instrument.add_command(command, response, validation)
                    current_commands.add(command_key)
                    commands_added += 1

            # Link stateful commands
            for instrument_data in loaded_instruments.values():
                instrument = instrument_data['instrument']
                instrument.link_stateful_commands()

            if loaded_instruments:
                self.instruments = loaded_instruments
                logger.info(f"Successfully loaded {len(loaded_instruments)} instruments with {commands_added} commands")
                return True

            logger.error("No valid instruments found in file")
            return False
        except ConfigurationError as e:
            logger.error(f"Invalid instrument configuration: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error loading file: {e}")
            return False

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
        
        if success_count > 0:
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

    def start_web_dashboard(self, host='0.0.0.0', port=8081):
        """Start the web dashboard"""
        if not HAS_FLASK:
            logger.warning("Flask not available. Cannot start web dashboard.")
            return False
            
        self.web_dashboard = WebDashboard(self, host, port)
        return self.web_dashboard.start()

    def interactive_mode(self):
        """Interactive command-line interface"""
        print("\n SCPI Emulator Manager v2.3 - Interactive Mode")
        print("=" * 60)
        print("Commands:")
        print("  load <file>       - Load instruments from Excel/CSV")
        print("  start             - Start all servers")
        print("  web               - Start web dashboard")
        print("  status            - Show server status")
        print("  stop              - Stop all servers")
        print("  quit              - Exit")
        print("=" * 60)
        
        while True:
            try:
                user_input = input("\nSCPI-MGR> ").strip()
                
                if not user_input:
                    continue
                
                parts = user_input.split()
                command = parts[0].lower()
                
                if command == 'quit':
                    if self.running:
                        self.stop_all_servers()
                    break
                
                elif command == 'load':
                    if len(parts) < 2:
                        print("Usage: load <file>")
                        continue
                    
                    file_path = ' '.join(parts[1:])
                    if self.load_from_file(file_path):
                        print("✅ File loaded successfully!")
                    else:
                        print("❌ Failed to load file")
                
                elif command == 'start':
                    if not self.instruments:
                        print("❌ No instruments loaded. Use 'load <file>' first.")
                        continue
                    
                    if self.start_all_servers():
                        print("✅ All servers started!")
                    else:
                        print("❌ Failed to start servers")
                
                elif command == 'web':
                    if self.start_web_dashboard():
                        print("✅ Web dashboard started at http://localhost:8081")
                    else:
                        print("❌ Failed to start web dashboard")
                
                elif command == 'status':
                    if self.running:
                        print(f"✅ Running {len(self.servers)} servers:")
                        for inst_id, server in self.servers.items():
                            print(f"   {server.instrument.name}: {server.host}:{server.port}")
                        
                        if self.web_dashboard:
                            print("   Web dashboard: http://localhost:8081")
                    else:
                        print("❌ No servers running")
                
                elif command == 'stop':
                    self.stop_all_servers()
                    print("✅ All servers stopped")
                
                else:
                    print(f"❌ Unknown command: {command}")
                    
            except KeyboardInterrupt:
                print("\n👋 Shutting down...")
                if self.running:
                    self.stop_all_servers()
                break
            except EOFError:
                print("\n👋 Goodbye!")
                if self.running:
                    self.stop_all_servers()
                break


def create_example_csv():
    """Create example CSV with validation examples"""
    data = [
        ['Equipment', 'Port', 'Command', 'Response', 'Validation'],
        ['Keysight 34461A DMM', '5555', 'MEAS:VOLT:DC?', '1.234567E+00', ''],
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


def create_dashboard_template():
    """Create the HTML template for the dashboard"""
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    os.makedirs(template_dir, exist_ok=True)
    
    template_path = os.path.join(template_dir, 'dashboard.html')
    
    if not os.path.exists(template_path):
        # Create a simple template - in a real implementation, you'd want the full HTML
        template_content = """
<!DOCTYPE html>
<html>
<head>
    <title>SCPI Emulator Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .status { background: #f0f8ff; padding: 15px; margin: 10px 0; border-radius: 5px; }
        .instrument { background: #f9f9f9; padding: 10px; margin: 5px 0; border-left: 4px solid #4CAF50; }
        .commands { background: #2c3e50; color: white; padding: 15px; font-family: monospace; height: 300px; overflow-y: auto; }
        button { padding: 10px 20px; margin: 5px; background: #4CAF50; color: white; border: none; cursor: pointer; }
        button:hover { background: #45a049; }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.0/socket.io.js"></script>
</head>
<body>
    <h1>🔬 SCPI Emulator Dashboard</h1>
    
    <div class="status" id="status">
        Loading system status...
    </div>
    
    <div>
        <button onclick="startAll()">▶️ Start All</button>
        <button onclick="stopAll()">⏹️ Stop All</button>
        <button onclick="refreshStatus()">🔄 Refresh</button>
    </div>
    
    <h2>📡 Instruments</h2>
    <div id="instruments">
        Loading instruments...
    </div>
    
    <h2>🖥️ Live Commands</h2>
    <div class="commands" id="commands">
        Waiting for commands...
    </div>

    <script>
        const socket = io();
        
        function refreshStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    updateStatus(data);
                });
        }
        
        function updateStatus(data) {
            document.getElementById('status').innerHTML = `
                <strong>System Status:</strong> 
                ${data.system.total_instruments} instruments, 
                ${data.system.running_servers} running, 
                ${data.stats.total_commands} total commands, 
                ${data.stats.errors} errors
            `;
            
            const instrumentsHtml = data.instruments.map(inst => `
                <div class="instrument">
                    <strong>${inst.name}</strong> (Port ${inst.port}) - 
                    Status: ${inst.running ? '🟢 Running' : '🔴 Stopped'} - 
                    Clients: ${inst.clients} - 
                    Commands: ${inst.commands}
                </div>
            `).join('');
            
            document.getElementById('instruments').innerHTML = instrumentsHtml;
        }
        
        function startAll() {
            fetch('/api/start_all', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    refreshStatus();
                });
        }
        
        function stopAll() {
            fetch('/api/stop_all', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    refreshStatus();
                });
        }
        
        socket.on('command_update', function(data) {
            const commands = document.getElementById('commands');
            const newCommand = document.createElement('div');
            newCommand.innerHTML = `[${data.instrument}] ${data.command} → ${data.response}`;
            commands.appendChild(newCommand);
            commands.scrollTop = commands.scrollHeight;
        });
        
        // Refresh status every 5 seconds
        setInterval(refreshStatus, 5000);
        
        // Initial load
        refreshStatus();
    </script>
</body>
</html>
        """
        
        with open(template_path, 'w', encoding='utf-8') as f:
            f.write(template_content)
        
        logger.info(f"Created dashboard template: {template_path}")


def build_parser():
    """Build the command-line parser without starting the emulator."""
    parser = argparse.ArgumentParser(
        description='Stateful SCPI instrument emulator for automation development and testing'
    )

    parser.add_argument('--load', '-l', help='Load instrument definitions (.csv, .xlsx)')
    parser.add_argument('--start', '-s', action='store_true', help='Start TCP servers immediately')
    parser.add_argument('--web', '-w', action='store_true', help='Start web dashboard')
    parser.add_argument('--web-port', type=int, default=8081, help='Web dashboard port (default: 8081)')
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

    print(f"SCPI Instrument Emulator {__version__} (legacy engine 2.3)")
    print("=" * 60)

    if args.create_example:
        create_example_csv()
        return 0

    # Create emulator manager
    manager = SCPIEmulatorManager()
    
    # Load file if provided
    if args.load:
        if not manager.load_from_file(args.load, args.port):
            return 1
        
        # Start servers if requested
        if args.start:
            if not manager.start_all_servers(args.host):
                return 1
        
        # Start web dashboard if requested
        if args.web:
            if not manager.start_web_dashboard('0.0.0.0', args.web_port):
                logger.warning("Failed to start web dashboard")
    
    # Start interactive mode
    if args.interactive or (not args.load and not args.create_example):
        manager.interactive_mode()
    elif args.load and (args.start or args.web):
        print("\n🚀 SCPI Emulator running!")
        if manager.running:
            print(f"📡 Instruments available on ports {args.port}+")
        if args.web:
            print(f"🌐 Web dashboard: http://localhost:{args.web_port}")
        print("\nPress Ctrl+C to stop...")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            manager.stop_all_servers()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
