"""Overlapped operation tracking and IEEE 488.2 completion fences."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Condition, RLock
from time import monotonic

from .errors import StandardEventBit
from .registry import CommandRegistry, CommandSpec, HeaderNode
from .status import StatusSystem


class OperationState(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OperationTimeout(TimeoutError):
    """A completion fence did not resolve before its caller's timeout."""


class OperationStateError(RuntimeError):
    """An operation was moved between incompatible terminal states."""


@dataclass
class _CompletionFence:
    pending: set[int]


@dataclass(frozen=True)
class OperationHandle:
    """A caller-owned handle for completing or cancelling overlapped work."""

    identifier: int
    name: str
    _manager: OperationManager

    @property
    def state(self) -> OperationState:
        return self._manager.operation_state(self.identifier)

    @property
    def done(self) -> bool:
        return self.state is not OperationState.PENDING

    def complete(self) -> bool:
        return self._manager._finish(self.identifier, OperationState.COMPLETED)

    def cancel(self) -> bool:
        return self._manager._finish(self.identifier, OperationState.CANCELLED)

    def fail(self, detail: str | None = None, code: int = -300) -> bool:
        changed = self._manager._finish(self.identifier, OperationState.FAILED)
        if changed:
            self._manager.status.error_queue.push(code, detail or self.name)
        return changed


class OperationManager:
    """Tracks work started before OPC, OPC?, and WAI synchronization points."""

    def __init__(self, status: StatusSystem) -> None:
        self.status = status
        self._next_identifier = 1
        self._states: dict[int, OperationState] = {}
        self._names: dict[int, str] = {}
        self._opc_fences: list[_CompletionFence] = []
        self._condition = Condition(RLock())

    @property
    def pending_count(self) -> int:
        with self._condition:
            return sum(state is OperationState.PENDING for state in self._states.values())

    def begin(self, name: str) -> OperationHandle:
        if not name.strip():
            raise ValueError("operation name cannot be empty")
        with self._condition:
            identifier = self._next_identifier
            self._next_identifier += 1
            self._states[identifier] = OperationState.PENDING
            self._names[identifier] = name
            return OperationHandle(identifier, name, self)

    def operation_state(self, identifier: int) -> OperationState:
        with self._condition:
            try:
                return self._states[identifier]
            except KeyError as error:
                raise KeyError(f"unknown operation identifier {identifier}") from error

    def opc(self) -> str:
        """Arm SESR OPC bit 0 for completion of operations already in progress."""
        with self._condition:
            pending = self._pending_identifiers()
            if pending:
                self._opc_fences.append(_CompletionFence(pending))
            else:
                self.status.latch_event(StandardEventBit.OPERATION_COMPLETE)
        return ""

    def opc_query(self, timeout: float | None = None) -> str:
        """Wait for prior operations and return 1 without setting the OPC event bit."""
        self.wait_for_prior(timeout)
        return "1"

    def wai(self, timeout: float | None = None) -> str:
        """Block command execution until operations preceding WAI are terminal."""
        self.wait_for_prior(timeout)
        return ""

    def wait_for_prior(self, timeout: float | None = None) -> None:
        with self._condition:
            pending = self._pending_identifiers()
            if not pending:
                return
            deadline = None if timeout is None else monotonic() + timeout
            while any(self._states[identifier] is OperationState.PENDING for identifier in pending):
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise OperationTimeout("timed out waiting for prior operations")
                self._condition.wait(remaining)

    def abort(self) -> tuple[int, ...]:
        """Cancel all pending work and resolve completion fences deterministically."""
        with self._condition:
            identifiers = tuple(sorted(self._pending_identifiers()))
            for identifier in identifiers:
                self._states[identifier] = OperationState.CANCELLED
            self._resolve_opc_fences(set(identifiers))
            self._condition.notify_all()
            return identifiers

    def _finish(self, identifier: int, state: OperationState) -> bool:
        if state is OperationState.PENDING:
            raise ValueError("cannot finish an operation as pending")
        with self._condition:
            current = self.operation_state(identifier)
            if current is state:
                return False
            if current is not OperationState.PENDING:
                raise OperationStateError(
                    f"operation {identifier} is already {current.value}, not {state.value}"
                )
            self._states[identifier] = state
            self._resolve_opc_fences({identifier})
            self._condition.notify_all()
            return True

    def _pending_identifiers(self) -> set[int]:
        return {
            identifier
            for identifier, state in self._states.items()
            if state is OperationState.PENDING
        }

    def _resolve_opc_fences(self, terminal: set[int]) -> None:
        completed: list[_CompletionFence] = []
        for fence in self._opc_fences:
            fence.pending.difference_update(terminal)
            if not fence.pending:
                completed.append(fence)
        if completed:
            self.status.latch_event(StandardEventBit.OPERATION_COMPLETE)
            self._opc_fences = [fence for fence in self._opc_fences if fence.pending]


def register_operation_commands(
    registry: CommandRegistry, operations: OperationManager
) -> None:
    """Register OPC, OPC?, WAI, and ABORt against the typed command registry."""
    registry.register(
        CommandSpec(
            path=(HeaderNode("*OPC"),),
            handler=lambda invocation: operations.opc(),
            common=True,
        )
    )
    registry.register(
        CommandSpec(
            path=(HeaderNode("*OPC"),),
            handler=lambda invocation: operations.opc_query(),
            query=True,
            common=True,
        )
    )
    registry.register(
        CommandSpec(
            path=(HeaderNode("*WAI"),),
            handler=lambda invocation: operations.wai(),
            common=True,
        )
    )
    registry.register(
        CommandSpec(
            path=(HeaderNode("ABORt"),),
            handler=lambda invocation: _abort_response(operations),
        )
    )


def _abort_response(operations: OperationManager) -> str:
    operations.abort()
    return ""
