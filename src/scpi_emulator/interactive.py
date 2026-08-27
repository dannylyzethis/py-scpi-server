"""Interactive shell for managing virtual instrument compositions."""

from pathlib import Path

from . import __version__
from .configuration import ConfigurationError
from .scenario import ScenarioError, load_scenario


class InteractiveShell:
    """Command interpreter over an :class:`SCPIEmulatorManager` contract."""

    def __init__(self, manager):
        self.manager = manager
        self._interactive_catalog = manager._interactive_catalog

    def __getattr__(self, name):
        return getattr(self.manager, name)

    def run(self):
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

                if command == "quit":
                    if self.active_running:
                        self.stop_active_servers()
                    break

                elif command == "load":
                    if len(parts) < 2:
                        print("Usage: load <path> or load bench <file>")
                        continue
                    argument = parts[1].strip()
                    if argument.casefold().startswith("bench "):
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

                elif command == "bench":
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

                elif command == "instruments":
                    self._print_configured_instruments()

                elif command == "catalog":
                    self._print_catalog(parts[1] if len(parts) > 1 else "")

                elif command == "create":
                    if len(parts) < 2 or not parts[1].casefold().startswith("bench "):
                        print("Usage: create bench <file>")
                        continue
                    target = Path(_interactive_path(parts[1][6:])).resolve()
                    try:
                        from .bench import BenchBuildCancelled, BenchRuntime, GuidedBenchBuilder
                        from .drivers import build_driver_catalog

                        csv_directory = (
                            target.parent
                            if target.parent.is_dir() and any(target.parent.glob("*.csv"))
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

                elif command == "start":
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

                elif command == "web":
                    try:
                        started = self.start_active_dashboard()
                    except Exception as error:
                        print(f"[ERROR] Failed to start web dashboard: {error}")
                        continue
                    if not started:
                        print("[ERROR] Failed to start web dashboard")
                    else:
                        print("[OK] Web dashboard started at http://127.0.0.1:8081")

                elif command == "scenario":
                    self._interactive_scenario(parts[1] if len(parts) > 1 else "")

                elif command == "status":
                    source = self._active_source or "none"
                    state = "running" if self.active_running else "stopped"
                    print(f"Active configuration: {source}")
                    print(f"Server state: {state}")
                    self._print_configured_instruments()

                elif command == "stop":
                    self.stop_active_servers()
                    print("[OK] Active instruments stopped")

                elif command == "help":
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
                print(f"[OK] Scenario {result['scenario']!r} loaded for {instrument_id} (paused)")
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
        instrument = entry.get("instrument") if isinstance(entry, dict) else None
        if instrument is None:
            instrument = getattr(entry, "instrument", None)
        if instrument is None:
            raise KeyError(f"instrument {instrument_id!r} is not configured")
        return instrument

    def _execute_interactive_scenario(self, instrument_id, action):
        instrument = self._scenario_instrument(instrument_id)
        server = self.active_runtime.servers.get(instrument_id)
        if server is not None and hasattr(server, "execute_control_action"):
            result = server.execute_control_action(action)
        else:
            result = action(instrument)
        dashboard = getattr(self.active_runtime, "web_dashboard", None)
        if dashboard is not None:
            dashboard.emit_state_changed("scenario-interactive", instrument_id)
        return result

    @staticmethod
    def _print_scenario_status(instrument_id, scenario, *, positions=None):
        print(
            f"Scenario {instrument_id}: {scenario['state']} | "
            f"{scenario['scenario'] or 'none'} | seed {scenario['seed']}"
        )
        displayed = positions if positions is not None else scenario["streams"]
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
            if argument.casefold().startswith("csv "):
                directory = _interactive_path(argument[4:])
                self._interactive_catalog = build_driver_catalog(csv_directory=directory)
                print(f"[OK] Included CSV instruments from {directory}")
                argument = "csv-instruments"
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
                + ", ".join(f"{item.name} ({item.support.value})" for item in driver.transports)
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
                        f"{item.kind} ({item.support.value})" for item in driver.scenario_inputs
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
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
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
    identity = str(getattr(instrument, "identification", "")).split(",", 3)
    model = model or (
        identity[1] if len(identity) == 4 else getattr(instrument, "name", instrument_id)
    )
    serial = serial or (identity[2] if len(identity) == 4 else instrument_id)
    reported_model = reported_model or (
        identity[1] if len(identity) == 4 else getattr(instrument, "name", instrument_id)
    )
    return {
        "id": instrument_id,
        "model": model,
        "reported_model": reported_model,
        "serial": serial,
        "state": "running" if running else "stopped",
        "resource": resource,
    }
