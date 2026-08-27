import io
import json
import socket

import pytest

from scpi_emulator import __version__, cli
from scpi_emulator.bench import BenchError, BenchRuntime
from scpi_emulator.cli import build_parser, main
from scpi_emulator.runtime import SCPIEmulatorManager


def test_package_version_is_exposed() -> None:
    assert __version__ == "4.0.0"


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
        "Equipment,Port,Command,Response,Validation\nFirst Device,,VALUE?,ONE,\n",
        encoding="utf-8",
    )
    (definitions / "second.csv").write_text(
        "Equipment,Port,Command,Response,Validation\nSecond Device,,VALUE?,TWO,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(SCPIEmulatorManager, "start_all_servers", _start_manager)
    monkeypatch.setattr(
        SCPIEmulatorManager,
        "start_web_dashboard",
        lambda self, host, port, auth_token=None: True,
    )
    monkeypatch.setattr(cli.time, "sleep", _interrupt_on_sleep)

    assert (
        main(
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
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "first_device: TCPIP::localhost::6200::SOCKET" in output
    assert "second_device: TCPIP::localhost::6201::SOCKET" in output
    assert "Web dashboard: http://127.0.0.1:9090" in output


def test_directory_load_rejects_duplicate_equipment_with_both_filenames(tmp_path, capsys) -> None:
    definitions = tmp_path / "duplicates"
    definitions.mkdir()
    for filename in ("alpha.csv", "beta.csv"):
        (definitions / filename).write_text(
            "Equipment,Port,Command,Response,Validation\nShared Device,,VALUE?,1,\n",
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
        "Equipment,Port,Command,Response,Validation\nBench Relay,,STATE?,OPEN,\n",
        encoding="utf-8",
    )
    bench = tmp_path / "bench.json"
    bench.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": "cli-bench",
                "instruments": [
                    {
                        "id": "meter1",
                        "driver": "virtual-dmm",
                        "model": "dmm",
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
    monkeypatch.setattr(cli.time, "sleep", _interrupt_on_sleep)

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


def _interactive_bench(path, port: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": "interactive-bench",
                "instruments": [
                    {
                        "id": "meter1",
                        "driver": "virtual-dmm",
                        "model": "dmm",
                        "serial_number": "DMM-INTERACTIVE-1",
                        "resource": {
                            "transport": "raw-socket",
                            "host": "127.0.0.1",
                            "port": port,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _interactive_scenario(path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "interactive-dut-cycle",
                "streams": {
                    "voltage.dc": {
                        "kind": "scalar",
                        "advance": "read",
                        "end": "hold-last",
                        "samples": [{"value": 3.3}, {"value": 4.8}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_interactive_loads_quoted_bench_lists_resources_and_controls_runtime(
    tmp_path, monkeypatch, capsys
) -> None:
    folder = tmp_path / "bench definitions"
    folder.mkdir()
    bench = folder / "development bench.json"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    _interactive_bench(bench, port)
    commands = iter(
        [
            f'load bench "{bench}"',
            "instruments",
            "start",
            "status",
            "stop",
            "quit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    manager = SCPIEmulatorManager()
    manager.interactive_mode()

    output = capsys.readouterr().out
    assert f"Bench loaded: {bench}" in output
    assert "meter1: dmm | reports Virtual DMM | serial DMM-INTERACTIVE-1" in output
    assert f"TCPIP::127.0.0.1::{port}::SOCKET" in output
    assert "Active instruments started" in output
    assert "Server state: running" in output
    assert manager.active_running is False


def test_interactive_controls_scenario_while_server_is_running(
    tmp_path, monkeypatch, capsys
) -> None:
    bench = tmp_path / "bench.json"
    scenario_folder = tmp_path / "DUT scenarios"
    scenario_folder.mkdir()
    scenario = scenario_folder / "voltage cycle.json"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    _interactive_bench(bench, port)
    _interactive_scenario(scenario)
    commands = iter(
        [
            f'load bench "{bench}"',
            "start",
            f'scenario load meter1 "{scenario}"',
            "scenario status meter1",
            "scenario start meter1",
            "scenario pause meter1",
            "scenario step meter1 voltage.dc",
            "scenario reset meter1",
            "stop",
            "quit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    manager = SCPIEmulatorManager()
    manager.interactive_mode()

    output = capsys.readouterr().out
    assert "Scenario 'interactive-dut-cycle' loaded for meter1 (paused)" in output
    assert "Scenario meter1: paused | interactive-dut-cycle | seed 0" in output
    assert "Scenario start applied to meter1" in output
    assert "Scenario pause applied to meter1" in output
    assert "Stepped voltage.dc for meter1" in output
    assert "Scenario reset applied to meter1" in output
    instrument = manager.active_instruments["meter1"]["instrument"]
    status = instrument.scenario_control.inspect()
    assert status["state"] == "paused"
    assert status["streams"][0]["index"] == 0


def test_failed_interactive_bench_load_preserves_previous_composition(tmp_path) -> None:
    good = tmp_path / "good.json"
    _interactive_bench(good, 6401)
    broken = tmp_path / "broken.json"
    broken.write_text("{bad-json", encoding="utf-8")
    manager = SCPIEmulatorManager()
    original = manager.load_bench_file(good)

    with pytest.raises(BenchError, match="invalid bench JSON"):
        manager.load_bench_file(broken)

    assert manager.active_runtime is original
    assert manager.configured_instruments()[0]["id"] == "meter1"


def test_bench_plus_interactive_adopts_cli_composition(tmp_path, monkeypatch) -> None:
    bench = tmp_path / "bench.json"
    _interactive_bench(bench, 6402)
    seen = []
    monkeypatch.setattr(
        SCPIEmulatorManager,
        "interactive_mode",
        lambda self: seen.extend(self.configured_instruments()),
    )

    assert main(["--bench", str(bench), "--interactive"]) == 0
    assert [row["id"] for row in seen] == ["meter1"]


def test_running_bench_output_is_safe_for_ascii_only_windows_streams(tmp_path, monkeypatch) -> None:
    bench = tmp_path / "bench.json"
    _interactive_bench(bench, 6403)
    monkeypatch.setattr(BenchRuntime, "start", lambda self: None)
    monkeypatch.setattr(cli.time, "sleep", _interrupt_on_sleep)
    raw_output = io.BytesIO()
    ascii_output = io.TextIOWrapper(raw_output, encoding="ascii", errors="strict")
    monkeypatch.setattr(cli.sys, "stdout", ascii_output)

    assert main(["--bench", str(bench), "--start"]) == 0

    ascii_output.flush()
    rendered = raw_output.getvalue().decode("ascii")
    assert "SCPI Emulator running!" in rendered
    assert "VISA resources:" in rendered


def test_interactive_catalog_lists_drivers_and_describes_model_contract(
    monkeypatch, capsys
) -> None:
    commands = iter(
        [
            "catalog",
            "catalog virtual-vna",
            "catalog virtual-vna vna-2-port",
            "quit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    SCPIEmulatorManager().interactive_mode()

    output = capsys.readouterr().out
    assert "Driver catalog:" in output
    assert "virtual-vna: Virtual Vector Network Analyzer" in output
    assert "Driver virtual-vna:" in output
    assert "Model vna-2-port:" in output
    assert "frequency_maximum_hz: number, default 50000000000" in output
    assert "Hardware features: 8" in output
    assert "Command coverage: 393/393 (100.0%)" in output


def test_interactive_catalog_can_include_csv_models_from_quoted_folder(
    tmp_path, monkeypatch, capsys
) -> None:
    folder = tmp_path / "CSV catalog"
    folder.mkdir()
    (folder / "fixtures.csv").write_text(
        "Equipment,Port,Command,Response,Validation\nBench Relay,,STATE?,OPEN,\n",
        encoding="utf-8",
    )
    commands = iter([f'catalog csv "{folder}"', "quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    SCPIEmulatorManager().interactive_mode()

    output = capsys.readouterr().out
    assert f"Included CSV instruments from {folder}" in output
    assert "Driver csv-instruments: CSV instruments" in output
    assert "bench_relay: Bench Relay" in output


def test_interactive_create_bench_saves_and_loads_active_composition(
    tmp_path, monkeypatch, capsys
) -> None:
    target = tmp_path / "created bench.json"
    commands = iter(
        [
            f'create bench "{target}"',
            "",
            "virtual-dmm",
            "dmm",
            "meter1",
            "",
            "",
            "",
            "",
            "",
            "",
            "n",
            "y",
            "instruments",
            "quit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    manager = SCPIEmulatorManager()
    manager.interactive_mode()

    output = capsys.readouterr().out
    assert target.exists()
    assert f"Bench saved and loaded: {target}" in output
    assert "meter1: dmm | reports Virtual DMM | serial EMU-METER1" in output
    assert manager.configured_instruments()[0]["id"] == "meter1"
