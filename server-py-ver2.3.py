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
import json
from pathlib import Path
from datetime import datetime
import traceback
from collections import deque
import os

# Flask imports
try:
    from flask import Flask, render_template, jsonify, request, send_from_directory
    from flask_socketio import SocketIO, emit
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    print("Flask not installed. Web dashboard will be disabled.")
    print("Install with: pip install flask flask-socketio")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scpi_emulator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

class ExcelReader:
    """Simple Excel reader using only standard library"""

    @staticmethod
    def read_excel_as_csv(excel_path):
        """Convert Excel to CSV format and read it"""
        try:
            try:
                import openpyxl
            except ImportError:
                logger.error("openpyxl not available. Please convert Excel to CSV format.")
                logger.info("To install openpyxl: pip install openpyxl")
                return []
            
            workbook = openpyxl.load_workbook(excel_path, read_only=True)
            worksheet = workbook.active
            
            headers = []
            for cell in worksheet[1]:
                headers.append(cell.value or '')
            
            data = []
            for row in worksheet.iter_rows(min_row=2, values_only=True):
                row_dict = {}
                for i, value in enumerate(row):
                    if i < len(headers):
                        row_dict[headers[i]] = str(value) if value is not None else ''
                data.append(row_dict)
            
            workbook.close()
            return data
        except Exception as e:
            logger.error(f"Error reading Excel file: {e}")
            return []

    @staticmethod
    def read_csv(csv_path):
        """Read CSV file using standard library"""
        try:
            data = []
            with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
                sample = csvfile.read(1024)
                csvfile.seek(0)
                
                if '\t' in sample:
                    delimiter = '\t'
                elif ';' in sample:
                    delimiter = ';'
                elif ',' in sample:
                    delimiter = ','
                else:
                    delimiter = ','
                
                logger.debug(f"Using delimiter: '{delimiter}'")
                
                reader = csv.DictReader(csvfile, delimiter=delimiter)
                for row_num, row in enumerate(reader, 2):
                    try:
                        clean_row = {}
                        for key, value in row.items():
                            clean_key = key.strip() if key else ''
                            if value is None:
                                clean_value = ''
                            elif isinstance(value, str):
                                clean_value = value.strip()
                            else:
                                clean_value = str(value).strip()
                            clean_row[clean_key] = clean_value
                        data.append(clean_row)
                    except Exception as e:
                        logger.error(f"Error processing row {row_num}: {e}")
                        continue
            
            return data
        
        except Exception as e:
            logger.error(f"Error reading CSV file: {e}")
            return []


