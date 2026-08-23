import csv
from pathlib import Path

import pytest

from scpi_emulator.emulator import SCPIEmulatorManager


REPOSITORY_ROOT = Path(__file__).parents[1]


def test_csv_configuration_loads_multiple_instruments(tmp_path: Path) -> None:
    config = tmp_path / "instruments.csv"
    config.write_text(
        "Equipment,Port,Command,Response,Validation\n"
        'DMM,6101,*IDN?,"Vendor,DMM,SN1,1.0",\n'
        ',,VOLT (.+),OK,"range:0,10"\n'
        ",,VOLT?,5,\n"
        "PSU,6102,OUTP (.+),OK,bool\n"
        ",,OUTP?,OFF,\n",
        encoding="utf-8",
    )

    manager = SCPIEmulatorManager()
    assert manager.load_from_file(config)

    assert set(manager.instruments) == {"dmm", "psu"}
    assert manager.instruments["dmm"]["port"] == 6101
    assert manager.instruments["psu"]["port"] == 6102

    dmm = manager.instruments["dmm"]["instrument"]
    assert dmm.process_command("*IDN?") == "Vendor,DMM,SN1,1.0"
    assert dmm.process_command("VOLT 6") == "OK"
    assert dmm.process_command("VOLT?") == "6"


def test_configuration_rejects_missing_required_columns(tmp_path: Path) -> None:
    config = tmp_path / "invalid.csv"
    config.write_text("Equipment,Port,Command\nDMM,6101,*IDN?\n", encoding="utf-8")

    manager = SCPIEmulatorManager()
    assert manager.load_from_file(config) is False
    assert manager.instruments == {}


@pytest.mark.parametrize(
    ("filename", "instrument_count"),
    [
        ("scpi_instruments_example.csv", 2),
        ("detailed_instruments.csv", 8),
        ("pna-commands.csv", 1),
    ],
)
def test_shipped_catalogs_are_canonical_and_loadable(
    filename: str, instrument_count: int
) -> None:
    path = REPOSITORY_ROOT / filename
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))

    assert rows[0] == ["Equipment", "Port", "Command", "Response", "Validation"]
    assert {len(row) for row in rows} == {5}

    manager = SCPIEmulatorManager()
    assert manager.load_from_file(path)
    assert len(manager.instruments) == instrument_count
    assert "```" not in manager.instruments


def test_pna_identity_response_is_not_truncated() -> None:
    manager = SCPIEmulatorManager()
    assert manager.load_from_file(REPOSITORY_ROOT / "pna-commands.csv")

    pna = manager.instruments["virtual_vna_n5222b_emu"]["instrument"]
    assert pna.process_command("*IDN?") == (
        "SCPI Emulator,N5222B-EMU,US12345678,E.1.0"
    )
    assert pna.process_command("SYST:CAP:FREQ:MAX?") == "26500000000"
    assert pna.process_command("SENS1:FREQ:STOP?") == "26500000000"
    assert pna.process_command("SENS1:FREQ:STOP 30e9") == ""
    assert pna.process_command("SYST:ERR?").startswith('-222,"Data out of range')
    # Typed VNA data owns this command now; validate sweep shape instead of the old canned row.
    formatted = pna.process_command("CALC1:DATA? FDAT").split(",")
    assert len(formatted) == 201
    assert set(formatted) == {"-inf"}


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                "Device,6101,*IDN?,Device A,",
                "Other Device,6101,*IDN?,Device B,",
            ],
            "duplicate port 6101",
        ),
        (
            [
                "My Device,6101,*IDN?,Device A,",
                "My-Device,6102,*IDN?,Device B,",
            ],
            "duplicate equipment identifier 'my_device'",
        ),
        (
            [
                "Device,6101,*IDN?,Device A,",
                ",,*IDN?,Device B,",
            ],
            "duplicate command '*IDN?'",
        ),
        (
            ["Device,6101,VOLT (.+),OK,custom:1"],
            "unsupported validation rule 'custom:1'",
        ),
    ],
)
def test_semantically_invalid_configuration_is_rejected(
    tmp_path: Path, caplog, rows: list[str], message: str
) -> None:
    config = tmp_path / "invalid.csv"
    config.write_text(
        "Equipment,Port,Command,Response,Validation\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )

    manager = SCPIEmulatorManager()
    assert manager.load_from_file(config) is False
    assert message in caplog.text


def test_spilled_csv_fields_are_rejected_with_actionable_error(
    tmp_path: Path, caplog
) -> None:
    config = tmp_path / "spilled.csv"
    config.write_text(
        "Equipment,Port,Command,Response,Validation\n"
        "Device,6101,*IDN?,Vendor,Model,Serial,Firmware\n",
        encoding="utf-8",
    )

    manager = SCPIEmulatorManager()
    assert manager.load_from_file(config) is False
    assert "quote values containing commas" in caplog.text


def test_failed_reload_preserves_active_configuration(tmp_path: Path) -> None:
    manager = SCPIEmulatorManager()
    assert manager.load_from_file(REPOSITORY_ROOT / "scpi_instruments_example.csv")
    original_ids = set(manager.instruments)

    invalid = tmp_path / "invalid.csv"
    invalid.write_text(
        "Equipment,Port,Command,Response,Validation\n"
        "Device,not-a-port,*IDN?,Device,\n",
        encoding="utf-8",
    )

    assert manager.load_from_file(invalid) is False
    assert set(manager.instruments) == original_ids


def test_command_with_empty_response_is_loaded(tmp_path: Path) -> None:
    config = tmp_path / "write_only.csv"
    config.write_text(
        "Equipment,Port,Command,Response,Validation\n"
        "Device,6101,ACTION,,\n",
        encoding="utf-8",
    )

    manager = SCPIEmulatorManager()
    assert manager.load_from_file(config)
    assert manager.instruments["device"]["instrument"].process_command("ACTION") == ""
