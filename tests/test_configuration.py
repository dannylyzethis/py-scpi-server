from pathlib import Path

from scpi_emulator.emulator import SCPIEmulatorManager


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

