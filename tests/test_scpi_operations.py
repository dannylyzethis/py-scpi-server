from queue import Empty, Queue
from threading import Event, Thread

import pytest

from scpi_emulator.scpi import (
    CommandRegistry,
    OperationManager,
    OperationState,
    OperationStateError,
    OperationTimeout,
    StandardEventBit,
    StatusByteBit,
    StatusSystem,
    parse_program_message,
    register_operation_commands,
    register_status_commands,
)


def dispatch(registry: CommandRegistry, source: str):
    return registry.dispatch(parse_program_message(source).commands[0])


def test_opc_latches_only_after_operations_before_its_fence_complete() -> None:
    status = StatusSystem()
    operations = OperationManager(status)
    first = operations.begin("sweep 1")

    assert operations.opc() == ""
    later = operations.begin("sweep 2")
    assert status.event_status == 0

    first.complete()

    assert status.event_status == StandardEventBit.OPERATION_COMPLETE
    assert later.state is OperationState.PENDING


def test_opc_event_propagates_through_ese_esb_sre_and_mss() -> None:
    status = StatusSystem()
    status.set_event_status_enable(StandardEventBit.OPERATION_COMPLETE)
    status.set_service_request_enable(StatusByteBit.EVENT_STATUS)
    operations = OperationManager(status)
    sweep = operations.begin("sweep")
    operations.opc()

    assert status.status_byte == 0

    sweep.complete()

    assert status.event_status == StandardEventBit.OPERATION_COMPLETE
    assert status.status_byte == int(
        StatusByteBit.EVENT_STATUS | StatusByteBit.MASTER_STATUS_SUMMARY
    )
    assert status.requesting_service is True

    assert status.read_event_status() == StandardEventBit.OPERATION_COMPLETE
    assert status.status_byte == 0


def test_opc_with_no_pending_work_latches_immediately() -> None:
    status = StatusSystem()
    operations = OperationManager(status)

    operations.opc()

    assert status.event_status == StandardEventBit.OPERATION_COMPLETE


def test_opc_query_blocks_then_returns_one_without_latching_opc() -> None:
    status = StatusSystem()
    operations = OperationManager(status)
    sweep = operations.begin("sweep")
    started = Event()
    responses: Queue[str] = Queue()

    def query() -> None:
        started.set()
        responses.put(operations.opc_query())

    thread = Thread(target=query)
    thread.start()
    assert started.wait(1)
    with pytest.raises(Empty):
        responses.get(timeout=0.05)

    sweep.complete()
    assert responses.get(timeout=1) == "1"
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert status.event_status == 0


def test_wai_is_a_barrier_without_response_or_event_bit() -> None:
    status = StatusSystem()
    operations = OperationManager(status)
    calibration = operations.begin("calibration")
    passed_barrier = Event()

    def wait_then_continue() -> None:
        operations.wai()
        passed_barrier.set()

    thread = Thread(target=wait_then_continue)
    thread.start()
    assert not passed_barrier.wait(0.05)

    calibration.complete()
    assert passed_barrier.wait(1)
    thread.join(timeout=1)
    assert status.event_status == 0


def test_abort_cancels_work_and_releases_opc_query_and_opc_fence() -> None:
    status = StatusSystem()
    operations = OperationManager(status)
    first = operations.begin("sweep")
    second = operations.begin("file operation")
    operations.opc()

    assert operations.abort() == (first.identifier, second.identifier)

    assert first.state is second.state is OperationState.CANCELLED
    assert operations.pending_count == 0
    assert operations.opc_query(timeout=0.01) == "1"
    assert status.event_status == StandardEventBit.OPERATION_COMPLETE


def test_failed_operation_is_terminal_and_queues_device_error() -> None:
    status = StatusSystem()
    operations = OperationManager(status)
    operation = operations.begin("calibration")
    operations.opc()

    operation.fail("calibration failed")

    assert operation.state is OperationState.FAILED
    assert status.error_queue.next_response() == (
        '-300,"Device-specific error; calibration failed"'
    )
    assert status.event_status == int(
        StandardEventBit.OPERATION_COMPLETE | StandardEventBit.DEVICE_DEPENDENT_ERROR
    )


def test_terminal_transitions_are_idempotent_or_deterministically_rejected() -> None:
    operations = OperationManager(StatusSystem())
    operation = operations.begin("sweep")

    assert operation.complete() is True
    assert operation.complete() is False
    with pytest.raises(OperationStateError, match="already completed"):
        operation.cancel()


def test_wait_timeout_does_not_cancel_operation() -> None:
    operations = OperationManager(StatusSystem())
    operation = operations.begin("sweep")

    with pytest.raises(OperationTimeout):
        operations.opc_query(timeout=0.01)

    assert operation.state is OperationState.PENDING


def test_typed_commands_keep_opc_query_and_wai_semantics_distinct() -> None:
    status = StatusSystem()
    operations = OperationManager(status)
    registry = CommandRegistry()
    register_status_commands(registry, status)
    register_operation_commands(registry, operations)

    assert dispatch(registry, "*ESE 1") == ""
    assert dispatch(registry, "*SRE 32") == ""
    assert dispatch(registry, "*OPC") == ""
    assert dispatch(registry, "*STB?") == "96"
    assert dispatch(registry, "*ESR?") == "1"
    assert dispatch(registry, "*STB?") == "0"
    assert dispatch(registry, "*OPC?") == "1"
    assert status.event_status == 0
    assert dispatch(registry, "*WAI") == ""
