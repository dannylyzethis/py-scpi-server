import socket
import struct

import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.hislip_transport import (
    ASYNC_DEVICE_CLEAR,
    ASYNC_DEVICE_CLEAR_ACKNOWLEDGE,
    ASYNC_INITIALIZE,
    ASYNC_INITIALIZE_RESPONSE,
    ASYNC_LOCK,
    ASYNC_LOCK_INFO,
    ASYNC_LOCK_INFO_RESPONSE,
    ASYNC_LOCK_RESPONSE,
    ASYNC_MAX_MSG_SIZE,
    ASYNC_MAX_MSG_SIZE_RESPONSE,
    ASYNC_SERVICE_REQUEST,
    ASYNC_STATUS_QUERY,
    ASYNC_STATUS_RESPONSE,
    DATA_END,
    DEVICE_CLEAR_ACKNOWLEDGE,
    DEVICE_CLEAR_COMPLETE,
    ERROR,
    ERROR_MESSAGE_TOO_LARGE,
    FATAL_ERROR,
    FATAL_MAX_CLIENTS,
    HEADER,
    INITIALIZE,
    INITIALIZE_RESPONSE,
    PROTOCOL_VERSION,
    TRIGGER,
    HiSLIPMessage,
    HiSLIPServer,
    receive_message,
    send_message,
)
from scpi_emulator.scpi import TriggerSource


@pytest.fixture
def running_hislip():
    instrument = SCPIInstrument("HiSLIP Test", "hislip_test")
    instrument.add_command("VOLT (.+)", "OK", "range:0,10")
    instrument.add_command("VOLT?", "5")
    instrument.link_stateful_commands()
    server = HiSLIPServer(instrument, port=0)
    assert server.start()
    try:
        yield server
    finally:
        server.stop()


def open_channels(server):
    sync = socket.create_connection((server.host, server.port), timeout=2)
    sync.settimeout(2)
    parameter = (1 << 24) | int.from_bytes(b"IV", "big")
    send_message(sync, HiSLIPMessage(INITIALIZE, parameter=parameter, payload=b"hislip0"))
    initialized = receive_message(sync)
    assert initialized.message_type == INITIALIZE_RESPONSE
    assert initialized.parameter >> 16 == PROTOCOL_VERSION
    session_id = initialized.parameter & 0xFFFF

    asynchronous = socket.create_connection((server.host, server.port), timeout=2)
    asynchronous.settimeout(2)
    send_message(asynchronous, HiSLIPMessage(ASYNC_INITIALIZE, parameter=session_id))
    response = receive_message(asynchronous)
    assert response.message_type == ASYNC_INITIALIZE_RESPONSE
    assert response.parameter.to_bytes(4, "big") == b"SCPI"
    return sync, asynchronous


def exchange(sync, command, message_id=0xFFFFFF00):
    send_message(sync, HiSLIPMessage(DATA_END, parameter=message_id, payload=command))
    response = receive_message(sync)
    assert response.message_type == DATA_END
    assert response.parameter == message_id
    return response.payload


def test_initialization_query_maximum_size_lock_status_and_single_session(running_hislip):
    sync, asynchronous = open_channels(running_hislip)
    extra = socket.create_connection((running_hislip.host, running_hislip.port), timeout=2)
    try:
        parameter = (1 << 24) | int.from_bytes(b"IV", "big")
        send_message(extra, HiSLIPMessage(INITIALIZE, parameter=parameter, payload=b"hislip0"))
        refused = receive_message(extra)
        assert (refused.message_type, refused.control_code) == (FATAL_ERROR, FATAL_MAX_CLIENTS)

        send_message(
            asynchronous,
            HiSLIPMessage(ASYNC_MAX_MSG_SIZE, payload=struct.pack("!Q", 2 * 1024 * 1024)),
        )
        maximum = receive_message(asynchronous)
        assert maximum.message_type == ASYNC_MAX_MSG_SIZE_RESPONSE
        assert struct.unpack("!Q", maximum.payload)[0] == 1024 * 1024

        assert exchange(sync, b"*IDN?\n").startswith(b"SCPI_Emulator,HiSLIP Test,hislip_test,")

        send_message(asynchronous, HiSLIPMessage(ASYNC_LOCK, control_code=1))
        assert receive_message(asynchronous).control_code == 1
        send_message(asynchronous, HiSLIPMessage(ASYNC_LOCK_INFO))
        lock_info = receive_message(asynchronous)
        assert (lock_info.message_type, lock_info.control_code, lock_info.parameter) == (
            ASYNC_LOCK_INFO_RESPONSE,
            1,
            1,
        )
        send_message(asynchronous, HiSLIPMessage(ASYNC_LOCK, control_code=0))
        assert receive_message(asynchronous).message_type == ASYNC_LOCK_RESPONSE

        running_hislip.instrument.status.set_service_request_enable(4)
        running_hislip.instrument.error_queue.push(-113, "serial poll")
        send_message(asynchronous, HiSLIPMessage(ASYNC_STATUS_QUERY, parameter=0xFFFFFF00))
        status = receive_message(asynchronous)
        assert (status.message_type, status.control_code) == (ASYNC_STATUS_RESPONSE, 68)
    finally:
        extra.close()
        asynchronous.close()
        sync.close()


