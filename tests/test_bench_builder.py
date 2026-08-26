import json

import pytest

from scpi_emulator.bench import (
    BenchBuildCancelled,
    BenchComposer,
    GuidedBenchBuilder,
    load_bench,
)
from scpi_emulator.drivers import build_driver_catalog


def answers(*values):
    selected = iter(values)
    return lambda _prompt: next(selected)


def test_guided_builder_creates_atomic_mixed_bench_with_safe_defaults(tmp_path) -> None:
    (tmp_path / "relay.csv").write_text(
        "Equipment,Port,Command,Response,Validation\n"
        "Bench Relay,,STATE?,OPEN,\n",
        encoding="utf-8",
    )
    target = tmp_path / "mixed bench.json"
    catalog = build_driver_catalog(discover_plugins=False, csv_directory=tmp_path)
    builder = GuidedBenchBuilder(
        catalog,
        input_fn=answers(
            "",
            "virtual-ps",
            "ps-3-output",
            "psu1",
            "",
            "",
            "",
            "",
            "",
            "",
            "y",
            "csv-instruments",
            "bench_relay",
            "relay1",
            "",
            "",
            "",
            "",
            "",
            "",
            "n",
            "y",
        ),
        output_fn=lambda _message: None,
    )

    composed = builder.build_and_save(target)
    definition = load_bench(target)

    assert definition.name == "mixed bench"
    assert [item.id for item in definition.instruments] == ["psu1", "relay1"]
    assert [item.serial_number for item in definition.instruments] == [
        "EMU-PSU1",
        "EMU-RELAY1",
    ]
    assert composed.resources() == {
        "psu1": "TCPIP::127.0.0.1::5025::SOCKET",
        "relay1": "TCPIP::127.0.0.1::5026::SOCKET",
    }
    assert BenchComposer(catalog).compose(definition).resources() == composed.resources()
    assert list(tmp_path.glob(".mixed bench.json.*.tmp")) == []


def test_guided_builder_makes_all_applications_a_single_choice(tmp_path) -> None:
    target = tmp_path / "vna.json"
    builder = GuidedBenchBuilder(
        build_driver_catalog(discover_plugins=False),
        input_fn=answers(
            "development-vna",
            "virtual-vna",
            "vna-2-port",
            "vna1",
            "",
            "",
            "",
            "",
            "",
            "",
            "n",
            "n",
            "y",
        ),
        output_fn=lambda _message: None,
    )

    builder.build_and_save(target)
    instrument = load_bench(target).instruments[0]

    assert instrument.configuration == {}
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(
        load_bench(target)
    )
    assert "APP-TIME-DOMAIN" in composed.instrument("vna1").instrument.process_command(
        "*OPT?"
    )


def test_advanced_frequency_prompts_accept_scientific_notation_and_retry_ranges(
    tmp_path,
) -> None:
    target = tmp_path / "limited-vna.json"
    messages = []
    builder = GuidedBenchBuilder(
        build_driver_catalog(discover_plugins=False),
        input_fn=answers(
            "limited-vna",
            "virtual-vna",
            "vna-2-port",
            "vna1",
            "",
            "",
            "",
            "",
            "",
            "",
            "y",
            "1.5",
            "2",
            "",
            "",
            "1e8",
            "20e9",
            "n",
            "y",
        ),
        output_fn=messages.append,
    )

    builder.build_and_save(target)
    configuration = load_bench(target).instruments[0].configuration

    assert configuration["source_count"] == 2
    assert configuration["frequency_minimum_hz"] == 100_000_000
    assert configuration["frequency_maximum_hz"] == 20_000_000_000
    assert any("source_count must be an integer" in message for message in messages)


def test_cancel_never_creates_or_replaces_a_partial_bench(tmp_path) -> None:
    target = tmp_path / "cancelled.json"
    builder = GuidedBenchBuilder(
        build_driver_catalog(discover_plugins=False),
        input_fn=answers("", "cancel"),
        output_fn=lambda _message: None,
    )

    with pytest.raises(BenchBuildCancelled, match="cancelled"):
        builder.build_and_save(target)
    assert not target.exists()


def test_saved_bench_json_is_schema_version_two(tmp_path) -> None:
    target = tmp_path / "meter.json"
    builder = GuidedBenchBuilder(
        build_driver_catalog(discover_plugins=False),
        input_fn=answers(
            "meter-bench",
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
        ),
        output_fn=lambda _message: None,
    )

    builder.build_and_save(target)

    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 2
