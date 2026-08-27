from pathlib import Path

from scpi_emulator.configuration import (
    ConfigurationError,
    ExcelReader,
    load_compatibility_instruments,
)
from scpi_emulator.cli import build_parser as DirectBuildParser
from scpi_emulator.cli import main as DirectMain
from scpi_emulator.emulator import (
    ConfigurationError as FacadeConfigurationError,
)
from scpi_emulator.emulator import (
    ExcelReader as FacadeExcelReader,
)
from scpi_emulator.emulator import (
    SCPIInstrument as FacadeSCPIInstrument,
)
from scpi_emulator.emulator import SCPIEmulatorManager
from scpi_emulator.emulator import build_parser as FacadeBuildParser
from scpi_emulator.emulator import main as FacadeMain
from scpi_emulator.instrument import SCPIInstrument


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_emulator_facade_preserves_instrument_and_configuration_imports() -> None:
    assert FacadeSCPIInstrument is SCPIInstrument
    assert FacadeConfigurationError is ConfigurationError
    assert FacadeExcelReader is ExcelReader
    assert FacadeBuildParser is DirectBuildParser
    assert FacadeMain is DirectMain


def test_configuration_module_loads_compatibility_instruments_directly() -> None:
    loaded, command_count = load_compatibility_instruments(
        REPOSITORY_ROOT / "scpi_instruments_example.csv"
    )

    assert set(loaded) == {"virtual_dmm_csv_example", "debug_test_instrument"}
    assert command_count == 7
    assert isinstance(loaded["virtual_dmm_csv_example"]["instrument"], SCPIInstrument)


def test_manager_construction_does_not_install_process_signal_handlers(monkeypatch) -> None:
    """Library consumers can construct managers without mutating process signals."""
    import signal

    calls = []
    monkeypatch.setattr(signal, "signal", lambda *args: calls.append(args))

    SCPIEmulatorManager()

    assert calls == []
