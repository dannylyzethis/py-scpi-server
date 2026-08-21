import socket
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from scpi_emulator.emulator import SCPIInstrument, SCPIServer


def receive_lines(client: socket.socket, count: int) -> list[str]:
    data = b""
    deadline = time.monotonic() + 3
    while data.count(b"\n") < count and time.monotonic() < deadline:
        data += client.recv(4096)
    return data.decode("utf-8").splitlines()


@pytest.fixture
def running_server():
    instrument = SCPIInstrument("Socket Test", "socket_test")
    instrument.add_command("VOLT (.+)", "OK", "range:0,10")
    instrument.add_command("VOLT?", "5")
    instrument.link_stateful_commands()
    manager = SimpleNamespace(web_dashboard=None)
    server = SCPIServer(instrument, manager, host="127.0.0.1", port=0)
    assert server.start()
    port = server.socket.getsockname()[1]
    try:
        yield server, port
    finally:
        server.stop()
        if server.thread:
            server.thread.join(timeout=1)


def test_terminated_and_fragmented_commands(running_server) -> None:
    _, port = running_server
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(b"*ID")
        client.sendall(b"N?\r\nSYST:VERS?\n")

        responses = receive_lines(client, 2)

    assert responses == ["SCPI_Emulator,Socket Test,socket_test,2.3.0", "1999.0"]


def test_legacy_timeout_processes_unterminated_command(running_server) -> None:
    _, port = running_server
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(b"SYST:VERS?")

        responses = receive_lines(client, 1)

    assert responses == ["1999.0"]


def test_cls_preserves_values_and_connection_remains_responsive(running_server) -> None:
    _, port = running_server
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(b"VOLT 7.5\n")
        assert receive_lines(client, 1) == ["OK"]

        client.sendall(b"NOT:A:COMMAND\n*CLS\nVOLT?\nSYST:ERR?\n*IDN?\n")
        responses = receive_lines(client, 3)

    assert responses == [
        "7.5",
        '0,"No error"',
        "SCPI_Emulator,Socket Test,socket_test,2.3.0",
    ]


def test_opc_event_handshake_propagates_to_status_byte_over_socket(running_server) -> None:
    server, port = running_server
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(b"*IDN?\n")
        assert receive_lines(client, 1)[0].startswith("SCPI_Emulator,")
        sweep = server.instrument.begin_operation("sweep")

        client.sendall(b"*ESE 1\n*SRE 32\n*OPC\n*STB?\n")
        assert receive_lines(client, 1) == ["0"]

        sweep.complete()
        client.sendall(b"*STB?\n*ESR?\n*STB?\n")
        assert receive_lines(client, 3) == ["96", "1", "0"]


def test_opc_query_waits_for_prior_socket_operation_without_setting_event(running_server) -> None:
    server, port = running_server
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(b"*IDN?\n")
        assert receive_lines(client, 1)[0].startswith("SCPI_Emulator,")
        sweep = server.instrument.begin_operation("sweep")

        client.sendall(b"*OPC?\n")
        client.settimeout(0.1)
        with pytest.raises(socket.timeout):
            client.recv(4096)

        sweep.complete()
        client.settimeout(2)
        assert receive_lines(client, 1) == ["1"]
        client.sendall(b"*ESR?\n")
        assert receive_lines(client, 1) == ["0"]


def test_bus_triggered_acquisition_drives_real_opc_handshake(running_server) -> None:
    _, port = running_server
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(b"*IDN?\n")
        assert receive_lines(client, 1)[0].startswith("SCPI_Emulator,")

        client.sendall(
            b"TRIG:SOUR BUS\n"
            b"SENS:SWE:TIME 0.02\n"
            b"*ESE 1\n"
            b"*SRE 32\n"
            b"INIT:IMM\n"
            b"*OPC\n"
            b"*STB?\n"
        )
        assert receive_lines(client, 1) == ["0"]

        client.sendall(b"*TRG\n*OPC?\n*STB?\n*ESR?\n*STB?\n")
        assert receive_lines(client, 4) == ["1", "96", "1", "0"]
