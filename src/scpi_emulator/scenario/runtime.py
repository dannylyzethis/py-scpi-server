"""Thread-safe deterministic playback for scenario streams."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .model import (
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamExhausted,
    StreamNotFound,
    StreamNotReady,
)


@dataclass(frozen=True)
class PlaybackPosition:
    stream: str
    index: int
    sample_count: int
    reads: int
    advances: int
    cycles: int
    exhausted: bool
    ready: bool
    due_at_seconds: float
    elapsed_seconds: float


@dataclass
class _StreamState:
    definition: ScenarioStream
    index: int = 0
    reads: int = 0
    advances: int = 0
    cycles: int = 0
    exhausted: bool = False


class ScenarioPlayer:
    """Play named streams under one reset point, seed, and injected clock."""

    def __init__(
        self,
        definition: ScenarioDefinition,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.definition = definition
        self._clock = clock
        self._lock = threading.RLock()
        self._states = {stream.name: _StreamState(stream) for stream in definition.streams}
        self._seed = definition.seed
        self._random = random.Random(self._seed)
        self._random_draws = 0
        self._started_at = self._clock()
        self._paused = False
        self._paused_at: float | None = None
        self._paused_total = 0.0

    @property
    def stream_names(self) -> tuple[str, ...]:
        return tuple(self._states)

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def random_draws(self) -> int:
        return self._random_draws

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def pause(self) -> None:
        """Freeze timed playback and automatic advancement."""
        with self._lock:
            if not self._paused:
                self._paused = True
                self._paused_at = self._clock()

    def resume(self) -> None:
        """Resume playback without changing stream positions."""
        with self._lock:
            if self._paused:
                now = self._clock()
                self._paused_total += max(0.0, now - self._paused_at)
                self._paused = False
                self._paused_at = None

    def read(self, stream: str):
        """Return the current value and apply an advance-on-read policy afterward."""
        return self.read_sample(stream).value

    def read_sample(self, stream: str) -> ScenarioSample:
        with self._lock:
            state = self._state(stream)
            sample = self._current_ready_sample(state)
            state.reads += 1
            if state.definition.advance is AdvancePolicy.READ:
                self._advance(state)
            return sample

    def peek(self, stream: str):
        with self._lock:
            return self._current_ready_sample(self._state(stream)).value

    def step(self, stream: str) -> PlaybackPosition:
        """Explicitly advance one stream regardless of its automatic policy."""
        with self._lock:
            state = self._state(stream)
            self._advance(state, manual=True)
            return self._position(state)

    def notify_trigger(self, stream: str | None = None) -> tuple[str, ...]:
        return self._notify(AdvancePolicy.TRIGGER, stream)

    def notify_operation_complete(self, stream: str | None = None) -> tuple[str, ...]:
        return self._notify(AdvancePolicy.OPERATION, stream)

    def position(self, stream: str) -> PlaybackPosition:
        with self._lock:
            return self._position(self._state(stream))

    def positions(self) -> tuple[PlaybackPosition, ...]:
        with self._lock:
            return tuple(self._position(state) for state in self._states.values())

    def elapsed_seconds(self) -> float:
        with self._lock:
            return self._elapsed()

    def random_uniform(self, minimum: float = 0.0, maximum: float = 1.0) -> float:
        """Provide deterministic randomness to procedural scenario producers."""
        with self._lock:
            self._random_draws += 1
            return self._random.uniform(minimum, maximum)

    def reset(self, *, seed: int | None = None) -> None:
        with self._lock:
            if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
                raise ValueError("scenario seed must be an integer")
            self._seed = self.definition.seed if seed is None else seed
            self._random.seed(self._seed)
            self._random_draws = 0
            self._started_at = self._clock()
            self._paused_total = 0.0
            if self._paused:
                self._paused_at = self._started_at
            for state in self._states.values():
                state.index = 0
                state.reads = 0
                state.advances = 0
                state.cycles = 0
                state.exhausted = False

    def _notify(self, policy: AdvancePolicy, stream: str | None) -> tuple[str, ...]:
        with self._lock:
            states = (self._state(stream),) if stream is not None else tuple(self._states.values())
            advanced: list[str] = []
            for state in states:
                if state.definition.advance is policy and self._advance(state):
                    advanced.append(state.definition.name)
            return tuple(advanced)

    def _state(self, stream: str) -> _StreamState:
        try:
            return self._states[stream]
        except KeyError as error:
            raise StreamNotFound(stream) from error

    def _current_ready_sample(self, state: _StreamState) -> ScenarioSample:
        if state.exhausted:
            raise StreamExhausted(state.definition.name)
        sample = state.definition.samples[state.index]
        elapsed = self._elapsed()
        if elapsed < sample.at_seconds:
            raise StreamNotReady(
                f"stream {state.definition.name!r} sample {state.index} is due at "
                f"{sample.at_seconds:g}s; elapsed {elapsed:g}s"
            )
        return sample

    def _advance(self, state: _StreamState, *, manual: bool = False) -> bool:
        if self._paused and not manual:
            return False
        if state.exhausted:
            raise StreamExhausted(state.definition.name)
        state.advances += 1
        if state.index + 1 < len(state.definition.samples):
            state.index += 1
            return True
        if state.definition.end is EndPolicy.HOLD_LAST:
            return True
        if state.definition.end is EndPolicy.LOOP:
            state.index = 0
            state.cycles += 1
            return True
        state.exhausted = True
        return True

    def _position(self, state: _StreamState) -> PlaybackPosition:
        elapsed = self._elapsed()
        due = state.definition.samples[state.index].at_seconds
        return PlaybackPosition(
            stream=state.definition.name,
            index=state.index,
            sample_count=len(state.definition.samples),
            reads=state.reads,
            advances=state.advances,
            cycles=state.cycles,
            exhausted=state.exhausted,
            ready=not state.exhausted and elapsed >= due,
            due_at_seconds=due,
            elapsed_seconds=elapsed,
        )

    def _elapsed(self) -> float:
        now = self._paused_at if self._paused else self._clock()
        return max(0.0, now - self._started_at - self._paused_total)
