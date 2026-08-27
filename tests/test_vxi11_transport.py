import socket
import struct
import threading

import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scpi import TriggerSource
from scpi_emulator.vxi11_transport import (
    DEVICE_ASYNC_PROGRAM,
    DEVICE_CORE_PROGRAM,
    DEVICE_INTR_PROGRAM,
    DEVICE_VERSION,
    FLAG_END,
    PORTMAP_PROGRAM,
    PORTMAP_VERSION,
    READ_REASON_END,
    RPC_ACCEPTED,
    RPC_CALL,
    RPC_REPLY,
    RPC_SUCCESS,
    RPC_VERSION,
    VXI_DEVICE_LOCKED,
    VXI_SUCCESS,
    RpcTcpServer,
    VXI11Server,
    XdrReader,
    XdrWriter,
    receive_record,
    send_record,
)


def rpc_call(host, port, program, version, procedure, arguments=b"", xid=1234):
    request = (
        XdrWriter()
        .u32(xid)
        .u32(RPC_CALL)
        .u32(RPC_VERSION)
        .u32(program)
        .u32(version)
        .u32(procedure)
        .u32(0)
        .opaque(b"")
        .u32(0)
        .opaque(b"")
        .build()
        + arguments
    )
    with socket.create_connection((host, port), timeout=2) as client:
        send_record(client, request)
        response = receive_record(client)
    reader = XdrReader(response)
    assert reader.u32() == xid
    assert reader.u32() == RPC_REPLY
    assert reader.u32() == RPC_ACCEPTED
    reader.skip_auth()
    assert reader.u32() == RPC_SUCCESS
    return reader


def create_link(server, *, lock=False):
    arguments = XdrWriter().i32(99).boolean(lock).u32(1000).opaque(b"inst0").build()
    response = rpc_call(
        server.host,
        server.port,
        DEVICE_CORE_PROGRAM,
        DEVICE_VERSION,
        10,
        arguments,
    )
    return response.i32(), response.i32(), response.u32(), response.u32()


def generic(link):
    return XdrWriter().i32(link).i32(0).u32(1000).u32(1000).build()


@pytest.fixture
def running_vxi11():
    instrument = SCPIInstrument("VXI Test", "vxi_test")
    instrument.add_command("VOLT (.+)", "OK", "range:0,10")
    instrument.add_command("VOLT?", "5")
    instrument.link_stateful_commands()
    server = VXI11Server(instrument, portmapper_port=0)
    assert server.start()
    try:
        yield server
    finally:
        server.stop()


def test_portmapper_create_link_query_and_destroy(running_vxi11) -> None:
    server = running_vxi11
    mapping = XdrWriter().u32(DEVICE_CORE_PROGRAM).u32(1).u32(6).u32(0).build()
    response = rpc_call(
        server.host,
        server.portmapper.port,
        PORTMAP_PROGRAM,
        PORTMAP_VERSION,
        3,
        mapping,
    )
    assert response.u32() == server.port

    error, link, abort_port, max_receive = create_link(server)
    assert (error, abort_port, max_receive) == (VXI_SUCCESS, server.async_server.port, 1024 * 1024)

    write = XdrWriter().i32(link).u32(1000).u32(1000).i32(FLAG_END).opaque(b"*IDN?\n").build()
    response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 11, write)
    assert (response.i32(), response.u32()) == (VXI_SUCCESS, 6)
    response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 13, generic(link))
    assert (response.i32(), response.u32()) == (VXI_SUCCESS, 16)

    read = XdrWriter().i32(link).u32(4096).u32(1000).u32(1000).i32(0).u32(0).build()
    response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 12, read)
    assert response.i32() == VXI_SUCCESS
    assert response.i32() & READ_REASON_END
    assert response.opaque().decode().startswith("SCPI_Emulator,VXI Test,vxi_test,")
    response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 13, generic(link))
    assert (response.i32(), response.u32()) == (VXI_SUCCESS, 0)

    response = rpc_call(
        server.host,
        server.port,
        DEVICE_CORE_PROGRAM,
        1,
        23,
        XdrWriter().i32(link).build(),
    )
    assert response.i32() == VXI_SUCCESS


def test_single_link_lock_serial_poll_trigger_and_device_clear(running_vxi11) -> None:
    server = running_vxi11
    error, link, _, _ = create_link(server, lock=True)
    assert error == VXI_SUCCESS
    assert create_link(server)[0] == VXI_DEVICE_LOCKED

    server.instrument.status.set_service_request_enable(4)
    server.instrument.error_queue.push(-113, "test")
    response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 13, generic(link))
    assert response.i32() == VXI_SUCCESS
    assert response.u32() == 68

    server.instrument.acquisition.set_trigger_source(TriggerSource.BUS)
    server.instrument.acquisition.initiate()
    response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 14, generic(link))
    assert response.i32() == VXI_SUCCESS

    server.instrument.state["VOLT"] = "7.5"
    response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 15, generic(link))
    assert response.i32() == VXI_SUCCESS
    assert server.instrument.state["VOLT"] == "7.5"
    assert len(server.instrument.error_queue) == 0


