from pathlib import Path

from scpi_emulator.cli import build_parser, main
from scpi_emulator.configuration import (
    ConfigurationError,
    ExcelReader,
    load_compatibility_instruments,
)
from scpi_emulator.dashboard import WebDashboard
from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.interactive import InteractiveShell
from scpi_emulator.raw_server import SCPIServer
from scpi_emulator.runtime import SCPIEmulatorManager

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_transitional_emulator_facade_is_removed() -> None:
    assert not (REPOSITORY_ROOT / "src" / "scpi_emulator" / "emulator.py").exists()
    prohibited_import = "scpi_emulator." + "emulator"
    for directory in ("src", "tests", "tools"):
        for path in (REPOSITORY_ROOT / directory).rglob("*.py"):
            assert prohibited_import not in path.read_text(encoding="utf-8")
    assert all(
        item is not None
        for item in (
            ConfigurationError,
            ExcelReader,
            SCPIInstrument,
            SCPIEmulatorManager,
            SCPIServer,
            WebDashboard,
            build_parser,
            main,
        )
    )


def test_configuration_module_loads_compatibility_instruments_directly() -> None:
    loaded, command_count = load_compatibility_instruments(
        REPOSITORY_ROOT / "examples" / "csv" / "basic" / "scpi_instruments_example.csv"
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


def test_manager_interactive_entry_delegates_to_shell(monkeypatch) -> None:
    managers = []
    monkeypatch.setattr(InteractiveShell, "run", lambda self: managers.append(self.manager))
    manager = SCPIEmulatorManager()

    manager.interactive_mode()

    assert managers == [manager]
