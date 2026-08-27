#!/usr/bin/env python3
"""SCPI emulator process, transports, dashboard, and configuration loading."""

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
from .dashboard import CommandLogger as CommandLogger
from .dashboard import HAS_FLASK as HAS_FLASK
from .dashboard import WebDashboard as WebDashboard
from .dashboard import _dashboard_display_response as _dashboard_display_response


def configure_logging(*, verbose=False, log_file=None):
    """Compatibility wrapper for :func:`scpi_emulator.cli.configure_logging`."""
    from .cli import configure_logging as configure_cli_logging

    return configure_cli_logging(verbose=verbose, log_file=log_file)

if __name__ == "__main__":
    raise SystemExit(main())
