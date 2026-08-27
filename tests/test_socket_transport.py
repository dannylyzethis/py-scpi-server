import socket
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.raw_server import SCPIServer
from scpi_emulator.scpi import (
    BinaryResponse,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
)
from scpi_emulator.socket_transport import (
    MessageTooLarge,
    SocketMessageFramer,
    SocketTransportConfig,
)


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

    assert responses == ["SCPI_Emulator,Socket Test,socket_test,E.1.0", "1999.0"]


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
        "SCPI_Emulator,Socket Test,socket_test,E.1.0",
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


def test_large_binary_response_survives_fragmented_socket_reads(running_server) -> None:
    server, port = running_server
    payload = bytes(range(256)) * 4096
    expected = BinaryResponse(payload).encode() + b"\n"
    server.instrument.add_binary_query("CALC:DATA?", payload)

    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as client:
        client.settimeout(2)
        client.sendall(b"CALC:DATA?\n")
        received = bytearray()
        while len(received) < len(expected):
            received.extend(client.recv(min(997, len(expected) - len(received))))

    assert bytes(received) == expected


def test_framer_preserves_terminators_in_quotes_and_definite_blocks() -> None:
    framer = SocketMessageFramer(SocketTransportConfig())

    assert framer.feed(b'DISP:TEXT "first\nsecond"\nMMEM:DATA #15a\r\nb') == (
        b'DISP:TEXT "first\nsecond"',
    )
    assert framer.feed(b'c\n*IDN?\r\n') == (b'MMEM:DATA #15a\r\nbc', b'*IDN?')


def test_framer_enforces_per_message_bound_without_rejecting_command_chains() -> None:
    config = SocketTransportConfig(max_message_size=8)
    framer = SocketMessageFramer(config)

    assert framer.feed(b"A\nB\nC\nD\nE\n") == (b"A", b"B", b"C", b"D", b"E")
    with pytest.raises(MessageTooLarge):
        framer.feed(b"123456789")

    with pytest.raises(MessageTooLarge):
        SocketMessageFramer(config).feed(b"DATA #299")


def test_configurable_read_and_write_termination() -> None:
    instrument = SCPIInstrument("Terminator Test", "terminator_test")
    manager = SimpleNamespace(web_dashboard=None)
    config = SocketTransportConfig(
        read_terminations=(b"\0",),
        write_termination=b"\r\n",
        idle_frame_timeout=None,
    )
    server = SCPIServer(
        instrument,
        manager,
        host="127.0.0.1",
        port=0,
        transport_config=config,
    )
    assert server.start()
    try:
        with closing(socket.create_connection(("127.0.0.1", server.port), timeout=2)) as client:
            client.settimeout(2)
            client.sendall(b"SYST:VERS?\0")
            assert client.recv(64) == b"1999.0\r\n"
    finally:
        server.stop()


def test_definite_binary_command_is_dispatched_without_text_decoding() -> None:
    instrument = SCPIInstrument("Binary Input", "binary_input")
    instrument.core_registry.register(
        CommandSpec(
            path=(HeaderNode("MMEMory"), HeaderNode("DATA")),
            parameters=(ParameterSpec(ParameterType.BINARY, "data"),),
            handler=lambda invocation, data: str(len(data)),
        )
    )
    manager = SimpleNamespace(web_dashboard=None)
    server = SCPIServer(instrument, manager, host="127.0.0.1", port=0)
    assert server.start()
    payload = b"a\n\r;\xff"
    block = b"#1" + str(len(payload)).encode("ascii") + payload
    try:
        with closing(socket.create_connection(("127.0.0.1", server.port), timeout=2)) as client:
            client.settimeout(2)
            client.sendall(b"MMEM:DATA " + block + b"\n")
            assert client.recv(64) == b"5\n"
    finally:
        server.stop()


def test_additional_client_is_rejected_while_session_is_active(running_server) -> None:
    _, port = running_server
    with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as first:
        first.settimeout(2)
        first.sendall(b"*IDN?\n")
        assert receive_lines(first, 1)[0].startswith("SCPI_Emulator,")

        with closing(socket.create_connection(("127.0.0.1", port), timeout=2)) as second:
            second.settimeout(2)
            second.sendall(b"SYST:VERS?\n")
            try:
                rejected = second.recv(64)
            except (ConnectionAbortedError, ConnectionResetError):
                rejected = b""
            assert rejected == b""

        first.sendall(b"SYST:VERS?\n")
        assert receive_lines(first, 1) == ["1999.0"]


def test_idle_session_closes_and_releases_instrument() -> None:
    instrument = SCPIInstrument("Idle Test", "idle_test")
    manager = SimpleNamespace(web_dashboard=None)
    config = SocketTransportConfig(client_idle_timeout=0.15)
    server = SCPIServer(
        instrument,
        manager,
        host="127.0.0.1",
        port=0,
        transport_config=config,
    )
    assert server.start()
    try:
        with closing(socket.create_connection(("127.0.0.1", server.port), timeout=2)) as first:
            first.settimeout(2)
            assert first.recv(64) == b""
        with closing(socket.create_connection(("127.0.0.1", server.port), timeout=2)) as second:
            second.settimeout(2)
            second.sendall(b"SYST:VERS?\n")
            assert second.recv(64) == b"1999.0\n"
    finally:
        server.stop()


def test_stop_closes_active_session_and_listener(running_server) -> None:
    server, port = running_server
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    client.settimeout(2)
    client.sendall(b"*IDN?\n")
    assert receive_lines(client, 1)

    server.stop()

    assert client.recv(64) == b""
    assert not server.thread.is_alive()
    assert not server._client_thread.is_alive()
    client.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2)


def test_stalled_reader_is_bounded_by_send_timeout() -> None:
    class StalledClient:
        def __init__(self) -> None:
            self.timeouts = []

        def settimeout(self, value) -> None:
            self.timeouts.append(value)

        def sendall(self, data) -> None:
            raise socket.timeout("simulated stalled reader")

    instrument = SCPIInstrument("Backpressure Test", "backpressure_test")
    config = SocketTransportConfig(send_timeout=0.25)
    server = SCPIServer(
        instrument,
        SimpleNamespace(web_dashboard=None),
        transport_config=config,
    )
    client = StalledClient()

    with pytest.raises(socket.timeout, match="stalled reader"):
        server._execute_message(client, b"*IDN?")

    assert client.timeouts == [0.25]
