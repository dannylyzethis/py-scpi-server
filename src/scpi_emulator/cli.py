"""Console entry point and process lifecycle for the SCPI emulator."""

import argparse
import csv
import logging
import os
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .configuration import ConfigurationError, load_compatibility_path

logger = logging.getLogger(__name__)


def configure_logging(*, verbose=False, log_file=None):
    """Configure application logging without import-time filesystem writes."""
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.insert(0, logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )


def create_example_csv():
    """Create an example compatibility CSV with validation rules."""
    data = [
        ["Equipment", "Port", "Command", "Response", "Validation"],
        ["Virtual DMM", "5555", "MEAS:VOLT:DC?", "1.234567E+00", ""],
        ["", "", "VOLT (.+)", "OK", "range:0,10"],
        ["", "", "VOLT?", "5.0", ""],
        ["Debug Test Instrument", "5559", "TEST_RANGE (.+)", "Range OK: {value}", "range:1,10"],
        ["", "", "TEST_RANGE?", "5", ""],
        ["", "", "TEST_ENUM (.+)", "Enum OK: {value}", "enum:A,B,C"],
        ["", "", "TEST_ENUM?", "A", ""],
    ]
    filename = "scpi_instruments_example.csv"
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
        writer.writerows(data)
    print(f"Created example file: {filename}")


def build_parser():
    """Build the command-line parser without starting the emulator."""
    parser = argparse.ArgumentParser(
        description="Stateful SCPI instrument emulator for automation development and testing"
    )
    definitions = parser.add_mutually_exclusive_group()
    definitions.add_argument(
        "--load", "-l", metavar="PATH", help="Point at one CSV/XLSX file or a folder of CSVs"
    )
    definitions.add_argument(
        "--bench", metavar="FILE", help="Define a precise multi-instrument bench from JSON"
    )
    parser.add_argument("--start", "-s", action="store_true", help="Start TCP servers immediately")
    parser.add_argument("--web", "-w", action="store_true", help="Start web dashboard")
    parser.add_argument("--web-port", type=int, default=8081, help="Web dashboard port (default: 8081)")
    parser.add_argument("--web-host", default="127.0.0.1", help="Dashboard bind host (default: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=5025, help="Starting port for instruments (default: 5025)")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--create-example", action="store_true", help="Create example CSV file")
    parser.add_argument("--interactive", "-i", action="store_true", help="Start interactive mode")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--log-file", help="Write logs to this file in addition to stderr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _install_signal_handlers(manager):
    """Install process-wide handlers only from the executable path."""
    def shutdown(signum, _frame):
        logger.info("Received shutdown signal %s, stopping servers...", signum)
        manager.stop_active_servers()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line process."""
    from .emulator import SCPIEmulatorManager

    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose, log_file=args.log_file)
    print(f"SCPI Instrument Emulator {__version__}")
    print("=" * 60)

    if args.create_example:
        create_example_csv()
        return 0

    manager = SCPIEmulatorManager()
    _install_signal_handlers(manager)
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
            "Successfully loaded %s instruments with %s commands",
            len(loaded_instruments),
            commands_added,
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
                item.driver.casefold() == "csv-instruments"
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
                auth_token=os.environ.get("SCPI_EMULATOR_WEB_TOKEN"),
            )
        except Exception as error:
            print(f"Error: could not start web dashboard: {error}", file=sys.stderr)
            return 1
        if not dashboard_started:
            print("Error: could not start web dashboard", file=sys.stderr)
            return 1

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