class SCPIInstrument:
    """Represents a single SCPI instrument with its command set"""

    def __init__(self, name, instrument_id):
        self.name = name
        self.id = instrument_id
        self.commands = {}
        self.state = {}
        self.error_queue = []
        self.last_command = ""
        self.command_count = 0
        
        # Store validation info separately to survive device clear
        self.validation_rules = {}
        self.default_values = {}
        
        # Add standard IEEE 488.2 commands
        self._add_ieee488_commands()

    def _add_ieee488_commands(self):
        """Add standard IEEE 488.2 mandatory commands"""
        self.commands.update({
            '*CLS': self._clear_status,
            '*ESE': self._event_status_enable,
            '*ESE?': self._event_status_enable_query,
            '*ESR?': self._event_status_register_query,
            '*IDN?': lambda: f"SCPI_Emulator,{self.name},{self.id},2.3.0",
            '*OPC': lambda: '1',
            '*OPC?': lambda: '1',
            '*RST': self._reset,
            '*SRE': self._service_request_enable,
            '*SRE?': self._service_request_enable_query,
            '*STB?': self._status_byte_query,
            '*TST?': self._self_test,
            '*WAI': lambda: '',
            'SYST:ERR?': self._system_error_query,
            'SYST:VERS?': lambda: '1999.0',
        })

    def _clear_status(self):
        self.error_queue.clear()
        self.state.clear()
        return ''

    def visa_device_clear(self):
        """Simulate VISA Device Clear operation"""
        logger.info(f"[VISA-CLR] VISA Device Clear for {self.name}")
        
        self.state.clear()
        self.error_queue.clear()
        self.last_command = ""
        self.command_count = 0
        
        self.link_stateful_commands()

    def _reset(self):
        self.state.clear()
        self.error_queue.clear()
        return ''

    def _event_status_enable(self, value=None):
        if value is not None:
            self.state['ese'] = int(value)
        return ''

    def _event_status_enable_query(self):
        return str(self.state.get('ese', 0))

    def _event_status_register_query(self):
        return str(self.state.get('esr', 0))

    def _service_request_enable(self, value=None):
        if value is not None:
            self.state['sre'] = int(value)
        return ''

    def _service_request_enable_query(self):
        return str(self.state.get('sre', 0))

    def _status_byte_query(self):
        return str(self.state.get('stb', 0))

    def _self_test(self):
        return '0'

    def _system_error_query(self):
        if self.error_queue:
            return self.error_queue.pop(0)
        return '0,"No error"'

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

    def _create_parameterized_response(self, response_template, validation=None):
        """Create a function for parameterized responses"""
        def parameterized_response(*args, template=response_template, val=validation):
            if args and val:
                error = self._validate_generic(args[0], val)
                if error:
                    self.error_queue.append(error)
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
                    return f'-222,"Data out of range; expected {min_val} to {max_val}, got {value}"'
            except ValueError:
                return f'-104,"Data type error; cannot convert \'{param}\' to number"'
        elif validation.startswith('enum:'):
            try:
                _, enum_str = validation.split(':', 1)
                valid_values = [v.strip().upper() for v in enum_str.split(',')]
                if param.upper() not in valid_values:
                    return f'-108,"Parameter not allowed; expected one of {valid_values}, got \'{param}\'"'
            except Exception:
                return f'-108,"Invalid enum format in validation rule"'
        elif validation == 'bool':
            if param.upper() not in ['ON', 'OFF', '1', '0']:
                return f'-108,"Invalid boolean; expected ON/OFF/1/0, got \'{param}\'"'
        
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
                        self.error_queue.append(error)
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
        self.last_command = command
        self.command_count += 1
        
        command = command.strip()
        if not command:
            return ''
        
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
        command_upper = command.upper()
        
        # Try exact match first
        if command_upper in self.commands:
            try:
                handler = self.commands[command_upper]
                result = handler()
                return str(result) if result is not None else ''
            except Exception as e:
                error_msg = f'-113,"Command execution error; {command}"'
                self.error_queue.append(error_msg)
                return ''
        
        # Try regex matching for parameterized commands
        for pattern, handler in self.commands.items():
            try:
                if '(' in pattern:
                    match = re.fullmatch(pattern, command_upper)
                    if match:
                        args = match.groups()
                        result = handler(*args)
                        return str(result) if result is not None else ''
            except Exception as e:
                logger.error(f"Error in regex matching for '{command}': {e}")
                continue
        
        # Command not found
        error_msg = f'-113,"Undefined header; {command}"'
        self.error_queue.append(error_msg)
        return ''


