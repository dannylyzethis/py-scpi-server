from pathlib import Path

from scpi_emulator.configuration import (
    ConfigurationError,
    ExcelReader,
    load_compatibility_instruments,
)
from scpi_emulator.emulator import (
    ConfigurationError as FacadeConfigurationError,
)
from scpi_emulator.emulator import (
    ExcelReader as FacadeExcelReader,
)
from scpi_emulator.emulator import (
    SCPIInstrument as FacadeSCPIInstrument,
)
from scpi_emulator.instrument import SCPIInstrument


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_emulator_facade_preserves_instrument_and_configuration_imports() -> None:
    assert FacadeSCPIInstrument is SCPIInstrument
    assert FacadeConfigurationError is ConfigurationError
    assert FacadeExcelReader is ExcelReader


def test_configuration_module_loads_compatibility_instruments_directly() -> None:
    loaded, command_count = load_compatibility_instruments(
        REPOSITORY_ROOT / "scpi_instruments_example.csv"
    )

    assert set(loaded) == {"virtual_dmm_csv_example", "debug_test_instrument"}
    assert command_count == 7
    assert isinstance(loaded["virtual_dmm_csv_example"]["instrument"], SCPIInstrument)
