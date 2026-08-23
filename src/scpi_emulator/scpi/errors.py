"""SCPI error records, bounded queues, and Standard Event Status classification."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, IntFlag
from threading import RLock

from .registry import SCPICommandError


class ErrorCategory(Enum):
    COMMAND = "command"
    EXECUTION = "execution"
    DEVICE = "device"
    QUERY = "query"


class StandardEventBit(IntFlag):
    OPERATION_COMPLETE = 1 << 0
    REQUEST_CONTROL = 1 << 1
    QUERY_ERROR = 1 << 2
    DEVICE_DEPENDENT_ERROR = 1 << 3
    EXECUTION_ERROR = 1 << 4
    COMMAND_ERROR = 1 << 5
    USER_REQUEST = 1 << 6
    POWER_ON = 1 << 7


_CATEGORY_BITS = {
    ErrorCategory.COMMAND: StandardEventBit.COMMAND_ERROR,
    ErrorCategory.EXECUTION: StandardEventBit.EXECUTION_ERROR,
    ErrorCategory.DEVICE: StandardEventBit.DEVICE_DEPENDENT_ERROR,
    ErrorCategory.QUERY: StandardEventBit.QUERY_ERROR,
}

_STANDARD_MESSAGES = {
    0: "No error",
    -100: "Command error",
    -102: "Syntax error",
    -104: "Data type error",
    -108: "Parameter not allowed",
    -109: "Missing parameter",
    -113: "Undefined header",
    -200: "Execution error",
    -222: "Data out of range",
    -224: "Illegal parameter value",
    -300: "Device-specific error",
    -310: "System error",
    -350: "Queue overflow",
    -400: "Query error",
    -410: "Query INTERRUPTED",
    -420: "Query UNTERMINATED",
    -430: "Query DEADLOCKED",
    -440: "Query UNTERMINATED after indefinite response",
}


@dataclass(frozen=True)
class SCPIErrorRecord:
    code: int
    message: str
    category: ErrorCategory

    @property
    def response(self) -> str:
        escaped = self.message.replace('"', '""')
        return f'{self.code},"{escaped}"'

    def __str__(self) -> str:
        return self.response


NO_ERROR = SCPIErrorRecord(0, _STANDARD_MESSAGES[0], ErrorCategory.DEVICE)
QUEUE_OVERFLOW = SCPIErrorRecord(-350, _STANDARD_MESSAGES[-350], ErrorCategory.DEVICE)


def classify_error(code: int) -> ErrorCategory:
    """Classify standard negative SCPI error ranges for the SESR."""
    if -199 <= code <= -100:
        return ErrorCategory.COMMAND
    if -299 <= code <= -200:
        return ErrorCategory.EXECUTION
    if -399 <= code <= -300:
        return ErrorCategory.DEVICE
    if -499 <= code <= -400:
        return ErrorCategory.QUERY
    raise ValueError(f"error code {code} is outside the standard SCPI error ranges")


def standard_error(code: int, detail: str | None = None) -> SCPIErrorRecord:
    """Build a standard SCPI error with an optional stable detail suffix."""
    if code == 0:
        if detail is not None:
            raise ValueError("the no-error response cannot include detail")
        return NO_ERROR
    category = classify_error(code)
    message = _STANDARD_MESSAGES.get(code)
    if message is None:
        message = _STANDARD_MESSAGES[{
            ErrorCategory.COMMAND: -100,
            ErrorCategory.EXECUTION: -200,
            ErrorCategory.DEVICE: -300,
            ErrorCategory.QUERY: -400,
        }[category]]
    if detail:
        message = f"{message}; {detail}"
    return SCPIErrorRecord(code, message, category)


class ErrorQueue:
    """Thread-safe bounded FIFO with IEEE 488.2 event-status latching."""

    def __init__(
        self,
        capacity: int = 20,
        event_sink: Callable[[StandardEventBit], None] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("error queue capacity must be at least one")
        self.capacity = capacity
        self._errors: deque[SCPIErrorRecord] = deque()
        self._event_status = StandardEventBit(0)
        self._event_sink = event_sink
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._errors)

    @property
    def event_status(self) -> int:
        """Return the latched Standard Event Status register without clearing it."""
        with self._lock:
            return int(self._event_status)

    def push(
        self,
        error: SCPIErrorRecord | SCPICommandError | int,
        message: str | None = None,
    ) -> SCPIErrorRecord:
        """Queue an error and latch its category bit; return the normalized record."""
        record = self._normalize(error, message)
        with self._lock:
            self._latch(_CATEGORY_BITS[record.category])
            if len(self._errors) < self.capacity:
                self._errors.append(record)
                return record
            if self._errors[-1] != QUEUE_OVERFLOW:
                self._errors[-1] = QUEUE_OVERFLOW
                self._latch(StandardEventBit.DEVICE_DEPENDENT_ERROR)
            return record

    def pop(self) -> SCPIErrorRecord:
        """Return and remove the oldest error, or the standard no-error record."""
        with self._lock:
            return self._errors.popleft() if self._errors else NO_ERROR

    def next_response(self) -> str:
        """Implement the response payload for ``SYSTem:ERRor?``."""
        return self.pop().response

    def last_response(self) -> str | None:
        """Return the newest queued error for monitoring without removing it."""
        with self._lock:
            return self._errors[-1].response if self._errors else None

    def snapshot(self) -> tuple[dict[str, object], ...]:
        """Return queued errors without changing the queue or event registers."""
        with self._lock:
            return tuple(
                {
                    "code": record.code,
                    "message": record.message,
                    "category": record.category.value,
                }
                for record in self._errors
            )

    def count_response(self) -> str:
        """Implement the response payload for ``SYSTem:ERRor:COUNt?``."""
        return str(len(self))

    def clear(self, *, clear_event_status: bool = False) -> None:
        """Clear queued errors and optionally the Standard Event Status latch."""
        with self._lock:
            self._errors.clear()
            if clear_event_status:
                self._event_status = StandardEventBit(0)

    def read_event_status(self) -> int:
        """Return and destructively clear the Standard Event Status register."""
        with self._lock:
            value = int(self._event_status)
            self._event_status = StandardEventBit(0)
            return value

    def latch(self, bits: StandardEventBit) -> None:
        """Latch non-error Standard Event Status bits such as OPC or power-on."""
        with self._lock:
            self._latch(bits)

    def _latch(self, bits: StandardEventBit) -> None:
        self._event_status |= bits
        if self._event_sink is not None:
            self._event_sink(bits)

    @staticmethod
    def _normalize(
        error: SCPIErrorRecord | SCPICommandError | int,
        message: str | None,
    ) -> SCPIErrorRecord:
        if isinstance(error, SCPIErrorRecord):
            if message is not None:
                raise TypeError("message cannot accompany an SCPIErrorRecord")
            return error
        if isinstance(error, SCPICommandError):
            if message is not None:
                raise TypeError("message cannot accompany an SCPICommandError")
            return SCPIErrorRecord(error.code, error.message, classify_error(error.code))
        return standard_error(error, message)
