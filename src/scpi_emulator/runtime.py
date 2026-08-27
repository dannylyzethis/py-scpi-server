"""Runtime manager for compatibility and composed virtual instruments."""

import logging
from pathlib import Path

from .configuration import (
    ConfigurationError,
    compatibility_instrument_id,
    load_compatibility_path,
    validate_compatibility_rule,
)
from .interactive import _interactive_instrument_row
from .raw_server import SCPIServer

logger = logging.getLogger(__name__)

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
        from .emulator import HAS_FLASK, WebDashboard
        if not HAS_FLASK:
            logger.warning("Flask not available. Cannot start web dashboard.")
            return False
            
        self.web_dashboard = WebDashboard(self, host, port, auth_token=auth_token)
        return self.web_dashboard.start()

    def interactive_mode(self):
        """Run the compatibility interactive shell."""
        from .interactive import InteractiveShell

        InteractiveShell(self).run()
