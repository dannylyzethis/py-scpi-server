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
        "description": "Two model-faithful network analyzers.",
        "metadata": {"site": "remote-lab"},
        "instruments": [
            {
                "id": "pna1",
                "name": "Input PNA",
                "driver": "keysight-pna",
                "model": "N5222B",
                "firmware": "A.20.25.04",
                "configuration": {
                    "mode": "model-faithful",
                    "hardware_configuration": "200",
                    "application_options": ["S93010B"],
                },
                "resource": {
                    "transport": "raw-socket",
                    "host": "127.0.0.1",
                    "port": first_port,
                },
            },
            {
                "id": "pnax1",
                "driver": "keysight-pna",
                "model": "N5242B",
                "configuration": {
                    "hardware_configuration": "425",
                    "application_options": ["S93080B", "S93029B"],
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
    assert definition.instrument("PNA1").configuration["application_options"] == ("S93010B",)
    assert loads_bench(dumps_bench(definition)) == definition

    path = tmp_path / "rf-bench.json"
    save_bench(definition, path)
    assert load_bench(path) == definition


def test_catalog_composition_creates_each_selected_model_and_resource() -> None:
    definition = loads_bench(json.dumps(bench_json(5201, 5202)))
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

    assert composed.instrument("pna1").instrument.process_command("*OPT?") == "200,010"
    assert composed.instrument("pnax1").instrument.process_command("*OPT?") == "425,080,028"
    assert composed.resources() == {
        "pna1": "TCPIP::127.0.0.1::5201::SOCKET",
        "pnax1": "TCPIP::127.0.0.1::5202::SOCKET",
    }
    assert composed.resources(host="ate-host.example") == {
        "pna1": "TCPIP::ate-host.example::5201::SOCKET",
        "pnax1": "TCPIP::ate-host.example::5202::SOCKET",
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["instruments"][1].update(id="pna1"), "ids must be unique"),
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
    unavailable["instruments"][1]["resource"]["transport"] = "vxi-11"
    with pytest.raises(BenchCompositionError, match="planned, not implemented"):
        composer.compose(loads_bench(json.dumps(unavailable)))

    invalid_config = bench_json(5401, 5402)
    invalid_config["instruments"][1]["configuration"] = {"imaginary_option": True}
    with pytest.raises(BenchCompositionError, match="could not compose instrument 'pnax1'"):
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
            "Keysight Technologies,N5222B,"
        )
        assert receive_line(second_port, "*IDN?").startswith(
            "Keysight Technologies,N5242B,"
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
                "pna1",
                "keysight-pna",
                "N5222B",
                ResourceAddress("raw-socket", "127.0.0.1", port),
            ),
            BenchInstrument(
                "pna2",
                "keysight-pna",
                "N5222B",
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

        with pytest.raises(BenchStartError, match="could not bind 'pnax1'"):
            composed.start(bind_host="127.0.0.1")

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", first_port))
