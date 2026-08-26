import json
import socket
from contextlib import closing

import pytest

from scpi_emulator.bench import (
    BenchComposer,
    BenchCompositionError,
    BenchDefinition,
    BenchFormatError,
    BenchInstrument,
    BenchStartError,
    ResourceAddress,
    dumps_bench,
    load_bench,
    loads_bench,
    save_bench,
)
from scpi_emulator.drivers import build_driver_catalog
from scpi_emulator.hislip_transport import HiSLIPServer
from scpi_emulator.vxi11_transport import VXI11Server


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def free_ports(count: int) -> tuple[int, ...]:
    sockets = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return tuple(sock.getsockname()[1] for sock in sockets)
    finally:
        for sock in sockets:
            sock.close()


def receive_line(port: int, command: str) -> str:
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(command.encode("ascii") + b"\n")
        data = b""
        while b"\n" not in data:
            data += client.recv(4096)
        return data.split(b"\n", 1)[0].decode("utf-8")


def bench_json(first_port: int, second_port: int) -> dict:
    return {
        "schema_version": 1,
        "name": "rf-bench",
        "description": "Two generic vector network analyzers.",
        "metadata": {"site": "remote-lab"},
        "instruments": [
            {
                "id": "vna1",
                "name": "Input VNA",
                "driver": "virtual-vna",
                "model": "VNA-2PORT-EMU",
                "firmware": "E.1.0",
                "serial_number": "VNA-001",
                "configuration": {"applications": ["time_domain"]},
                "resource": {
                    "transport": "raw-socket",
                    "host": "127.0.0.1",
                    "port": first_port,
                },
            },
            {
                "id": "vnax1",
                "driver": "virtual-vna",
                "model": "VNA-4PORT-EMU",
                "configuration": {
                    "source_count": 2,
                    "applications": ["frequency_offset", "noise_figure"],
                },
                "resource": {
                    "transport": "raw-socket",
                    "host": "127.0.0.1",
                    "port": second_port,
                },
            },
        ],
    }


def test_versioned_bench_file_round_trips_and_preserves_configuration(tmp_path) -> None:
    definition = loads_bench(json.dumps(bench_json(5101, 5102)))

    assert definition.name == "rf-bench"
    assert definition.metadata == {"site": "remote-lab"}
    assert definition.instrument("VNA1").serial_number == "VNA-001"
    assert definition.instrument("VNA1").configuration["applications"] == ("time_domain",)
    assert loads_bench(dumps_bench(definition)) == definition

    path = tmp_path / "rf-bench.json"
    save_bench(definition, path)
    assert load_bench(path) == definition


def test_catalog_composition_creates_each_selected_model_and_resource() -> None:
    definition = loads_bench(json.dumps(bench_json(5201, 5202)))
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

    assert "PORTS-2" in composed.instrument("vna1").instrument.process_command("*OPT?")
    assert "APP-TIME-DOMAIN" in composed.instrument("vna1").instrument.process_command("*OPT?")
    assert "PORTS-4" in composed.instrument("vnax1").instrument.process_command("*OPT?")
    assert "APP-NOISE-FIGURE" in composed.instrument("vnax1").instrument.process_command("*OPT?")
    assert composed.resources() == {
        "vna1": "TCPIP::127.0.0.1::5201::SOCKET",
        "vnax1": "TCPIP::127.0.0.1::5202::SOCKET",
    }
    assert composed.resources(host="ate-host.example") == {
        "vna1": "TCPIP::ate-host.example::5201::SOCKET",
        "vnax1": "TCPIP::ate-host.example::5202::SOCKET",
    }


def test_bench_vna_frequency_override_defaults_and_failures_are_clear() -> None:
    raw = bench_json(5203, 5204)
    raw["instruments"][0]["configuration"]["frequency_maximum_hz"] = 18_000_000_000
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(
        loads_bench(json.dumps(raw))
    )
    instrument = composed.instrument("vna1").instrument

    assert instrument.process_command("SYST:CAP:FREQ:MIN?") == "10000000"
    assert instrument.process_command("SYST:CAP:FREQ:MAX?") == "18000000000"

    raw["instruments"][0]["configuration"]["frequency_maximum_hz"] = 67_000_000_000
    widened = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(
        loads_bench(json.dumps(raw))
    )
    assert widened.instrument("vna1").instrument.process_command("SYST:CAP:FREQ:MAX?") == (
        "67000000000"
    )

    raw["instruments"][0]["configuration"]["frequency_minimum_hz"] = 68_000_000_000
    with pytest.raises(BenchCompositionError, match="cannot exceed"):
        BenchComposer(build_driver_catalog(discover_plugins=False)).compose(
            loads_bench(json.dumps(raw))
        )