def test_clear_trigger_srq_and_oversized_message(running_hislip):
    sync, asynchronous = open_channels(running_hislip)
    try:
        running_hislip.instrument.state["VOLT"] = "7.5"
        running_hislip.instrument.error_queue.push(-113, "clear me")
        send_message(asynchronous, HiSLIPMessage(ASYNC_DEVICE_CLEAR))
        assert receive_message(asynchronous).message_type == ASYNC_DEVICE_CLEAR_ACKNOWLEDGE
        send_message(sync, HiSLIPMessage(DEVICE_CLEAR_COMPLETE))
        assert receive_message(sync).message_type == DEVICE_CLEAR_ACKNOWLEDGE
        assert running_hislip.instrument.state["VOLT"] == "7.5"
        assert len(running_hislip.instrument.error_queue) == 0

        running_hislip.instrument.acquisition.set_trigger_source(TriggerSource.BUS)
        running_hislip.instrument.acquisition.initiate()
        send_message(sync, HiSLIPMessage(TRIGGER, parameter=0xFFFFFF02))

        send_message(sync, HiSLIPMessage(DATA_END, parameter=0xFFFFFF02, payload=b"*ESE 1"))
        send_message(sync, HiSLIPMessage(DATA_END, parameter=0xFFFFFF04, payload=b"*SRE 32"))
        operation = running_hislip.instrument.begin_operation("sweep")
        send_message(sync, HiSLIPMessage(DATA_END, parameter=0xFFFFFF06, payload=b"*OPC"))
        operation.complete()
        service_request = receive_message(asynchronous)
        assert service_request.message_type == ASYNC_SERVICE_REQUEST
        assert service_request.control_code == 96
    finally:
        asynchronous.close()
        sync.close()

    oversized = socket.create_connection((running_hislip.host, running_hislip.port), timeout=2)
    oversized.settimeout(2)
    try:
        oversized.sendall(HEADER.pack(b"HS", INITIALIZE, 0, 0, 1024 * 1024 + 1))
        response = receive_message(oversized)
        assert (response.message_type, response.control_code) == (ERROR, ERROR_MESSAGE_TOO_LARGE)
    finally:
        oversized.close()


def test_real_pyvisa_hislip_query_clear_trigger_and_serial_poll(running_hislip) -> None:
    pyvisa = pytest.importorskip("pyvisa")
    manager = pyvisa.ResourceManager("@py")
    resource = None
    try:
        resource = manager.open_resource(f"TCPIP0::127.0.0.1::hislip0,{running_hislip.port}::INSTR")
        resource.timeout = 2000
        assert resource.query("*IDN?").startswith("SCPI_Emulator,HiSLIP Test,hislip_test,")
        resource.write("VOLT 7.5")
        assert resource.read().strip() == "OK"
        resource.clear()
        assert resource.query("VOLT?").strip() == "7.5"

        running_hislip.instrument.status.set_service_request_enable(4)
        running_hislip.instrument.error_queue.push(-113, "serial poll")
        assert resource.read_stb() == 68

        running_hislip.instrument.acquisition.set_trigger_source(TriggerSource.BUS)
        running_hislip.instrument.acquisition.initiate()
        # PyVISA-Py 0.8 does not yet expose assert_trigger on its HiSLIP
        # session, so exercise the same client protocol implementation directly.
        resource.visalib.sessions[resource.session].interface.trigger()
    finally:
        if resource is not None:
            resource.close()
        manager.close()


def test_native_pyvisa_hislip_query_status_and_opc_service_request() -> None:
    pyvisa = pytest.importorskip("pyvisa")
    try:
        manager = pyvisa.ResourceManager()
    except OSError:
        pytest.skip("a native VISA library is not installed")
    if type(manager.visalib).__module__.startswith("pyvisa_py"):
        manager.close()
        pytest.skip("native VISA is not available")

    instrument = SCPIInstrument("Native HiSLIP", "native_hislip")
    server = HiSLIPServer(instrument, port=0)
    assert server.start()
    resource = None
    try:
        resource = manager.open_resource(f"TCPIP0::127.0.0.1::hislip0,{server.port}::INSTR")
        resource.timeout = 2000
        assert resource.query("*IDN?").startswith("SCPI_Emulator,Native HiSLIP,native_hislip,")
        assert resource.read_stb() == 0

        resource.enable_event(
            pyvisa.constants.EventType.service_request,
            pyvisa.constants.EventMechanism.queue,
        )
        resource.write("*ESE 1")
        resource.write("*SRE 32")
        operation = instrument.begin_operation("native HiSLIP sweep")
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
