import json

from scpi_emulator.emulator import SCPIInstrument


def pna(tmp_path, instrument_id="N5222B-EMU"):
    return SCPIInstrument(
        "Virtual N5222B-EMU", instrument_id, state_directory=tmp_path
    )


def test_named_state_file_round_trip_persists_existence_only(tmp_path):
    instrument = pna(tmp_path)
    instrument.process_command('CALC2:PAR:DEF:EXT "Gain","S21"')
    instrument.process_command('DISP:WIND2:TRAC3:FEED "Gain"')
    instrument.process_command("SENS2:FREQ:STAR 2GHz")
    instrument.process_command('MMEM:STOR:STAT "bench.sta"')

    saved = json.loads((tmp_path / "N5222B-EMU" / "bench.sta").read_text())
    assert set(saved) == {"schema_version", "channels", "windows"}
    assert "frequency" not in json.dumps(saved).lower()
    assert instrument.process_command("MMEM:CAT?") == '"bench.sta"'

    instrument.process_command("*RST")
    assert instrument.process_command("CALC2:PAR:CAT?") == ""
    instrument.process_command('MMEM:LOAD:STAT "bench.sta"')
    assert instrument.process_command("CALC2:PAR:CAT?") == '"Gain,S21"'
    assert instrument.process_command("DISP:WIND2:TRAC3:FEED?") == "Gain"
    assert instrument.process_command("SENS2:FREQ:STAR?") == "10000000"


def test_catalog_delete_and_instrument_isolation(tmp_path):
    first = pna(tmp_path, "one")
    second = pna(tmp_path, "two")
    first.process_command('MMEM:STOR:STAT "z.sta"')
    first.process_command('MMEM:STOR:STAT "a.sta"')

    assert first.process_command("MMEM:CAT?") == '"a.sta,z.sta"'
    assert second.process_command("MMEM:CAT?") == "EMPTY"
    assert first.process_command('MMEM:DEL "a.sta"') == ""
    assert first.process_command("MMEM:CAT?") == '"z.sta"'


def test_missing_unsafe_and_malformed_files_report_errors_without_mutation(tmp_path):
    instrument = pna(tmp_path)
    original = instrument.process_command("CALC:PAR:CAT?")

    for command, code in (
        ('MMEM:LOAD:STAT "missing.sta"', "-256"),
        ('MMEM:STOR:STAT "../escape.sta"', "-257"),
        ('MMEM:DEL "missing.sta"', "-256"),
    ):
        assert instrument.process_command(command) == ""
        assert instrument.process_command("SYST:ERR?").startswith(code)

    directory = tmp_path / "N5222B-EMU"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bad.sta").write_text('{"schema_version": 1}', encoding="utf-8")
    assert instrument.process_command('MMEM:LOAD:STAT "bad.sta"') == ""
    assert instrument.process_command("SYST:ERR?").startswith("-257")
    assert instrument.process_command("CALC:PAR:CAT?") == original


def test_recall_rejects_dangling_trace_atomically(tmp_path):
    instrument = pna(tmp_path)
    directory = tmp_path / "N5222B-EMU"
    directory.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "channels": [{"number": 2, "measurements": []}],
        "windows": [
            {"number": 2, "traces": [{"number": 1, "measurement": "missing"}]}
        ],
    }
    (directory / "dangling.sta").write_text(json.dumps(payload), encoding="utf-8")

    instrument.process_command('MMEM:LOAD:STAT "dangling.sta"')
    assert instrument.process_command("SYST:ERR?").startswith("-257")
    assert instrument.process_command("CALC:PAR:CAT?") == '"CH1_S11_1,S11"'


def test_addressed_objects_are_gated_before_handlers(tmp_path):
    instrument = pna(tmp_path)

    assert instrument.process_command("CALC9:PAR:CAT?") == ""
    assert instrument.process_command("SYST:ERR?").startswith(
        '-200,"Execution error; addressed object does not exist'
    )
    assert instrument.process_command("DISP:WIND9:TRAC2:FEED?") == ""
    assert instrument.process_command("SYST:ERR?").startswith(
        '-200,"Execution error; addressed object does not exist'
    )

    # Commands whose purpose is to create an address remain available.
    assert instrument.process_command('CALC9:PAR:DEF:EXT "New","S21"') == ""
    assert instrument.process_command('DISP:WIND9:TRAC2:FEED "New"') == ""
    assert instrument.process_command("DISP:WIND9:TRAC2:FEED?") == "New"