def test_csv_catalog_instrument_can_be_selected_in_a_virtual_bench(tmp_path) -> None:
    (tmp_path / "device.csv").write_text(
        "Equipment,Port,Command,Response,Validation\n"
        'Bench Relay,6101,*IDN?,"SCPI Emulator,Bench Relay,CSV-SERIAL,E.1.0",\n'
        ",,STATE?,OPEN,\n",
        encoding="utf-8",
    )
    definition = BenchDefinition(
        "csv-bench",
        (
            BenchInstrument(
                id="relay1",
                driver="csv-instruments",
                model="bench_relay",
                serial_number="RELAY-001",
                resource=ResourceAddress("raw-socket", "127.0.0.1", 6201),
            ),
        ),
    )

    composed = BenchComposer(
        build_driver_catalog(discover_plugins=False, csv_directory=tmp_path)
    ).compose(definition)

    assert composed.instrument("relay1").instrument.process_command("STATE?") == "OPEN"
    assert composed.instrument("relay1").instrument.process_command("*IDN?") == (
        "SCPI Emulator,Bench Relay,RELAY-001,E.1.0"
    )
    assert composed.resources() == {"relay1": "TCPIP::127.0.0.1::6201::SOCKET"}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["instruments"][1].update(id="vna1"), "ids must be unique"),
        (
            lambda raw: raw["instruments"][1]["resource"].update(
                raw["instruments"][0]["resource"]
            ),
            "resource addresses must be unique",
        ),
        (lambda raw: raw["instruments"][0]["resource"].update(port=70000), "between 1 and"),
    ],
)
def test_invalid_addresses_and_identifiers_fail_before_composition(mutate, message: str) -> None:
    raw = bench_json(5301, 5302)
    mutate(raw)

    with pytest.raises(BenchFormatError, match=message):
        loads_bench(json.dumps(raw))


def test_unknown_driver_model_transport_and_configuration_fail_transactionally() -> None:
    catalog = build_driver_catalog(discover_plugins=False)
    composer = BenchComposer(catalog)

    for field, value, message in (
        ("driver", "missing-driver", "unknown driver"),
        ("model", "N9999Z", "does not support model"),
    ):
        raw = bench_json(5401, 5402)
        raw["instruments"][1][field] = value
        with pytest.raises(BenchCompositionError, match=message):
            composer.compose(loads_bench(json.dumps(raw)))

    unavailable = bench_json(5401, 5402)
    unavailable["instruments"][1]["resource"]["transport"] = "usb"
    with pytest.raises(BenchCompositionError, match="does not advertise"):
        composer.compose(loads_bench(json.dumps(unavailable)))

    invalid_config = bench_json(5401, 5402)
    invalid_config["instruments"][1]["configuration"] = {"imaginary_option": True}
    with pytest.raises(BenchCompositionError, match="could not compose instrument 'vnax1'"):
        composer.compose(loads_bench(json.dumps(invalid_config)))

    valid = composer.compose(loads_bench(json.dumps(bench_json(5401, 5402))))
    assert len(valid.instruments) == 2


def test_same_definition_starts_two_real_socket_instruments_with_bind_override() -> None:
    first_port, second_port = free_ports(2)
    definition = loads_bench(json.dumps(bench_json(first_port, second_port)))
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

    runtime = composed.start(bind_host="127.0.0.1")
    try:
        assert runtime.running is True
        assert receive_line(first_port, "*IDN?").startswith(
            "SCPI Emulator,VNA-2PORT-EMU,"
        )
        assert receive_line(second_port, "*IDN?").startswith(
            "SCPI Emulator,VNA-4PORT-EMU,"
        )
    finally:
        runtime.stop()

    assert runtime.running is False
    assert runtime.servers == {}


def test_bind_override_conflicts_are_rejected_before_any_server_starts() -> None:
    port = free_port()
    definition = BenchDefinition(
        "host-specific",
        (
            BenchInstrument(
                "vna1",
                "virtual-vna",
                "VNA-2PORT-EMU",
                ResourceAddress("raw-socket", "127.0.0.1", port),
            ),
            BenchInstrument(
                "vna2",
                "virtual-vna",
                "VNA-2PORT-EMU",
                ResourceAddress("raw-socket", "localhost", port),
            ),
        ),
    )
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

    with pytest.raises(BenchStartError, match="duplicate endpoint"):
        composed.start(bind_host="127.0.0.1")

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", port))


def test_failed_second_bind_rolls_back_the_first_server() -> None:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        second_port = occupied.getsockname()[1]
        first_port = free_port()
        definition = loads_bench(json.dumps(bench_json(first_port, second_port)))
        composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

        with pytest.raises(BenchStartError, match="could not bind 'vnax1'"):
            composed.start(bind_host="127.0.0.1")

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", first_port))


def test_composed_vxi11_resource_starts_transactionally() -> None:
    portmapper_port = free_port()
    definition = BenchDefinition(
        "vxi-bench",
        (
            BenchInstrument(
                "vna1",
                "virtual-vna",
                "VNA-2PORT-EMU",
                ResourceAddress("vxi-11", "127.0.0.1", portmapper_port),
            ),
        ),
    )
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

    assert composed.resources() == {"vna1": "TCPIP::127.0.0.1::INSTR"}
    runtime = composed.start()
    try:
        assert isinstance(runtime.servers["vna1"], VXI11Server)
        assert runtime.servers["vna1"].running is True
    finally:
        runtime.stop()

    assert runtime.servers == {}


def test_composed_hislip_resource_starts_transactionally() -> None:
    port = free_port()
    definition = BenchDefinition(
        "hislip-bench",
        (
            BenchInstrument(
                "vna1",
                "virtual-vna",
                "VNA-2PORT-EMU",
                ResourceAddress("hislip", "127.0.0.1", port),
            ),
        ),
    )
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

    assert composed.resources() == {
        "vna1": f"TCPIP::127.0.0.1::hislip0,{port}::INSTR"
    }
    runtime = composed.start()
    try:
        assert isinstance(runtime.servers["vna1"], HiSLIPServer)
        assert runtime.servers["vna1"].running is True
    finally:
        runtime.stop()

    assert runtime.servers == {}
