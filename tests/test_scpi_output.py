import struct

import pytest

from scpi_emulator.scpi import (
    BinaryResponse,
    ByteOrder,
    CommandRegistry,
    DataEncoding,
    DataFormat,
    OutputQueue,
    OutputQueueFull,
    StatusByteBit,
    StatusSystem,
    parse_program_message,
    register_format_commands,
)


def dispatch(registry: CommandRegistry, source: str):
    return registry.dispatch(parse_program_message(source).commands[0])


def test_mav_tracks_partial_reads_until_every_byte_is_consumed() -> None:
    status = StatusSystem()
    queue = OutputQueue(status)
    queue.enqueue("response")

    assert len(queue) == 9
    assert status.status_byte == StatusByteBit.MESSAGE_AVAILABLE
    assert queue.read(3) == b"res"
    assert len(queue) == 6
    assert status.status_byte == StatusByteBit.MESSAGE_AVAILABLE
    assert queue.read(5) == b"ponse"
    assert status.status_byte == StatusByteBit.MESSAGE_AVAILABLE
    assert queue.read(1) == b"\n"
    assert status.status_byte == 0


def test_multiple_messages_and_fragmented_reads_preserve_all_bytes() -> None:
    status = StatusSystem()
    queue = OutputQueue(status)
    queue.enqueue("first")
    queue.enqueue(b"second")

    fragments = []
    while queue:
        fragments.append(queue.read(2))

    assert b"".join(fragments) == b"first\nsecond\n"
    assert status.output_queue_count == 0


def test_definite_and_indefinite_binary_blocks_are_byte_exact() -> None:
    payload = b"\x00;,\xff\nabc"

    assert BinaryResponse(payload).encode() == b"#18" + payload
    assert BinaryResponse(payload, definite=False).encode() == b"#0" + payload


def test_binary_blocks_survive_queue_fragmentation() -> None:
    status = StatusSystem()
    queue = OutputQueue(status)
    payload = bytes(range(256)) * 4096
    expected = BinaryResponse(payload).encode() + b"\n"
    queue.enqueue(BinaryResponse(payload))

    received = bytearray()
    while queue:
        received.extend(queue.read(997))

    assert bytes(received) == expected
    assert status.status_byte == 0


def test_queue_capacity_is_bounded_without_partial_enqueue() -> None:
    status = StatusSystem()
    queue = OutputQueue(status, capacity=5)

    with pytest.raises(OutputQueueFull):
        queue.enqueue("12345")

    assert len(queue) == 0
    assert status.status_byte == 0


def test_data_format_encodes_ascii_and_binary_byte_orders() -> None:
    data_format = DataFormat(DataEncoding.ASCII)
    assert data_format.encode_values([1, 2.5, -3]) == "1,2.5,-3"

    data_format.configure("REAL", 32)
    normal = data_format.encode_values([1.0, -2.0])
    assert isinstance(normal, BinaryResponse)
    assert normal.data == struct.pack(">2f", 1.0, -2.0)

    data_format.byte_order = ByteOrder.SWAPPED
    swapped = data_format.encode_values([1.0, -2.0])
    assert isinstance(swapped, BinaryResponse)
    assert swapped.data == struct.pack("<2f", 1.0, -2.0)


def test_format_commands_control_encoding_and_byte_order() -> None:
    data_format = DataFormat()
    registry = CommandRegistry()
    register_format_commands(registry, data_format)

    assert dispatch(registry, "FORM:DATA REAL,32") == ""
    assert dispatch(registry, "FORM:DATA?") == "REAL,32"
    assert dispatch(registry, "FORM:BORD SWAP") == ""
    assert dispatch(registry, "FORM:BORD?") == "SWAP"
    assert dispatch(registry, "FORM:DATA ASC") == ""
    assert dispatch(registry, "FORM:DATA?") == "ASC"


@pytest.mark.parametrize(
    ("kind", "bits"),
    [("REAL", None), ("REAL", 16), ("INT", 64), ("ASCII", 32)],
)
def test_invalid_data_formats_are_rejected(kind: str, bits: int | None) -> None:
    with pytest.raises(ValueError):
        DataFormat().configure(kind, bits)
