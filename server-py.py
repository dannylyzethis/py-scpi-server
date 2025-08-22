#!/usr/bin/env python3
"""
SCPI Equipment Emulator - VERSION 2.1
FIXED: Welcome message removed for VISA compatibility
FIXING: Range validation in stateful commands

Recent Changes:
- v2.1: Adding version tracking, fixing validation extraction
- v2.0: Removed welcome message (MAJOR FIX for NI-MAX compatibility)
- v1.x: Lambda closures, Unicode fixes, VISA device clear simulation

Complete SCPI Equipment Emulator - LabVIEW Compatible
Compatible with LabVIEW VISA, NI-MAX, and standard SCPI clients
Supports multiple instruments on different ports from Excel/CSV definitions
NO EXTERNAL DEPENDENCIES - Pure Python 3.6+
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
import traceback

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

class ExcelReader:
    """Simple Excel reader using only standard library"""

    @staticmethod
    def read_excel_as_csv(excel_path):
        """
        Convert Excel to CSV format and read it
        Note: This requires openpyxl for Excel support
        """
        try:
            # Try to import openpyxl if available (optional dependency)
            try:
                import openpyxl
            except ImportError:
                logger.error("openpyxl not available. Please convert Excel to CSV format.")
                logger.info("To install openpyxl: pip install openpyxl")
                return []
            
            workbook = openpyxl.load_workbook(excel_path, read_only=True)
            worksheet = workbook.active
            
            # Get header row
            headers = []
            for cell in worksheet[1]:
                headers.append(cell.value or '')
            
            # Read data rows
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
                # Try to detect delimiter
                sample = csvfile.read(1024)
                csvfile.seek(0)
                
                # Simple delimiter detection
                if '\t' in sample:
                    delimiter = '\t'
                elif ';' in sample:
                    delimiter = ';'
                elif ',' in sample:
                    delimiter = ','
                else:
                    delimiter = ','  # Default to comma
                
                logger.debug(f"Using delimiter: '{delimiter}'")
                
                reader = csv.DictReader(csvfile, delimiter=delimiter)
                for row_num, row in enumerate(reader, 2):  # Start at 2 for header
                    try:
                        # Clean up the row data
                        clean_row = {}
                        for key, value in row.items():
                            clean_key = key.strip() if key else ''
                            # Handle case where value might be None or not a string
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
                        logger.error(f"Row data: {row}")
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
        self.state = {}  # For stateful instruments
        self.error_queue = []
        self.last_command = ""
        self.command_count = 0
        
        # Add standard IEEE 488.2 commands
        self._add_ieee488_commands()

    def _add_ieee488_commands(self):
        """Add standard IEEE 488.2 mandatory commands"""
        self.commands.update({
            '*CLS': self._clear_status,
            '*ESE': self._event_status_enable,
            '*ESE?': self._event_status_enable_query,
            '*ESR?': self._event_status_register_query,
            '*IDN?': lambda: f"SCPI_Emulator,{self.name},{self.id},1.0.0",
            '*OPC': lambda: '1',
            '*OPC?': lambda: '1',
            '*RST': self._reset,
            '*SRE': self._service_request_enable,
            '*SRE?': self._service_request_enable_query,
            '*STB?': self._status_byte_query,
            '*TST?': self._self_test,
            '*WAI': lambda: '',
            'SYST:ERR?': self._system_error_query,
            'SYST:VERS?': lambda: '1999.0',  # SCPI version
        })

    def _clear_status(self):
        logger.info(f"[CLEAR] _clear_status() called - Before: state={dict(self.state)}, errors={len(self.error_queue)}")
        self.error_queue.clear()
        self.state.clear()
        logger.info(f"[CLEAR] _clear_status() called - After: state={dict(self.state)}, errors={len(self.error_queue)}")
        return ''

    def visa_device_clear(self):
        """Simulate VISA Device Clear operation - more comprehensive than *CLS"""
        logger.info(f"[VISA-CLR] VISA Device Clear - Before: state={dict(self.state)}, errors={len(self.error_queue)}")
        
        # Clear all state
        self.state.clear()
        self.error_queue.clear()
        
        # Reset command counters and history
        self.last_command = ""
        self.command_count = 0
        
        # CRITICAL: Re-establish stateful commands with fresh default values
        # This ensures the closures capture clean state
        self.link_stateful_commands()
        
        logger.info(f"[VISA-CLR] VISA Device Clear - After: state={dict(self.state)}, errors={len(self.error_queue)}")
        logger.info(f"[VISA-CLR] VISA Device Clear completed successfully")

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
        return '0'  # 0 = passed, 1 = failed

    def _system_error_query(self):
        if self.error_queue:
            return self.error_queue.pop(0)
        return '0,"No error"'

    def add_command(self, command, response, validation=None):
        """Add a command-response pair - FIXED VERSION"""
        command = command.strip().upper()
        
        logger.debug(f"Adding command '{command}' -> '{response}' for {self.name}")
        
        # Handle parameterized commands (convert to regex)
        if '(.+)' in command or '{value}' in command:
            # Create regex pattern
            pattern = command.replace('{value}', r'(.+)')
            # Don't double-replace (.+) - it's already a regex pattern
            if '(.+)' not in pattern:
                pattern = pattern.replace('(.+)', r'(.+)')
            
            logger.debug(f"Created parameterized pattern: '{pattern}'")
            self.commands[pattern] = self._create_parameterized_response(response, validation)
        else:
            # FIXED: Capture response by value to avoid closure issues
            self.commands[command] = lambda resp=response: str(resp)
            logger.debug(f"Added simple command: '{command}'")

    def _create_parameterized_response(self, response_template, validation=None):
        """Create a function for parameterized responses - ENHANCED VALIDATION DEBUGGING"""
        # FIXED: Capture response_template by value
        def parameterized_response(*args, template=response_template, val=validation):
            logger.info(f"[PARAM] Parameterized command called with args: {args}")
            logger.info(f"[PARAM] Template: '{template}'")
            logger.info(f"[PARAM] Validation: '{val}'")
            
            if args and val:
                logger.info(f"[VALIDATION] Starting validation of '{args[0]}' against '{val}'")
                error = self._validate_generic(args[0], val)
                if error:
                    self.error_queue.append(error)
                    logger.warning(f"[VALIDATION] FAILED for {args[0]}: {error}")
                    logger.info(f"[VALIDATION] Error added to queue. Queue now: {self.error_queue}")
                    return ''  # Return empty string on validation failure
                else:
                    logger.info(f"[VALIDATION] PASSED for {args[0]}")
            
            response = template
            for i, arg in enumerate(args, 1):
                response = response.replace(f'{{param{i}}}', str(arg))
                response = response.replace('{value}', str(arg))
            
            logger.info(f"[PARAM] Final response: '{response}'")
            return response
        
        # Store validation as an attribute so it can be retrieved later
        parameterized_response._validation = validation
        return parameterized_response
    
    def _validate_generic(self, param, validation):
        """Generic validation - IMPROVED WITH BETTER DEBUGGING"""
        if not validation:
            return None
        
        logger.debug(f"Validating '{param}' against '{validation}'")
        
        if validation.startswith('range:'):
            try:
                _, range_str = validation.split(':', 1)
                min_val, max_val = map(float, range_str.split(','))
                value = float(param)
                logger.debug(f"Range check: {min_val} <= {value} <= {max_val}")
                if not (min_val <= value <= max_val):
                    error_msg = f'-222,"Data out of range; expected {min_val} to {max_val}, got {value}"'
                    logger.warning(f"Range validation failed: {error_msg}")
                    return error_msg
            except ValueError as e:
                error_msg = f'-104,"Data type error; cannot convert \'{param}\' to number"'
                logger.warning(f"Range validation error: {error_msg}")
                return error_msg
        elif validation.startswith('enum:'):
            try:
                _, enum_str = validation.split(':', 1)
                valid_values = [v.strip().upper() for v in enum_str.split(',')]
                param_upper = param.upper()
                logger.debug(f"Enum check: '{param_upper}' in {valid_values}")
                if param_upper not in valid_values:
                    error_msg = f'-108,"Parameter not allowed; expected one of {valid_values}, got \'{param}\'"'
                    logger.warning(f"Enum validation failed: {error_msg}")
                    return error_msg
            except Exception as e:
                error_msg = f'-108,"Invalid enum format in validation rule"'
                logger.warning(f"Enum validation error: {error_msg}")
                return error_msg
        elif validation == 'bool':
            param_upper = param.upper()
            logger.debug(f"Bool check: '{param_upper}' in ['ON', 'OFF', '1', '0']")
            if param_upper not in ['ON', 'OFF', '1', '0']:
                error_msg = f'-108,"Invalid boolean; expected ON/OFF/1/0, got \'{param}\'"'
                logger.warning(f"Bool validation failed: {error_msg}")
                return error_msg
        
        logger.debug(f"Validation passed for '{param}'")
        return None

    def link_stateful_commands(self):
        """After all commands are loaded, link SET/QUERY pairs - PRESERVE VALIDATION"""
        logger.info(f"[LINK] Starting link_stateful_commands for {self.name} (VERSION 2.1)")
        command_groups = {}
        
        # Group commands by base name and preserve validation info
        for cmd in self.commands.keys():
            if cmd.startswith('*') or cmd.startswith('SYST:'):
                continue  # Skip IEEE commands
                
            if '(.+)' in cmd:
                # This is a SET command like "FREQ (.+)"
                base_name = cmd.replace(' (.+)', '').replace('(.+)', '')
                if base_name not in command_groups:
                    command_groups[base_name] = {}
                command_groups[base_name]['set'] = cmd
                
                # Extract validation from the original handler if it exists
                original_handler = self.commands[cmd]
                validation = getattr(original_handler, '_validation', None)
                logger.info(f"[LINK] Command '{cmd}' handler type: {type(original_handler)}")
                logger.info(f"[LINK] Command '{cmd}' validation extracted: '{validation}'")
                command_groups[base_name]['validation'] = validation
                
            elif cmd.endswith('?'):
                # This is a QUERY command like "FREQ?"
                base_name = cmd[:-1]  # Remove the ?
                if base_name not in command_groups:
                    command_groups[base_name] = {}
                command_groups[base_name]['query'] = cmd
        
        # Convert paired commands to stateful
        for base_name, group in command_groups.items():
            if 'set' in group and 'query' in group:
                set_cmd = group['set']
                query_cmd = group['query']
                validation = group.get('validation')
                
                logger.info(f"[LINK] Processing stateful pair: {set_cmd} <-> {query_cmd}")
                logger.info(f"[LINK] Validation for {base_name}: '{validation}'")
                
                # Get the original query response as default value
                original_query_handler = self.commands[query_cmd]
                try:
                    default_value = original_query_handler() if callable(original_query_handler) else "0"
                    logger.info(f"[LINK] LINKING {set_cmd} <-> {query_cmd}, default: '{default_value}', validation: '{validation}'")
                except Exception as e:
                    default_value = "0"
                    logger.warning(f"[LINK] Error getting default for {query_cmd}: {e}, using '0'")
                
                # Replace with stateful versions that preserve validation
                self.commands[set_cmd] = self._create_stateful_set(base_name, validation)
                self.commands[query_cmd] = self._create_stateful_query(base_name, default_value)
                
                logger.info(f"[LINK] LINKED {set_cmd} <-> {query_cmd} (default: {default_value}) (validation: {validation})")
        
        logger.info(f"[LINK] Finished link_stateful_commands for {self.name}")

    def _create_stateful_set(self, base_name, validation=None):
        """Create SET command that stores value - WITH VALIDATION SUPPORT"""
        logger.info(f"[STATEFUL-CREATE] Creating stateful SET for {base_name} with validation: '{validation}'")
        
        def set_value(*args):
            if args:
                key = f'{base_name}_VALUE'
                old_value = self.state.get(key, 'NOT_SET')
                
                # VALIDATE THE INPUT FIRST
                if validation:
                    logger.info(f"[STATEFUL-VAL] Validating '{args[0]}' against '{validation}'")
                    error = self._validate_generic(args[0], validation)
                    if error:
                        self.error_queue.append(error)
                        logger.warning(f"[STATEFUL-VAL] VALIDATION FAILED for {args[0]}: {error}")
                        logger.info(f"[STATEFUL-VAL] Error added to queue. Queue now: {self.error_queue}")
                        return ''  # Return empty string on validation failure
                    else:
                        logger.info(f"[STATEFUL-VAL] VALIDATION PASSED for {args[0]}")
                else:
                    logger.info(f"[STATEFUL-VAL] NO VALIDATION for {base_name} (validation='{validation}')")
                
                # Store the value if validation passed (or no validation)
                self.state[key] = args[0]
                logger.info(f"[SET] STATEFUL SET {base_name}: {old_value} -> {args[0]} (key: {key})")
                logger.info(f"[SET] STATE NOW: {dict(self.state)}")
                return 'OK'
            return ''
        return set_value

    def _create_stateful_query(self, base_name, default_value):
        """Create QUERY command that returns stored or default value"""
        def get_value():
            key = f'{base_name}_VALUE'
            value = self.state.get(key, default_value)
            is_stored = key in self.state
            logger.info(f"[GET] STATEFUL GET {base_name}:")
            logger.info(f"[GET]   - key: {key}")
            logger.info(f"[GET]   - stored: {is_stored}")
            logger.info(f"[GET]   - value: {value}")
            logger.info(f"[GET]   - default: {default_value}")
            logger.info(f"[GET]   - self.state: {dict(self.state)}")
            logger.info(f"[GET]   - self ID: {id(self)}")
            return str(value)
        return get_value

    def process_command(self, command):
        """Process a SCPI command and return response - ENHANCED DEBUGGING"""
        self.last_command = command
        self.command_count += 1
        
        command = command.strip()
        if not command:
            return ''
        
        logger.info(f"COMMAND #{self.command_count}: Processing '{command}'")
        
        # Handle command chains (separated by semicolons)
        if ';' in command:
            responses = []
            for cmd in command.split(';'):
                response = self._process_single_command(cmd.strip())
                logger.info(f"CHAIN RESPONSE: '{cmd.strip()}' -> '{response}'")
                if response:
                    responses.append(response)
            final_response = ';'.join(responses) if responses else ''
            logger.info(f"FINAL CHAIN RESPONSE: '{final_response}'")
            return final_response
        
        response = self._process_single_command(command)
        logger.info(f"FINAL RESPONSE: '{command}' -> '{response}'")
        return response

    def _process_single_command(self, command):
        """Process a single SCPI command - ENHANCED DEBUGGING"""
        command_upper = command.upper()
        
        logger.debug(f"Processing command: '{command_upper}'")
        logger.debug(f"Available commands: {list(self.commands.keys())}")
        
        # Try exact match first
        if command_upper in self.commands:
            try:
                handler = self.commands[command_upper]
                result = handler()
                logger.debug(f"Exact match '{command_upper}' -> '{result}'")
                return str(result) if result is not None else ''
            except Exception as e:
                logger.error(f"Error executing exact match command '{command}': {e}")
                self.error_queue.append(f'-113,"Command execution error; {command}"')
                return ''
        
        # Try regex matching for parameterized commands
        matched = False
        for pattern, handler in self.commands.items():
            try:
                if '(' in pattern:  # Only try regex on patterns with groups
                    logger.debug(f"Trying pattern: '{pattern}' against '{command_upper}'")
                    match = re.fullmatch(pattern, command_upper)
                    if match:
                        matched = True
                        args = match.groups()
                        logger.debug(f"Pattern MATCHED! '{pattern}' with args {args}")
                        result = handler(*args)
                        logger.debug(f"Handler returned: '{result}'")
                        return str(result) if result is not None else ''
            except Exception as e:
                logger.error(f"Error in regex matching for '{command}' against pattern '{pattern}': {e}")
                logger.error(f"Exception details: {type(e).__name__}: {e}")
                continue
        
        if not matched:
            logger.warning(f"No pattern matched for command: '{command_upper}'")
        
        # Command not found
        self.error_queue.append(f'-113,"Undefined header; {command}"')
        return ''


class SCPIServer:
    """TCP server for a single SCPI instrument"""

    def __init__(self, instrument, host='localhost', port=5555):
        self.instrument = instrument
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
        
        # Close all client connections
        for client in self.clients[:]:
            try:
                client.close()
            except:
                pass
        self.clients.clear()
        
        # Close server socket
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
        """Handle individual client connection - NO WELCOME MESSAGE FOR VISA COMPATIBILITY"""
        self.clients.append(client_socket)
        
        try:
            # NO WELCOME MESSAGE - NI-MAX expects clean communication!
            logger.info(f"[CONN] Client connected from {address} - NO welcome message sent")
            
            # DETAILED INSTRUMENT DEBUGGING
            logger.info(f"[DEBUG] Instrument ID: {id(self.instrument)}")
            logger.info(f"[DEBUG] Instrument Name: {self.instrument.name}")
            logger.info(f"[DEBUG] State ID: {id(self.instrument.state)}")
            logger.info(f"[STATE] STATE BEFORE VISA DEVICE CLEAR: {dict(self.instrument.state)}")
            
            # SIMULATE VISA DEVICE CLEAR (like NI-MAX does)
            logger.info(f"[VISA-CLR] AUTO VISA DEVICE CLEAR starting for {address}")
            self.instrument.visa_device_clear()  # This is the key change!
            
            # Show state AFTER VISA device clear
            logger.info(f"[STATE] STATE AFTER VISA DEVICE CLEAR: {dict(self.instrument.state)}")
            
            buffer = b''
            last_activity = time.time()
            
            while self.running:
                try:
                    client_socket.settimeout(0.1)  # Short timeout for responsiveness
                    
                    try:
                        data = client_socket.recv(1024)
                        if not data:
                            break
                        
                        buffer += data
                        last_activity = time.time()
                        logger.debug(f"Received: {repr(data)}, Buffer: {repr(buffer)}")
                        
                    except socket.timeout:
                        # Check if we should process a non-terminated command
                        current_time = time.time()
                        if buffer and (current_time - last_activity) > 0.3:  # 300ms since last data
                            # Process as non-terminated command
                            try:
                                command = buffer.decode('utf-8').strip()
                                if command:
                                    logger.info(f"[CMD] [{address[0]}:{address[1]}] COMMAND: {command} (timeout)")
                                    logger.info(f"[PRE-CMD] State before command: {dict(self.instrument.state)}")
                                    response = self.instrument.process_command(command)
                                    logger.info(f"[POST-CMD] State after command: {dict(self.instrument.state)}")
                                    logger.info(f"[RSP] [{address[0]}:{address[1]}] RESPONSE: '{response}'")
                                    
                                    if response:
                                        response_msg = response + '\n'
                                        logger.info(f"[SEND] [{address[0]}:{address[1]}] SENDING: {repr(response_msg)}")
                                        client_socket.sendall(response_msg.encode('utf-8'))
                                        logger.info(f"[SENT] [{address[0]}:{address[1]}] SENT: '{response}'")
                                    else:
                                        logger.info(f"[NORSP] [{address[0]}:{address[1]}] NO RESPONSE TO SEND")
                                    
                                buffer = b''  # CLEAR BUFFER AFTER PROCESSING
                                last_activity = current_time
                            except UnicodeDecodeError:
                                logger.error("Unicode decode error, clearing buffer")
                                buffer = b''
                        continue
                    
                    # Check for terminated commands first (higher priority)
                    while True:
                        terminator_pos = -1
                        terminator_len = 0
                        
                        # Find first terminator
                        for term, length in [(b'\r\n', 2), (b'\n', 1), (b'\r', 1)]:
                            pos = buffer.find(term)
                            if pos != -1:
                                terminator_pos = pos
                                terminator_len = length
                                break
                        
                        if terminator_pos == -1:
                            break  # No terminator found
                        
                        # Extract command up to terminator
                        command_bytes = buffer[:terminator_pos]
                        buffer = buffer[terminator_pos + terminator_len:]  # Remove processed part
                        
                        try:
                            command = command_bytes.decode('utf-8').strip()
                            if command:
                                logger.info(f"[CMD] [{address[0]}:{address[1]}] COMMAND: {command} (terminated)")
                                logger.info(f"[PRE-CMD] State before command: {dict(self.instrument.state)}")
                                response = self.instrument.process_command(command)
                                logger.info(f"[POST-CMD] State after command: {dict(self.instrument.state)}")
                                logger.info(f"[RSP] [{address[0]}:{address[1]}] RESPONSE: '{response}'")
                                
                                if response:
                                    response_msg = response + '\n'
                                    logger.info(f"[SEND] [{address[0]}:{address[1]}] SENDING: {repr(response_msg)}")
                                    client_socket.sendall(response_msg.encode('utf-8'))
                                    logger.info(f"[SENT] [{address[0]}:{address[1]}] SENT: '{response}'")
                                else:
                                    logger.info(f"[NORSP] [{address[0]}:{address[1]}] NO RESPONSE TO SEND")
                                
                        except UnicodeDecodeError:
                            logger.error("Unicode decode error in terminated command")
                            continue
                    
                except ConnectionResetError:
                    logger.info(f"Connection reset by client {address}")
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
            logger.info(f"[CONN] Client {address} disconnected from {self.instrument.name}")


class SCPIEmulatorManager:
    """Manages multiple SCPI instrument emulators"""

    def __init__(self):
        self.instruments = {}
        self.servers = {}
        self.running = False
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("Received shutdown signal, stopping servers...")
        self.stop_all_servers()
        sys.exit(0)

    def load_from_file(self, file_path, port_start=5555):
        """Load instrument definitions from Excel or CSV file - IMPROVED VERSION"""
        try:
            file_path_obj = Path(file_path)
            
            if not file_path_obj.exists():
                logger.error(f"File not found: {file_path}")
                return False
            
            # Determine file type and read data
            if file_path_obj.suffix.lower() in ['.xlsx', '.xls']:
                data = ExcelReader.read_excel_as_csv(file_path)
            elif file_path_obj.suffix.lower() == '.csv':
                data = ExcelReader.read_csv(file_path)
            else:
                logger.error(f"Unsupported file type: {file_path_obj.suffix}")
                logger.info("Supported formats: .xlsx, .xls, .csv")
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
            
            # Optional port column
            has_port_col = 'Port' in data[0].keys()
            has_validation_col = 'Validation' in data[0].keys()
            
            self.instruments.clear()
            current_instrument = None
            current_port = port_start
            commands_added = 0
            
            logger.info(f"Processing {len(data)} rows from {file_path}")
            
            for row_num, row in enumerate(data, 1):
                # Check for new equipment
                equipment_name = row.get('Equipment', '').strip()
                if equipment_name:  # Only when Equipment field has a value
                    instrument_id = equipment_name.lower().replace(' ', '_').replace('-', '_')
                    
                    # Get port for this instrument
                    if has_port_col and row.get('Port', '').strip():
                        try:
                            port = int(row['Port'])
                        except ValueError:
                            port = current_port
                            current_port += 1
                    else:
                        port = current_port
                        current_port += 1
                    
                    # Create new instrument
                    current_instrument = SCPIInstrument(equipment_name, instrument_id)
                    self.instruments[instrument_id] = {
                        'instrument': current_instrument,
                        'port': port
                    }
                    
                    logger.info(f"Row {row_num}: Created instrument: {equipment_name} (Port: {port})")

                # Add command to current instrument (whether new or existing)
                if (current_instrument and 
                    row.get('Command', '').strip() and row.get('Response', '').strip()):
                    
                    command = row['Command'].strip()
                    response = row['Response'].strip()
                    validation = row.get('Validation','').strip() if has_validation_col else None

                    logger.debug(f"Row {row_num}: Adding command '{command}' to {current_instrument.name}")
                    current_instrument.add_command(command, response, validation)
                    commands_added += 1
                elif not current_instrument:
                    logger.warning(f"Row {row_num}: No current instrument to add command to")
                elif not row.get('Command', '').strip():
                    logger.debug(f"Row {row_num}: Skipping row with empty command")
            
            # Link stateful commands for all instruments
            for instrument_id, instrument_data in self.instruments.items():
                instrument = instrument_data['instrument']
                instrument.link_stateful_commands()
            
            if self.instruments:
                logger.info(f"Successfully loaded {len(self.instruments)} instruments with {commands_added} commands from {file_path}")
                self._log_instrument_summary()
                return True
            else:
                logger.error("No valid instruments found in file")
                return False
                
        except Exception as e:
            logger.error(f"Error loading file: {e}")
            logger.error(traceback.format_exc())
            return False

    def _log_instrument_summary(self):
        """Log summary of loaded instruments"""
        logger.info("\n" + "="*60)
        logger.info("LOADED INSTRUMENTS SUMMARY")
        logger.info("="*60)
        
        for inst_id, inst_data in self.instruments.items():
            instrument = inst_data['instrument']
            port = inst_data['port']
            custom_cmd_count = len([k for k in instrument.commands.keys() if not k.startswith('*') and not k.startswith('SYST:')])
            total_cmd_count = len(instrument.commands)
            
            logger.info(f"INST: {instrument.name}")
            logger.info(f"   ID: {inst_id}")
            logger.info(f"   Port: {port}")
            logger.info(f"   Commands: {custom_cmd_count} custom + {total_cmd_count - custom_cmd_count} IEEE488.2 = {total_cmd_count} total")
            logger.info(f"   LabVIEW VISA: TCPIP0::localhost::{port}::INSTR")
            logger.info(f"   Test with: telnet localhost {port}")
        
        logger.info("="*60)

    def start_all_servers(self, host='localhost'):
        """Start TCP servers for all instruments"""
        success_count = 0
        
        for inst_id, inst_data in self.instruments.items():
            instrument = inst_data['instrument']
            port = inst_data['port']
            
            server = SCPIServer(instrument, host, port)
            if server.start():
                self.servers[inst_id] = server
                success_count += 1
            else:
                logger.error(f"Failed to start server for {instrument.name}")
        
        if success_count > 0:
            self.running = True
            logger.info(f"\n Started {success_count} SCPI servers (LabVIEW Compatible)")
            logger.info("Connect from LabVIEW using VISA resource strings:")
            
            for inst_id, server in self.servers.items():
                instrument = server.instrument
                logger.info(f"   {instrument.name}: TCPIP0::localhost::{server.port}::INSTR")
            
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

    def list_instruments(self):
        """List all loaded instruments"""
        if not self.instruments:
            print("No instruments loaded")
            return
        
        print("\n Available Instruments:")
        print("-" * 80)
        
        for inst_id, inst_data in self.instruments.items():
            instrument = inst_data['instrument']
            port = inst_data['port']
            cmd_count = len([k for k in instrument.commands.keys() if not k.startswith('*')])
            
            print(f" {instrument.name}")
            print(f"   ID: {inst_id}")
            print(f"   Port: {port}")
            print(f"   Commands: {cmd_count} custom")
            print(f"   VISA: TCPIP0::localhost::{port}::INSTR")
            print()

    def show_commands(self, instrument_id):
        """Show commands for a specific instrument"""
        if instrument_id not in self.instruments:
            print(f"Instrument '{instrument_id}' not found")
            return
        
        instrument = self.instruments[instrument_id]['instrument']
        port = self.instruments[instrument_id]['port']
        
        print(f"\n Commands for {instrument.name} (Port {port}):")
        print("-" * 80)
        
        # Separate IEEE commands from custom commands
        ieee_commands = []
        custom_commands = []
        
        for cmd in sorted(instrument.commands.keys()):
            if cmd.startswith('*') or cmd.startswith('SYST:'):
                ieee_commands.append(cmd)
            else:
                custom_commands.append(cmd)
        
        if custom_commands:
            print("📡 Custom Commands:")
            for cmd in custom_commands:
                print(f"   {cmd}")
        
        if ieee_commands:
            print("\n⚙️  IEEE 488.2 Standard Commands:")
            for cmd in ieee_commands:
                print(f"   {cmd}")

    def interactive_mode(self):
        """Run interactive command-line interface"""
        print("\n SCPI Emulator Manager - Interactive Mode")
        print("=" * 60)
        print("Commands:")
        print("  help                    - Show this help")
        print("  load <file>             - Load instruments from Excel/CSV")
        print("  list                    - List loaded instruments")
        print("  commands <instrument>   - Show commands for instrument")
        print("  start                   - Start all servers")
        print("  stop                    - Stop all servers")
        print("  status                  - Show server status")
        print("  quit                    - Exit")
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
                
                elif command == 'help':
                    print("\n📖 Available commands:")
                    print("  load, list, commands, start, stop, status, quit")
                
                elif command == 'load':
                    if len(parts) < 2:
                        print("Usage: load <file>")
                        print("Supported formats: .xlsx, .xls, .csv")
                        continue
                    
                    file_path = ' '.join(parts[1:])  # Handle filenames with spaces
                    if self.load_from_file(file_path):
                        print("✅ File loaded successfully!")
                    else:
                        print("❌ Failed to load file")
                
                elif command == 'list':
                    self.list_instruments()
                
                elif command == 'commands':
                    if len(parts) < 2:
                        print("Usage: commands <instrument_id>")
                        continue
                    self.show_commands(parts[1])
                
                elif command == 'start':
                    if not self.instruments:
                        print("❌ No instruments loaded. Use 'load <file>' first.")
                        continue
                    
                    if self.start_all_servers():
                        print("✅ All servers started successfully!")
                    else:
                        print("❌ Failed to start servers")
                
                elif command == 'stop':
                    self.stop_all_servers()
                    print("✅ All servers stopped")
                
                elif command == 'status':
                    if self.running:
                        print(f"✅ Running {len(self.servers)} servers:")
                        for inst_id, server in self.servers.items():
                            print(f"   {server.instrument.name}: {server.host}:{server.port}")
                    else:
                        print("❌ No servers running")
                
                else:
                    print(f"❌ Unknown command: {command}. Type 'help' for available commands.")
                    
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
    """Create an example CSV file with multiple instruments including validation examples"""
    data = [
        # Header
        ['Equipment', 'Port', 'Command', 'Response', 'Validation'],

        # Keysight 34461A Digital Multimeter
        ['Keysight 34461A DMM', '5555', 'MEAS:VOLT:DC?', '1.234567E+00', ''],
        ['', '', 'MEAS:VOLT:AC?', '0.987654E+00', ''],
        ['', '', 'MEAS:CURR:DC?', '1.234567E-03', ''],
        ['', '', 'MEAS:RES?', '1.000000E+03', ''],
        ['', '', 'CONF:VOLT:DC (.+)', 'OK', 'range:0.1,1000'],
        ['', '', 'CONF:VOLT:DC?', 'VOLT +1.000000E+01,+3.000000E-06', ''],
        ['', '', 'NPLC (.+)', 'OK', 'range:0.02,200'],
        ['', '', 'NPLC?', '10', ''],
        
        # Keysight E36312A Power Supply
        ['Keysight E36312A PSU', '5556', 'OUTP ON', 'OK', ''],
        ['', '', 'OUTP OFF', 'OK', ''],
        ['', '', 'OUTP (.+)', 'OK', 'bool'],
        ['', '', 'OUTP?', '1', ''],
        ['', '', 'VOLT (.+)', 'OK', 'range:0,30'],
        ['', '', 'VOLT?', '5.000000E+00', ''],
        ['', '', 'CURR (.+)', 'OK', 'range:0,3'],
        ['', '', 'CURR?', '1.000000E+00', ''],
        ['', '', 'MEAS:VOLT?', '4.987654E+00', ''],
        ['', '', 'MEAS:CURR?', '9.876543E-01', ''],
        
        # Tektronix TDS2024B Oscilloscope
        ['Tektronix TDS2024B Scope', '5557', 'ACQ:MODE?', 'SAMPLE', ''],
        ['', '', 'ACQ:MODE (.+)', 'OK', 'enum:SAMPLE,PEAKDETECT,HIRES,AVERAGE'],
        ['', '', 'CH1:SCAL?', '1.0E+0', ''],
        ['', '', 'CH1:SCAL (.+)', 'OK', 'range:0.001,10'],
        ['', '', 'CH1:POS?', '0.0E+0', ''],
        ['', '', 'CH1:POS (.+)', 'OK', 'range:-40,40'],
        ['', '', 'CH1:COUP?', 'DC', ''],
        ['', '', 'CH1:COUP (.+)', 'OK', 'enum:AC,DC,GND'],
        ['', '', 'TRIG:MAIN:EDGE:SOUR?', 'CH1', ''],
        ['', '', 'TRIG:MAIN:EDGE:SOUR (.+)', 'OK', 'enum:CH1,CH2,CH3,CH4,EXT,LINE'],
        ['', '', 'MEAS:FREQ? CH1', '1.000000E+03', ''],
        ['', '', 'MEAS:AMPL? CH1', '2.500000E+00', ''],
        ['', '', 'CURV?', '#42000-127,-126,-125,-124,-123,-122,-121,-120', ''],
        
        # Agilent 33220A Function Generator
        ['Agilent 33220A Generator', '5558', 'FREQ (.+)', 'OK', 'range:0.000001,20000000'],
        ['', '', 'FREQ?', '1.000000E+03', ''],
        ['', '', 'VOLT (.+)', 'OK', 'range:0.001,10'],
        ['', '', 'VOLT?', '1.000000E+00', ''],
        ['', '', 'FUNC (.+)', 'OK', 'enum:SIN,SQU,TRI,RAMP,PULS,NOIS,DC,USER'],
        ['', '', 'FUNC?', 'SIN', ''],
        ['', '', 'OUTP (.+)', 'OK', 'bool'],
        ['', '', 'OUTP?', '1', ''],
        ['', '', 'PHAS (.+)', 'OK', 'range:-360,360'],
        ['', '', 'PHAS?', '0', ''],
        
        # Example instrument showing all validation types - EASY TESTING
        ['Debug Test Instrument', '5559', '*IDN?', 'DEBUG_INSTRUMENT,MODEL123,SERIAL456,1.0', ''],
        ['', '', 'TEST_RANGE (.+)', 'Range test OK: {value}', 'range:1,10'],
        ['', '', 'TEST_RANGE?', '5', ''],
        ['', '', 'TEST_ENUM (.+)', 'Enum test OK: {value}', 'enum:A,B,C'],
        ['', '', 'TEST_ENUM?', 'A', ''],
        ['', '', 'TEST_BOOL (.+)', 'Bool test OK: {value}', 'bool'],
        ['', '', 'TEST_BOOL?', 'ON', ''],
        ['', '', 'TEST_NOVALIDATION (.+)', 'No validation: {value}', ''],
        ['', '', 'TEST_NOVALIDATION?', 'anything', ''],
    ]

    filename = 'scpi_instruments_example.csv'
    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        writer.writerows(data)

    print(f"Created example file: {filename}")
    print(f"Contains 5 instruments with {len(data)-1} total entries")
    print("\nValidation Examples Included:")
    print("   - range:min,max     (e.g., TEST_RANGE: range:1,10)")
    print("   - enum:val1,val2    (e.g., TEST_ENUM: enum:A,B,C)")
    print("   - bool              (e.g., TEST_BOOL: accepts ON/OFF/1/0)")
    print("   - Port 5559 = Debug Test Instrument (easy validation testing)")
    print("\n💡 For Excel format, you can:")
    print("   1. Open this CSV file in Excel and save as .xlsx")
    print("   2. Or install openpyxl: pip install openpyxl")
    print("\n[VISA DEVICE CLEAR] WINDOWS-COMPATIBLE TESTING:")
    print("   python scpi_emulator_fixed.py --load scpi_instruments_example.csv --start --verbose")
    print("   # Now simulates VISA Device Clear (like NI-MAX) on new connections")
    print("   # Look for logs: [VISA-CLR] = VISA device clear, [CMD]/[RSP] = commands")
    print("")
    print("   telnet localhost 5559   # Connect to debug test instrument")
    print("   TEST_RANGE?             # Check default value")
    print("   TEST_RANGE 7            # Set to 7")  
    print("   TEST_RANGE?             # Should return 7")
    print("   # THEN disconnect and reconnect to test VISA Device Clear")
    print("   TEST_RANGE?             # Should return default, not 7!")
    print("")
    print("Watch for: [VISA-CLR] VISA Device Clear logs on new connections!")


def main():
    print("SCPI Equipment Emulator - VERSION 2.1")
    print("=" * 50)
    
    parser = argparse.ArgumentParser(
        description='SCPI Equipment Emulator v2.1 - LabVIEW Compatible (Pure Python)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --create-example                    # Create example CSV file
  %(prog)s --load instruments.csv --start     # Load CSV and start servers
  %(prog)s --load instruments.xlsx --start    # Load Excel (requires openpyxl)
  %(prog)s --load instruments.csv --port 6000 --interactive
  
LabVIEW Connection:
  Use VISA resource string: TCPIP0::localhost::<port>::INSTR
  Example: TCPIP0::localhost::5555::INSTR for first instrument

Dependencies:
  - Pure Python 3.6+ (no dependencies for CSV files)
  - For Excel files: pip install openpyxl (optional)
        """
    )
    
    parser.add_argument('--load', '-l', help='Load file with instrument definitions (.csv, .xlsx, .xls)')
    parser.add_argument('--start', '-s', action='store_true', help='Start TCP servers immediately')
    parser.add_argument('--port', '-p', type=int, default=5555, help='Starting port number (default: 5555)')
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
            
            if not args.interactive:
                print("\n⏳ Servers running. Press Ctrl+C to stop...")
                try:
                    while manager.running:
                        time.sleep(1)
                except KeyboardInterrupt:
                    manager.stop_all_servers()
    
    # Start interactive mode
    if args.interactive or (not args.load and not args.create_example):
        manager.interactive_mode()

if __name__ == "__main__":
    main()