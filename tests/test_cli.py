from scpi_emulator import __version__
from scpi_emulator.emulator import build_parser, main


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

    assert "Stateful SCPI instrument emulator" in capsys.readouterr().out


def test_version_exits_successfully(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("argparse version should terminate through SystemExit")

    assert capsys.readouterr().out.rstrip().endswith(__version__)
