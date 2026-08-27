#!/usr/bin/env python3
"""SCPI emulator process, transports, dashboard, and configuration loading."""

import json
import socket
import threading
import time
import logging
import secrets
from pathlib import Path
from datetime import datetime
from collections import deque

from . import __version__
from .configuration import (
    ConfigurationError,
    ExcelReader as ExcelReader,
    compatibility_instrument_id,
    load_compatibility_directory as load_compatibility_directory,
    load_compatibility_instruments as load_compatibility_instruments,
    load_compatibility_path,
    validate_compatibility_rule,
)
from .instrument import SCPIInstrument as SCPIInstrument
from .cli import build_parser as build_parser
from .cli import create_example_csv as create_example_csv
from .cli import main as main
from .socket_transport import MessageTooLarge, SocketMessageFramer, SocketTransportConfig
from .scenario import (
    ScenarioControlError,
    ScenarioError,
    load_scenario,
    loads_scenario,
)

# Flask imports
try:
    from flask import Flask, render_template, jsonify, request
    from flask_socketio import SocketIO
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

logger = logging.getLogger(__name__)


def configure_logging(*, verbose=False, log_file=None):
    """Compatibility wrapper for :func:`scpi_emulator.cli.configure_logging`."""
    from .cli import configure_logging as configure_cli_logging

    return configure_cli_logging(verbose=verbose, log_file=log_file)

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
        print("  scenario load <instrument> <file> - Select scenario JSON/.txt (paused)")
        print("  scenario status <instrument>      - Show scenario and stream positions")
        print("  scenario start|pause|reset <instrument>")
        print("  scenario step <instrument> [stream]")
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

                elif command == 'scenario':
                    self._interactive_scenario(parts[1] if len(parts) > 1 else "")
                
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
                    print(
                        "Use load, load bench, create bench, instruments, catalog, start, "
                        "web, scenario, status, stop, or quit."
                    )
                
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

    def _interactive_scenario(self, argument):
        """Execute one live scenario-control command for a configured instrument."""
        tokens = argument.split(maxsplit=2)
        action = tokens[0].casefold() if tokens else ""
        if action == "load":
            if len(tokens) < 3:
                print("Usage: scenario load <instrument> <file>")
                return
            instrument_id = tokens[1]
            scenario_path = Path(_interactive_path(tokens[2])).resolve()
            try:
                definition = load_scenario(scenario_path)
                result = self._execute_interactive_scenario(
                    instrument_id,
                    lambda instrument: instrument.scenario_control.select(definition),
                )
                print(
                    f"[OK] Scenario {result['scenario']!r} loaded for {instrument_id} "
                    "(paused)"
                )
            except (KeyError, OSError, ScenarioError, RuntimeError, TypeError, ValueError) as error:
                print(f"[ERROR] Could not load scenario {str(scenario_path)!r}: {error}")
            return

        if action not in {"status", "start", "pause", "reset", "step"}:
            self._print_scenario_usage()
            return
        if len(tokens) < 2:
            self._print_scenario_usage()
            return
        instrument_id = tokens[1]
        stream = tokens[2].strip() if action == "step" and len(tokens) == 3 else None
        try:
            if action == "status":
                result = self._scenario_instrument(instrument_id).scenario_control.inspect()
            elif action == "start":
                result = self._execute_interactive_scenario(
                    instrument_id, lambda instrument: instrument.scenario_control.start()
                )
            elif action == "pause":
                result = self._execute_interactive_scenario(
                    instrument_id, lambda instrument: instrument.scenario_control.pause()
                )
            elif action == "reset":
                result = self._execute_interactive_scenario(
                    instrument_id, lambda instrument: instrument.scenario_control.reset()
                )
            else:
                positions = self._execute_interactive_scenario(
                    instrument_id,
                    lambda instrument: instrument.scenario_control.step(stream),
                )
                result = self._scenario_instrument(instrument_id).scenario_control.inspect()
                print(f"[OK] Stepped {stream or 'all streams'} for {instrument_id}")
                self._print_scenario_status(instrument_id, result, positions=positions)
                return
            if action != "status":
                print(f"[OK] Scenario {action} applied to {instrument_id}")
            self._print_scenario_status(instrument_id, result)
        except (KeyError, ScenarioError, RuntimeError, TypeError, ValueError) as error:
            print(f"[ERROR] Scenario {action} failed for {instrument_id!r}: {error}")

    def _scenario_instrument(self, instrument_id):
        entry = self.active_instruments.get(instrument_id)
        if entry is None:
            raise KeyError(f"instrument {instrument_id!r} is not configured")
        instrument = entry.get('instrument') if isinstance(entry, dict) else None
        if instrument is None:
            instrument = getattr(entry, 'instrument', None)
        if instrument is None:
            raise KeyError(f"instrument {instrument_id!r} is not configured")
        return instrument

    def _execute_interactive_scenario(self, instrument_id, action):
        instrument = self._scenario_instrument(instrument_id)
        server = self.active_runtime.servers.get(instrument_id)
        if server is not None and hasattr(server, 'execute_control_action'):
            result = server.execute_control_action(action)
        else:
            result = action(instrument)
        dashboard = getattr(self.active_runtime, 'web_dashboard', None)
        if dashboard is not None:
            dashboard.emit_state_changed('scenario-interactive', instrument_id)
        return result

    @staticmethod
    def _print_scenario_status(instrument_id, scenario, *, positions=None):
        print(
            f"Scenario {instrument_id}: {scenario['state']} | "
            f"{scenario['scenario'] or 'none'} | seed {scenario['seed']}"
        )
        displayed = positions if positions is not None else scenario['streams']
        for position in displayed:
            print(
                f"  {position['stream']}: sample {position['index'] + 1}/"
                f"{position['sample_count']} | reads {position['reads']} | "
                f"advances {position['advances']}"
            )

    @staticmethod
    def _print_scenario_usage():
        print("Usage: scenario load <instrument> <file>")
        print("       scenario status <instrument>")
        print("       scenario start|pause|reset <instrument>")
        print("       scenario step <instrument> [stream]")

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
if __name__ == "__main__":
    raise SystemExit(main())
