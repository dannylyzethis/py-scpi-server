"""IEEE 488.2 and SCPI status-register model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntFlag
from threading import RLock

from .errors import ErrorQueue, StandardEventBit
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
)


EVENT_REGISTER_MASK = 0xFF
STATUS_REGISTER_MASK = 0x7FFF


class StatusByteBit(IntFlag):
    ERROR_QUEUE = 1 << 2
    QUESTIONABLE = 1 << 3
    MESSAGE_AVAILABLE = 1 << 4
    EVENT_STATUS = 1 << 5
    MASTER_STATUS_SUMMARY = 1 << 6
    OPERATION = 1 << 7


@dataclass
class StatusRegisterGroup:
    """SCPI condition/event/enable group with transition filters."""

    condition: int = 0
    event: int = 0
    enable: int = 0
    positive_transition: int = STATUS_REGISTER_MASK
    negative_transition: int = 0
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    @property
    def summary(self) -> bool:
        with self._lock:
            return bool(self.event & self.enable)

    def set_condition(self, value: int) -> None:
        value = _register_value(value, STATUS_REGISTER_MASK, "condition")
        with self._lock:
            rising = ~self.condition & value & STATUS_REGISTER_MASK
            falling = self.condition & ~value & STATUS_REGISTER_MASK
            self.event |= (rising & self.positive_transition) | (
                falling & self.negative_transition
            )
            self.condition = value

    def read_condition(self) -> int:
        with self._lock:
            return self.condition

    def read_event(self) -> int:
        with self._lock:
            value = self.event
            self.event = 0
            return value

    def clear_event(self) -> None:
        with self._lock:
            self.event = 0

    def set_enable(self, value: int) -> None:
        with self._lock:
            self.enable = _register_value(value, STATUS_REGISTER_MASK, "enable")

    def set_positive_transition(self, value: int) -> None:
        with self._lock:
            self.positive_transition = _register_value(
                value, STATUS_REGISTER_MASK, "positive transition"
            )

    def set_negative_transition(self, value: int) -> None:
        with self._lock:
            self.negative_transition = _register_value(
                value, STATUS_REGISTER_MASK, "negative transition"
            )

    def preset(self) -> None:
        """Apply ``STATus:PRESet`` defaults without changing condition/event state."""
        with self._lock:
            self.enable = 0
            self.positive_transition = STATUS_REGISTER_MASK
            self.negative_transition = 0


class StatusSystem:
    """Computes IEEE 488.2 summary bits from all underlying status sources."""

    def __init__(self, error_queue: ErrorQueue | None = None) -> None:
        self.error_queue = error_queue if error_queue is not None else ErrorQueue()
        self.operation = StatusRegisterGroup()
        self.questionable = StatusRegisterGroup()
        self._event_status_enable = 0
        self._service_request_enable = 0
        self._output_queue_count = 0
        self._lock = RLock()

    @property
    def event_status(self) -> int:
        return self.error_queue.event_status

    def read_event_status(self) -> int:
        return self.error_queue.read_event_status()

    def latch_event(self, bits: StandardEventBit | int) -> None:
        value = _register_value(int(bits), EVENT_REGISTER_MASK, "event status")
        self.error_queue.latch(StandardEventBit(value))

    @property
    def event_status_enable(self) -> int:
        with self._lock:
            return self._event_status_enable

    def set_event_status_enable(self, value: int) -> None:
        with self._lock:
            self._event_status_enable = _register_value(
                value, EVENT_REGISTER_MASK, "event status enable"
            )

    @property
    def service_request_enable(self) -> int:
        with self._lock:
            return self._service_request_enable

    def set_service_request_enable(self, value: int) -> None:
        value = _register_value(value, EVENT_REGISTER_MASK, "service request enable")
        with self._lock:
            self._service_request_enable = value & ~StatusByteBit.MASTER_STATUS_SUMMARY

    @property
    def output_queue_count(self) -> int:
        with self._lock:
            return self._output_queue_count

    def set_output_queue_count(self, count: int) -> None:
        if count < 0:
            raise ValueError("output queue count cannot be negative")
        with self._lock:
            self._output_queue_count = count

    @property
    def status_byte(self) -> int:
        base = 0
        if len(self.error_queue):
            base |= StatusByteBit.ERROR_QUEUE
        if self.questionable.summary:
            base |= StatusByteBit.QUESTIONABLE
        if self.output_queue_count:
            base |= StatusByteBit.MESSAGE_AVAILABLE
        if self.event_status & self.event_status_enable:
            base |= StatusByteBit.EVENT_STATUS
        if self.operation.summary:
            base |= StatusByteBit.OPERATION
        if base & self.service_request_enable:
            base |= StatusByteBit.MASTER_STATUS_SUMMARY
        return int(base)

    @property
    def requesting_service(self) -> bool:
        return bool(self.status_byte & StatusByteBit.MASTER_STATUS_SUMMARY)

    def clear_status(self) -> None:
        """Implement ``*CLS`` while preserving enable masks and live conditions."""
        self.error_queue.clear(clear_event_status=True)
        self.operation.clear_event()
        self.questionable.clear_event()

    def preset(self) -> None:
        self.operation.preset()
        self.questionable.preset()


def register_status_commands(registry: CommandRegistry, status: StatusSystem) -> None:
    """Register IEEE common and SCPI status commands on a typed registry."""
    integer_8 = (ParameterSpec(ParameterType.INTEGER, minimum=0, maximum=255),)
    integer_15 = (
        ParameterSpec(ParameterType.INTEGER, minimum=0, maximum=STATUS_REGISTER_MASK),
    )

    def common(name: str, handler, *, query: bool = False, parameters=()) -> None:
        registry.register(
            CommandSpec(
                path=(HeaderNode(name),),
                handler=handler,
                parameters=parameters,
                query=query,
                common=True,
            )
        )

    common("*CLS", lambda invocation: status.clear_status() or "")
    common(
        "*ESE",
        lambda invocation, value: status.set_event_status_enable(value) or "",
        parameters=integer_8,
    )
    common("*ESE", lambda invocation: str(status.event_status_enable), query=True)
    common("*ESR", lambda invocation: str(status.read_event_status()), query=True)
    common(
        "*SRE",
        lambda invocation, value: status.set_service_request_enable(value) or "",
        parameters=integer_8,
    )
    common("*SRE", lambda invocation: str(status.service_request_enable), query=True)
    common("*STB", lambda invocation: str(status.status_byte), query=True)

    registry.register(
        CommandSpec(
            path=(HeaderNode("STATus"), HeaderNode("PRESet")),
            handler=lambda invocation: status.preset() or "",
        )
    )
    _register_group_commands(registry, "OPERation", status.operation, integer_15)
    _register_group_commands(registry, "QUEStionable", status.questionable, integer_15)


def _register_group_commands(
    registry: CommandRegistry,
    name: str,
    group: StatusRegisterGroup,
    integer_parameter: tuple[ParameterSpec, ...],
) -> None:
    prefix = (HeaderNode("STATus"), HeaderNode(name))

    def add_query(node: str, reader) -> None:
        registry.register(
            CommandSpec(
                path=(*prefix, HeaderNode(node)),
                handler=lambda invocation: str(reader()),
                query=True,
            )
        )

    def add_set_query(node: str, setter, reader) -> None:
        registry.register(
            CommandSpec(
                path=(*prefix, HeaderNode(node)),
                handler=lambda invocation, value: setter(value) or "",
                parameters=integer_parameter,
            )
        )
        add_query(node, reader)

    add_query("CONDition", group.read_condition)
    add_query("EVENt", group.read_event)
    add_set_query("ENABle", group.set_enable, lambda: group.enable)
    add_set_query("PTRansition", group.set_positive_transition, lambda: group.positive_transition)
    add_set_query("NTRansition", group.set_negative_transition, lambda: group.negative_transition)


def _register_value(value: int, mask: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= mask:
        raise ValueError(f"{name} register must be between 0 and {mask}")
    return value
