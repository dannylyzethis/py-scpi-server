#!/usr/bin/env python3
"""SCPI emulator process, transports, dashboard, and configuration loading."""

import json
import threading
import time
import logging
import secrets
from datetime import datetime
from collections import deque

from .configuration import (
    ConfigurationError as ConfigurationError,
    ExcelReader as ExcelReader,
    compatibility_instrument_id as compatibility_instrument_id,
    load_compatibility_directory as load_compatibility_directory,
    load_compatibility_instruments as load_compatibility_instruments,
    load_compatibility_path as load_compatibility_path,
    validate_compatibility_rule as validate_compatibility_rule,
)
from .instrument import SCPIInstrument as SCPIInstrument
from .raw_server import SCPIServer as SCPIServer
from .runtime import SCPIEmulatorManager as SCPIEmulatorManager
from .cli import build_parser as build_parser
from .cli import create_example_csv as create_example_csv
from .cli import main as main
from .scenario import (
    ScenarioControlError,
    ScenarioError,
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


if __name__ == "__main__":
    raise SystemExit(main())