def test_abort_channel_cancels_pending_instrument_work(running_vxi11) -> None:
    server = running_vxi11
    error, link, abort_port, _ = create_link(server)
    assert error == VXI_SUCCESS
    operation = server.instrument.begin_operation("long sweep")

    response = rpc_call(
        server.host,
        abort_port,
        DEVICE_ASYNC_PROGRAM,
        DEVICE_VERSION,
        1,
        XdrWriter().i32(link).build(),
    )

    assert response.i32() == VXI_SUCCESS
    assert operation.done is True


def test_opc_service_request_uses_interrupt_channel(running_vxi11) -> None:
    server = running_vxi11
    error, link, _, _ = create_link(server)
    assert error == VXI_SUCCESS
    received = []
    event = threading.Event()

    def interrupt(procedure, reader):
        assert procedure == 30
        received.append(reader.opaque())
        event.set()
        return b""

    callback = RpcTcpServer(DEVICE_INTR_PROGRAM, 1, interrupt, host="127.0.0.1", port=0)
    callback.start()
    try:
        host = struct.unpack("!I", socket.inet_aton("127.0.0.1"))[0]
        channel = (
            XdrWriter().u32(host).u32(callback.port).u32(DEVICE_INTR_PROGRAM).u32(1).i32(0).build()
        )
        assert rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 25, channel).i32() == 0
        enable = XdrWriter().i32(link).boolean(True).opaque(b"opc-handle").build()
        assert rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 20, enable).i32() == 0

        server.instrument.process_command("*ESE 1")
        server.instrument.process_command("*SRE 32")
        operation = server.instrument.begin_operation("sweep")
        server.instrument.process_command("*OPC")
        operation.complete()

        assert event.wait(2)
        assert received == [b"opc-handle"]
        response = rpc_call(server.host, server.port, DEVICE_CORE_PROGRAM, 1, 13, generic(link))
        assert response.i32() == VXI_SUCCESS
        assert response.u32() == 96
    finally:
        callback.stop()


def test_real_pyvisa_instr_query_clear_trigger_and_serial_poll() -> None:
    pyvisa = pytest.importorskip("pyvisa")
    instrument = SCPIInstrument("PyVISA Test", "pyvisa_test")
    instrument.add_command("VOLT (.+)", "OK", "range:0,10")
    instrument.add_command("VOLT?", "5")
    instrument.link_stateful_commands()
    server = VXI11Server(instrument, portmapper_port=0)
    assert server.start()
    manager = pyvisa.ResourceManager("@py")
    resource = None
    try:
        resource = manager.open_resource(f"TCPIP0::127.0.0.1,{server.port}::inst0::INSTR")
        resource.timeout = 2000
        assert resource.query("*IDN?").startswith("SCPI_Emulator,PyVISA Test,pyvisa_test,")

        resource.write("VOLT 7.5")
        assert resource.read().strip() == "OK"
        resource.clear()
        assert resource.query("VOLT?").strip() == "7.5"

        instrument.status.set_service_request_enable(4)
        instrument.error_queue.push(-113, "serial poll")
        assert resource.read_stb() == 68

        instrument.acquisition.set_trigger_source(TriggerSource.BUS)
        instrument.acquisition.initiate()
        resource.assert_trigger()
    finally:
        if resource is not None:
            resource.close()
        manager.close()
        server.stop()


def test_native_pyvisa_opc_drives_vxi11_service_request() -> None:
    pyvisa = pytest.importorskip("pyvisa")
    try:
        manager = pyvisa.ResourceManager()
    except OSError:
        pytest.skip("a native VISA library is not installed")
    if type(manager.visalib).__module__.startswith("pyvisa_py"):
        manager.close()
        pytest.skip("native VISA is not available")

    instrument = SCPIInstrument("Native VISA SRQ", "native_visa_srq")
    server = VXI11Server(instrument, portmapper_port=111)
    if not server.start():
        manager.close()
        pytest.skip("standard RPC portmapper port 111 is unavailable")
    resource = None
    try:
        resource = manager.open_resource("TCPIP0::127.0.0.1::inst0::INSTR")
        resource.timeout = 2000
        resource.enable_event(
            pyvisa.constants.EventType.service_request,
            pyvisa.constants.EventMechanism.queue,
        )
        resource.write("*ESE 1")
        resource.write("*SRE 32")
        operation = instrument.begin_operation("native VISA sweep")
        resource.write("*OPC")
        operation.complete()

        event = resource.wait_on_event(pyvisa.constants.EventType.service_request, 2000)
        assert event.timed_out is False
        assert resource.read_stb() == 96
    finally:
        if resource is not None:
            resource.close()
        manager.close()
        server.stop()