class SCPIServer:
    """TCP server for a single SCPI instrument"""

    def __init__(self, instrument, manager, host='localhost', port=5555):
        self.instrument = instrument
        self.manager = manager  # Store the manager
        self.host = host
        self.port = port
        self.socket = None
        self.running = False
        self.clients = []
        self.thread = None
        
    def start(self):
        """Start the TCP server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)
            self.running = True
            
            self.thread = threading.Thread(target=self._server_loop, daemon=True)
            self.thread.start()
            
            logger.info(f"Started SCPI server for '{self.instrument.name}' on {self.host}:{self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start server for {self.instrument.name}: {e}")
            return False

    def stop(self):
        """Stop the TCP server"""
        self.running = False
        
        for client in self.clients[:]:
            try:
                client.close()
            except:
                pass
        self.clients.clear()
        
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        
        logger.info(f"Stopped SCPI server for '{self.instrument.name}'")

    def _server_loop(self):
        """Main server loop"""
        while self.running:
            try:
                client_socket, address = self.socket.accept()
                logger.info(f"Client connected to {self.instrument.name} from {address}")
                
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, address),
                    daemon=True
                )
                client_thread.start()
                
            except socket.error:
                if self.running:
                    logger.error(f"Server socket error for {self.instrument.name}")
                break

    def _handle_client(self, client_socket, address):
        """Handle individual client connection"""
        self.clients.append(client_socket)
        
        try:
            # Simulate VISA device clear
            self.instrument.visa_device_clear()
            
            buffer = b''
            last_activity = time.time()
            
            while self.running:
                try:
                    client_socket.settimeout(0.1)
                    
                    try:
                        data = client_socket.recv(1024)
                        if not data:
                            break
                        
                        buffer += data
                        last_activity = time.time()
                        
                    except socket.timeout:
                        current_time = time.time()
                        if buffer and (current_time - last_activity) > 0.3:
                            try:
                                command = buffer.decode('utf-8').strip()
                                if command:
                                    response = self.instrument.process_command(command)
                                    
                                    # Log to web dashboard
                                    error = None
                                    if self.instrument.error_queue:
                                        error = self.instrument.error_queue[-1]
                                    
                                    command_logger.log_command(
                                        self.instrument.name, 
                                        command, 
                                        response or '(no response)', 
                                        error
                                    )
                                    
                                    # Emit to web clients
                                    if HAS_FLASK and hasattr(self.manager, 'web_dashboard') and self.manager.web_dashboard:
                                        self.manager.web_dashboard.socketio.emit('command_update', {
                                            'timestamp': time.time(),
                                            'time_str': datetime.fromtimestamp(time.time()).strftime('%H:%M:%S'),
                                            'instrument': self.instrument.name,
                                            'command': command,
                                            'response': response or '(no response)',
                                            'error': error
                                        })
                                    
                                    if response:
                                        response_msg = response + '\n'
                                        client_socket.sendall(response_msg.encode('utf-8'))
                                    
                                buffer = b''
                                last_activity = current_time
                            except UnicodeDecodeError:
                                buffer = b''
                        continue
                    
                    # Check for terminated commands
                    while True:
                        terminator_pos = -1
                        terminator_len = 0
                        
                        for term, length in [(b'\r\n', 2), (b'\n', 1), (b'\r', 1)]:
                            pos = buffer.find(term)
                            if pos != -1:
                                terminator_pos = pos
                                terminator_len = length
                                break
                        
                        if terminator_pos == -1:
                            break
                        
                        command_bytes = buffer[:terminator_pos]
                        buffer = buffer[terminator_pos + terminator_len:]
                        
                        try:
                            command = command_bytes.decode('utf-8').strip()
                            if command:
                                response = self.instrument.process_command(command)
                                
                                # Log to web dashboard
                                error = None
                                if self.instrument.error_queue:
                                    error = self.instrument.error_queue[-1]
                                
                                command_logger.log_command(
                                    self.instrument.name, 
                                    command, 
                                    response or '(no response)', 
                                    error
                                )
                                
                                # Emit to web clients
                                if HAS_FLASK and hasattr(self.manager, 'web_dashboard') and self.manager.web_dashboard:
                                    self.manager.web_dashboard.socketio.emit('command_update', {
                                        'timestamp': time.time(),
                                        'time_str': datetime.fromtimestamp(time.time()).strftime('%H:%M:%S'),
                                        'instrument': self.instrument.name,
                                        'command': command,
                                        'response': response or '(no response)',
                                        'error': error
                                    })
                                
                                if response:
                                    response_msg = response + '\n'
                                    client_socket.sendall(response_msg.encode('utf-8'))
                                
                        except UnicodeDecodeError:
                            continue
                    
                except ConnectionResetError:
                    break
                except Exception as e:
                    logger.error(f"Client handling error: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Client {address} error: {e}")
        finally:
            client_socket.close()
            if client_socket in self.clients:
                self.clients.remove(client_socket)


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
                error = server.instrument.error_queue[-1] if server.instrument.error_queue else None
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

    def load_from_file(self, file_path, port_start=5555):
        """Load instrument definitions from Excel or CSV file"""
        try:
            file_path_obj = Path(file_path)
            
            if not file_path_obj.exists():
                logger.error(f"File not found: {file_path}")
                return False
            
            # Read data based on file type
            if file_path_obj.suffix.lower() in ['.xlsx', '.xls']:
                data = ExcelReader.read_excel_as_csv(file_path)
            elif file_path_obj.suffix.lower() == '.csv':
                data = ExcelReader.read_csv(file_path)
            else:
                logger.error(f"Unsupported file type: {file_path_obj.suffix}")
                return False
            
            if not data:
                logger.error("No data found in file")
                return False
            
            # Validate required columns
            required_cols = ['Equipment', 'Command', 'Response']
            if not all(col in data[0].keys() for col in required_cols):
                available_cols = list(data[0].keys())
                logger.error(f"File missing required columns: {required_cols}")
                logger.error(f"Available columns: {available_cols}")
                return False
            
            has_port_col = 'Port' in data[0].keys()
            has_validation_col = 'Validation' in data[0].keys()
            
            self.instruments.clear()
            current_instrument = None
            current_port = port_start
            commands_added = 0
            
            logger.info(f"Processing {len(data)} rows from {file_path}")
            
            for row_num, row in enumerate(data, 1):
                equipment_name = row.get('Equipment', '').strip()
                if equipment_name:
                    instrument_id = equipment_name.lower().replace(' ', '_').replace('-', '_')
                    
                    if has_port_col and row.get('Port', '').strip():
                        try:
                            port = int(row['Port'])
                        except ValueError:
                            port = current_port
                            current_port += 1
                    else:
                        port = current_port
                        current_port += 1
                    
                    current_instrument = SCPIInstrument(equipment_name, instrument_id)
                    self.instruments[instrument_id] = {
                        'instrument': current_instrument,
                        'port': port
                    }
                    
                    logger.info(f"Row {row_num}: Created instrument: {equipment_name} (Port: {port})")

                if (current_instrument and 
                    row.get('Command', '').strip() and row.get('Response', '').strip()):
                    
                    command = row['Command'].strip()
                    response = row['Response'].strip()
                    validation = row.get('Validation','').strip() if has_validation_col else None

                    current_instrument.add_command(command, response, validation)
                    commands_added += 1
            
            # Link stateful commands
            for instrument_id, instrument_data in self.instruments.items():
                instrument = instrument_data['instrument']
                instrument.link_stateful_commands()
            
            if self.instruments:
                logger.info(f"Successfully loaded {len(self.instruments)} instruments with {commands_added} commands")
                return True
            else:
                logger.error("No valid instruments found in file")
                return False
                
        except Exception as e:
            logger.error(f"Error loading file: {e}")
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
                            print(f"   Web dashboard: http://localhost:8081")
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


def main():
    print("SCPI Equipment Emulator - VERSION 2.3")
    print("🌐 NEW: Web Dashboard with real-time monitoring!")
    print("=" * 60)
    create_dashboard_template()
    parser = argparse.ArgumentParser(
        description='SCPI Equipment Emulator v2.3 - LabVIEW Compatible with Web Dashboard'
    )
    
    parser.add_argument('--load', '-l', help='Load instrument definitions (.csv, .xlsx, .xls)')
    parser.add_argument('--start', '-s', action='store_true', help='Start TCP servers immediately')
    parser.add_argument('--web', '-w', action='store_true', help='Start web dashboard')
    parser.add_argument('--web-port', type=int, default=8081, help='Web dashboard port (default: 8081)')
    parser.add_argument('--port', '-p', type=int, default=5555, help='Starting port for instruments (default: 5555)')
    parser.add_argument('--host', default='localhost', help='Server host (default: localhost)')
    parser.add_argument('--create-example', action='store_true', help='Create example CSV file')
    parser.add_argument('--interactive', '-i', action='store_true', help='Start interactive mode')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.create_example:
        create_example_csv()
        return
    
    # Create dashboard template
    create_dashboard_template()
    
    # Create emulator manager
    manager = SCPIEmulatorManager()
    
    # Load file if provided
    if args.load:
        if not manager.load_from_file(args.load, args.port):
            sys.exit(1)
        
        # Start servers if requested
        if args.start:
            if not manager.start_all_servers(args.host):
                sys.exit(1)
        
        # Start web dashboard if requested
        if args.web:
            if not manager.start_web_dashboard('0.0.0.0', args.web_port):
                logger.warning("Failed to start web dashboard")
    
    # Start interactive mode
    if args.interactive or (not args.load and not args.create_example):
        manager.interactive_mode()
    elif args.load and (args.start or args.web):
        print(f"\n🚀 SCPI Emulator running!")
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

if __name__ == "__main__":
    main()