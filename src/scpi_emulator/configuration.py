"""CSV/XLSX compatibility definition loading independent of the CLI and runtime manager."""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from .instrument import SCPIInstrument


logger = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when an instrument definition file is structurally invalid."""


class ExcelReader:
    """Read and structurally validate CSV or XLSX configuration rows."""

    COLUMNS = ("Equipment", "Port", "Command", "Response", "Validation")

    @classmethod
    def _normalize_headers(cls, headers, source):
        normalized = [str(header or "").strip() for header in headers]
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
            headers = cls._normalize_headers([cell.value for cell in worksheet[1]], excel_path)

            data = []
            for row in worksheet.iter_rows(min_row=2, values_only=True):
                values = list(row)
                if len(values) > len(headers) and any(values[len(headers) :]):
                    raise ConfigurationError(
                        f"{excel_path}: worksheet row has data beyond the declared columns"
                    )
                row_dict = {
                    header: (
                        str(values[index]).strip()
                        if index < len(values) and values[index] is not None
                        else ""
                    )
                    for index, header in enumerate(headers)
                }
                data.append(row_dict)

            workbook.close()
            return data
        except ConfigurationError:
            raise
        except Exception as error:
            raise ConfigurationError(
                f"{excel_path}: unable to read XLSX file: {error}"
            ) from error

    @classmethod
    def read_csv(cls, csv_path):
        """Read a CSV file and reject rows whose fields spill past the header."""
        try:
            data = []
            with open(csv_path, "r", newline="", encoding="utf-8-sig") as csvfile:
                sample = csvfile.read(4096)
                csvfile.seek(0)
                try:
                    delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
                except csv.Error:
                    delimiter = ","

                logger.debug("Using delimiter: %r", delimiter)
                reader = csv.DictReader(csvfile, delimiter=delimiter)
                reader.fieldnames = cls._normalize_headers(reader.fieldnames or [], csv_path)

                for row_num, row in enumerate(reader, 2):
                    extras = row.pop(None, [])
                    if extras:
                        raise ConfigurationError(
                            f"{csv_path}: row {row_num} has fields beyond the declared columns; "
                            "quote values containing commas"
                        )
                    clean_row = {key: str(value or "").strip() for key, value in row.items()}
                    if any(clean_row.values()):
                        data.append(clean_row)

            return data
        except ConfigurationError:
            raise
        except Exception as error:
            raise ConfigurationError(f"{csv_path}: unable to read CSV file: {error}") from error


def compatibility_instrument_id(name: str) -> str:
    """Return the stable identifier used by compatibility Equipment blocks."""
    instrument_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not instrument_id:
        raise ConfigurationError("equipment name must contain a letter or number")
    return instrument_id


def validate_compatibility_rule(rule: str, row_num: int) -> None:
    """Validate one CSV compatibility parameter rule."""
    if not rule or rule == "bool":
        return
    if rule.startswith("range:"):
        values = rule.split(":", 1)[1].split(",")
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
    if rule.startswith("enum:"):
        values = [value.strip() for value in rule.split(":", 1)[1].split(",")]
        if not values or any(not value for value in values):
            raise ConfigurationError(
                f"row {row_num}: enum validation requires non-empty values"
            )
        return
    raise ConfigurationError(f"row {row_num}: unsupported validation rule '{rule}'")


def load_compatibility_instruments(file_path, port_start=5025, *, reserved_ports=()):
    """Parse and construct CSV/XLSX instruments without mutating a manager."""
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        raise ConfigurationError(f"file not found: {file_path}")
    if file_path_obj.suffix.lower() == ".xlsx":
        data = ExcelReader.read_excel_as_csv(file_path)
    elif file_path_obj.suffix.lower() == ".csv":
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

    logger.info("Processing %s rows from %s", len(data), file_path)
    for row_num, row in enumerate(data, 2):
        equipment_name = row.get("Equipment", "").strip()
        port_text = row.get("Port", "").strip()
        command = row.get("Command", "").strip()
        response = row.get("Response", "").strip()
        validation = row.get("Validation", "").strip()

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
                    raise ConfigurationError(f"row {row_num}: port must be an integer") from error
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
                "instrument": current_instrument,
                "port": port,
            }
            used_ports.add(port)
            current_commands = set()
            logger.info(
                "Row %s: Created instrument: %s (Port: %s)",
                row_num,
                equipment_name,
                port,
            )

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
            if validation and "(.+)" not in command and "{value}" not in command:
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
        instrument_data["instrument"].csv_compatibility.link_stateful_commands()
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
            (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".csv"),
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
            equipment_name = row.get("Equipment", "").strip()
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
            port = item["port"]
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
