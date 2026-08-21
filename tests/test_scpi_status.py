import pytest

from scpi_emulator.scpi import (
    STATUS_REGISTER_MASK,
    CommandRegistry,
    StandardEventBit,
    StatusByteBit,
    StatusRegisterGroup,
    StatusSystem,
    parse_program_message,
    register_status_commands,
)


def dispatch(registry: CommandRegistry, source: str):
    return registry.dispatch(parse_program_message(source).commands[0])


def test_condition_is_nondestructive_and_event_is_destructive() -> None:
    group = StatusRegisterGroup()
    group.set_condition(0b0101)

    assert group.read_condition() == 0b0101
    assert group.read_condition() == 0b0101
    assert group.read_event() == 0b0101
    assert group.read_event() == 0
    assert group.read_condition() == 0b0101


def test_positive_and_negative_transition_filters_latch_selected_edges() -> None:
    group = StatusRegisterGroup()
    group.set_positive_transition(0b0011)
    group.set_negative_transition(0b1100)

    group.set_condition(0b1111)
    assert group.read_event() == 0b0011

    group.set_condition(0)
    assert group.read_event() == 0b1100


def test_group_summary_requires_both_event_and_enable_bits() -> None:
    group = StatusRegisterGroup()
    group.set_enable(0b0010)
    group.set_condition(0b0001)
    assert group.summary is False

    group.set_condition(0b0011)
    assert group.summary is True

    group.read_event()
    assert group.summary is False


def test_every_status_byte_source_propagates_bit_for_bit() -> None:
    status = StatusSystem()
    status.error_queue.push(-113)
    status.set_event_status_enable(StandardEventBit.COMMAND_ERROR)
    status.questionable.set_enable(0b1)
    status.questionable.set_condition(0b1)
    status.operation.set_enable(0b10)
    status.operation.set_condition(0b10)
    status.set_output_queue_count(1)

    expected_sources = int(
        StatusByteBit.ERROR_QUEUE
        | StatusByteBit.QUESTIONABLE
        | StatusByteBit.MESSAGE_AVAILABLE
        | StatusByteBit.EVENT_STATUS
        | StatusByteBit.OPERATION
    )
    assert status.status_byte == expected_sources

    status.set_service_request_enable(expected_sources)
    assert status.status_byte == expected_sources | StatusByteBit.MASTER_STATUS_SUMMARY
    assert status.requesting_service is True


def test_service_request_enable_bit_six_is_ignored() -> None:
    status = StatusSystem()
    status.set_service_request_enable(255)

    assert status.service_request_enable == 191
    assert not status.requesting_service


def test_esr_read_clears_esb_without_draining_error_queue() -> None:
    status = StatusSystem()
    status.error_queue.push(-222)
    status.set_event_status_enable(StandardEventBit.EXECUTION_ERROR)

    assert status.status_byte == int(StatusByteBit.ERROR_QUEUE | StatusByteBit.EVENT_STATUS)
    assert status.read_event_status() == StandardEventBit.EXECUTION_ERROR
    assert status.status_byte == StatusByteBit.ERROR_QUEUE
    assert len(status.error_queue) == 1


def test_cls_clears_events_and_errors_but_preserves_enables_and_conditions() -> None:
    status = StatusSystem()
    status.set_event_status_enable(255)
    status.set_service_request_enable(255)
    status.error_queue.push(-113)
    for group in (status.operation, status.questionable):
        group.set_enable(3)
        group.set_condition(3)

    status.clear_status()

    assert status.event_status == 0
    assert len(status.error_queue) == 0
    assert status.event_status_enable == 255
    assert status.service_request_enable == 191
    assert status.operation.enable == status.questionable.enable == 3
    assert status.operation.condition == status.questionable.condition == 3
    assert status.operation.event == status.questionable.event == 0


def test_status_preset_resets_group_masks_only() -> None:
    status = StatusSystem()
    status.operation.set_enable(1)
    status.operation.set_condition(1)
    status.operation.set_positive_transition(0)
    status.operation.set_negative_transition(1)

    status.preset()

    assert status.operation.enable == 0
    assert status.operation.positive_transition == STATUS_REGISTER_MASK
    assert status.operation.negative_transition == 0
    assert status.operation.condition == 1
    assert status.operation.event == 1


def test_typed_registry_exposes_ieee_and_status_group_commands() -> None:
    status = StatusSystem()
    registry = CommandRegistry()
    register_status_commands(registry, status)

    assert dispatch(registry, "*ESE 32") == ""
    assert dispatch(registry, "*ESE?") == "32"
    status.latch_event(StandardEventBit.COMMAND_ERROR)
    assert dispatch(registry, "*STB?") == "32"
    assert dispatch(registry, "*ESR?") == "32"
    assert dispatch(registry, "*STB?") == "0"

    assert dispatch(registry, "STAT:OPER:ENAB 4") == ""
    status.operation.set_condition(4)
    assert dispatch(registry, "STAT:OPER:COND?") == "4"
    assert dispatch(registry, "STAT:OPER:EVEN?") == "4"
    assert dispatch(registry, "STAT:OPER:EVEN?") == "0"
    assert dispatch(registry, "STAT:OPER:ENAB?") == "4"


def test_typed_system_error_queries_cannot_be_masked_by_static_profiles() -> None:
    status = StatusSystem()
    registry = CommandRegistry()
    register_status_commands(registry, status)
    status.error_queue.push(-222, "outside configured range")

    assert dispatch(registry, "SYST:ERR:COUN?") == "1"
    assert dispatch(registry, "SYST:ERR?") == '-222,"Data out of range; outside configured range"'
    assert dispatch(registry, "SYST:ERR:COUN?") == "0"
    assert dispatch(registry, "SYST:ERR?") == '0,"No error"'


def test_stb_query_is_nondestructive_and_cls_is_registered() -> None:
    status = StatusSystem()
    registry = CommandRegistry()
    register_status_commands(registry, status)
    status.error_queue.push(-113)

    assert dispatch(registry, "*STB?") == "4"
    assert dispatch(registry, "*STB?") == "4"
    assert dispatch(registry, "*CLS") == ""
    assert dispatch(registry, "*STB?") == "0"


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("set_event_status_enable", -1),
        ("set_event_status_enable", 256),
        ("set_service_request_enable", 256),
        ("set_output_queue_count", -1),
    ],
)
def test_invalid_register_and_queue_values_are_rejected(method: str, value: int) -> None:
    with pytest.raises(ValueError):
        getattr(StatusSystem(), method)(value)


def test_invalid_group_register_values_are_rejected() -> None:
    group = StatusRegisterGroup()
    with pytest.raises(ValueError):
        group.set_enable(STATUS_REGISTER_MASK + 1)
