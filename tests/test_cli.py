import json

import pytest

from scpi_emulator import __version__
from scpi_emulator import emulator
from scpi_emulator.bench import BenchRuntime
from scpi_emulator.emulator import SCPIEmulatorManager, build_parser, main


def test_package_version_is_exposed() -> None:
    assert __version__ == "0.1.0"


def test_parser_accepts_create_example() -> None:
    args = build_parser().parse_args(["--create-example"])
    assert args.create_example is True


def test_help_exits_successfully(capsys) -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("argparse help should terminate through SystemExit")

    output = capsys.readouterr().out
    assert "Stateful SCPI instrument emulator" in output
    assert "Point at one CSV/XLSX file or a folder of CSVs" in output
    assert "Define a precise multi-instrument bench from JSON" in output


def test_version_exits_successfully(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("argparse version should terminate through SystemExit")

    assert capsys.readouterr().out.rstrip().endswith(__version__)


def _interrupt_on_sleep(_seconds: float) -> None:
    raise KeyboardInterrupt


def _start_manager(manager: SCPIEmulatorManager, _host: str) -> bool:
    manager.running = True
    return True


def test_directory_load_starts_all_csvs_and_prints_resources_and_dashboard(
    tmp_path, monkeypatch, capsys
) -> None:
    definitions = tmp_path / "instruments"
    definitions.mkdir()
    (definitions / "first.csv").write_text(
        "Equipment,Port,Command,Response,Validation\n"
        "First Device,,VALUE?,ONE,\n",
        encoding="utf-8",
    )
    (definitions / "second.csv").write_text(
        "Equipment,Port,Command,Response,Validation\n"
        "Second Device,,VALUE?,TWO,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(SCPIEmulatorManager, "start_all_servers", _start_manager)
    monkeypatch.setattr(
        SCPIEmulatorManager,
        "start_web_dashboard",
        lambda self, host, port, auth_token=None: True,
    )
    monkeypatch.setattr(emulator.time, "sleep", _interrupt_on_sleep)

    assert main(
        [
            "--load",
            str(definitions),
            "--start",
            "--port",
            "6200",
            "--web",
            "--web-port",
            "9090",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "first_device: TCPIP::localhost::6200::SOCKET" in output
    assert "second_device: TCPIP::localhost::6201::SOCKET" in output
    assert "Web dashboard: http://127.0.0.1:9090" in output


def test_directory_load_rejects_duplicate_equipment_with_both_filenames(
    tmp_path, capsys
) -> None:
    definitions = tmp_path / "duplicates"
    definitions.mkdir()
    for filename in ("alpha.csv", "beta.csv"):
        (definitions / filename).write_text(
            "Equipment,Port,Command,Response,Validation\n"
            "Shared Device,,VALUE?,1,\n",
            encoding="utf-8",
        )

    assert main(["--load", str(definitions), "--start"]) == 1

    error = capsys.readouterr().err
    assert "duplicate equipment name 'Shared Device'" in error
    assert "alpha.csv" in error
    assert "beta.csv" in error
    assert "Traceback" not in error


def test_empty_and_malformed_directories_fail_in_plain_language(tmp_path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--load", str(empty), "--start"]) == 1
    empty_error = capsys.readouterr().err
    assert "contains no CSV files" in empty_error
    assert str(empty) in empty_error

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    bad_file = malformed / "bad.csv"
    bad_file.write_text("Equipment,Port,Command\nDevice,,VALUE?\n", encoding="utf-8")
    assert main(["--load", str(malformed), "--start"]) == 1
    malformed_error = capsys.readouterr().err
    assert "bad.csv" in malformed_error
    assert "missing required columns" in malformed_error
    assert "Traceback" not in malformed_error


def test_bench_starts_unchanged_and_prints_precise_resource_and_dashboard(
    tmp_path, monkeypatch, capsys
) -> None:
    (tmp_path / "relay.csv").write_text(
        "Equipment,Port,Command,Response,Validation\n"
        "Bench Relay,,STATE?,OPEN,\n",
        encoding="utf-8",
    )
    bench = tmp_path / "bench.json"
    bench.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "cli-bench",
                "instruments": [
                    {
                        "id": "meter1",
                        "driver": "virtual-3446x",
                        "model": "34461A-EMU",
                        "resource": {
                            "transport": "raw-socket",
                            "host": "127.0.0.1",
                            "port": 6301,
                        },
                    },
                    {
                        "id": "relay1",
                        "driver": "csv-instruments",
                        "model": "bench_relay",
                        "resource": {
                            "transport": "raw-socket",
                            "host": "127.0.0.1",
                            "port": 6302,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(BenchRuntime, "start", lambda self: None)
    monkeypatch.setattr(
        BenchRuntime,
        "start_web_dashboard",
        lambda self, host, port, auth_token=None: True,
    )
    monkeypatch.setattr(emulator.time, "sleep", _interrupt_on_sleep)

    assert main(["--bench", str(bench), "--start", "--web"]) == 0

    output = capsys.readouterr().out
    assert "meter1: TCPIP::127.0.0.1::6301::SOCKET" in output
    assert "relay1: TCPIP::127.0.0.1::6302::SOCKET" in output
    assert "Web dashboard: http://127.0.0.1:8081" in output


def test_bad_bench_fails_cleanly_and_names_file(tmp_path, capsys) -> None:
    bench = tmp_path / "broken.json"
    bench.write_text("{not-json", encoding="utf-8")

    assert main(["--bench", str(bench), "--start"]) == 1

    error = capsys.readouterr().err
    assert "broken.json" in error
    assert "invalid bench JSON" in error
    assert "Traceback" not in error


def test_load_and_bench_are_mutually_exclusive(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["--load", "instruments", "--bench", "bench.json"])

    assert caught.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_no_flags_still_enters_interactive_menu(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        SCPIEmulatorManager,
        "interactive_mode",
        lambda self: called.append(True),
    )

    assert main([]) == 0
    assert called == [True]
