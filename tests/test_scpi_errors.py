import pytest

from scpi_emulator.scpi import (
    ErrorCategory,
    ErrorQueue,
    SCPICommandError,
    StandardEventBit,
    classify_error,
    standard_error,
)


@pytest.mark.parametrize(
    ("code", "category", "bit"),
    [
        (-113, ErrorCategory.COMMAND, StandardEventBit.COMMAND_ERROR),
        (-222, ErrorCategory.EXECUTION, StandardEventBit.EXECUTION_ERROR),
        (-310, ErrorCategory.DEVICE, StandardEventBit.DEVICE_DEPENDENT_ERROR),
        (-410, ErrorCategory.QUERY, StandardEventBit.QUERY_ERROR),
    ],
)
def test_errors_are_classified_and_latch_standard_event_bits(
    code: int, category: ErrorCategory, bit: StandardEventBit
) -> None:
    queue = ErrorQueue()

    record = queue.push(code)

    assert classify_error(code) is category
    assert record.category is category
    assert queue.event_status == bit


def test_fifo_responses_and_count_query() -> None:
    queue = ErrorQueue()
    queue.push(-113, "SOUR:UNKNOWN")
    queue.push(-222, "power")

    assert queue.count_response() == "2"
    assert queue.next_response() == '-113,"Undefined header; SOUR:UNKNOWN"'
    assert queue.count_response() == "1"
    assert queue.next_response() == '-222,"Data out of range; power"'
    assert queue.next_response() == '0,"No error"'


def test_queue_overflow_preserves_oldest_errors_and_reports_overflow_last() -> None:
    queue = ErrorQueue(capacity=3)
    queue.push(-100, "first")
    queue.push(-200, "second")
    queue.push(-400, "replaced")

    queue.push(-113, "overflow trigger")
    queue.push(-222, "discarded after overflow")

    assert len(queue) == 3
    assert [queue.next_response(), queue.next_response(), queue.next_response()] == [
        '-100,"Command error; first"',
        '-200,"Execution error; second"',
        '-350,"Queue overflow"',
    ]
    assert queue.event_status == int(
        StandardEventBit.COMMAND_ERROR
        | StandardEventBit.EXECUTION_ERROR
        | StandardEventBit.DEVICE_DEPENDENT_ERROR
        | StandardEventBit.QUERY_ERROR
    )


def test_capacity_one_queue_becomes_overflow_marker() -> None:
    queue = ErrorQueue(capacity=1)
    queue.push(-113)
    queue.push(-222)

    assert queue.next_response() == '-350,"Queue overflow"'


def test_draining_errors_does_not_clear_event_status_but_esr_read_does() -> None:
    queue = ErrorQueue()
    queue.push(-113)

    queue.pop()
    assert queue.event_status == StandardEventBit.COMMAND_ERROR
    assert queue.read_event_status() == StandardEventBit.COMMAND_ERROR
    assert queue.event_status == 0
    assert queue.read_event_status() == 0


def test_clear_can_model_queue_clear_or_full_cls_behavior() -> None:
    queue = ErrorQueue()
    queue.push(-222)
    queue.clear()

    assert len(queue) == 0
    assert queue.event_status == StandardEventBit.EXECUTION_ERROR

    queue.clear(clear_event_status=True)
    assert queue.event_status == 0


def test_non_error_events_and_external_event_sink_are_latched() -> None:
    observed = []
    queue = ErrorQueue(event_sink=observed.append)

    queue.latch(StandardEventBit.POWER_ON | StandardEventBit.OPERATION_COMPLETE)

    assert queue.event_status == int(
        StandardEventBit.POWER_ON | StandardEventBit.OPERATION_COMPLETE
    )
    assert observed == [StandardEventBit.POWER_ON | StandardEventBit.OPERATION_COMPLETE]


def test_registry_errors_can_be_enqueued_without_losing_details() -> None:
    queue = ErrorQueue()

    queue.push(SCPICommandError(-104, "Data type error; power requires numeric data"))

    assert queue.next_response() == '-104,"Data type error; power requires numeric data"'


def test_quotes_are_escaped_in_scpi_response_strings() -> None:
    assert standard_error(-300, 'file "missing"').response == (
        '-300,"Device-specific error; file ""missing"""'
    )


@pytest.mark.parametrize("capacity", [0, -1])
def test_invalid_capacity_is_rejected(capacity: int) -> None:
    with pytest.raises(ValueError, match="at least one"):
        ErrorQueue(capacity)


@pytest.mark.parametrize("code", [1, -99, -500])
def test_nonstandard_error_ranges_require_explicit_records(code: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        standard_error(code)
