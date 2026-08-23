"""Instrument-facing lifecycle and fault controls for DUT scenarios."""

from __future__ import annotations

from dataclasses import asdict

from .model import ScenarioDefinition, ScenarioError


class ScenarioControlError(ScenarioError):
    """Raised when a scenario control action is invalid for the current state."""


class ScenarioController:
    """Control one instrument's shared player without bypassing SCPI state."""

    def __init__(self, instrument) -> None:
        self.instrument = instrument
        self.player = None
        self.last_fault: dict[str, object] | None = None

    def select(self, definition: ScenarioDefinition, *, start: bool = False):
        self.player = self.instrument.attach_scenario(definition)
        self.last_fault = None
        if not start:
            self.player.pause()
        return self.inspect()

    def attach(self, player) -> None:
        self.player = player

    def start(self, *, reset: bool = False, seed: int | None = None):
        player = self._require_player()
        if reset:
            player.reset(seed=seed)
        elif seed is not None:
            raise ScenarioControlError("seed requires reset")
        player.resume()
        return self.inspect()

    def pause(self):
        player = self._require_player()
        player.pause()
        return self.inspect()

    def reset(self, *, seed: int | None = None):
        player = self._require_player()
        player.reset(seed=seed)
        self.last_fault = None
        return self.inspect()

    def step(self, stream: str | None = None):
        player = self._require_player()
        names = (stream,) if stream is not None else player.stream_names
        positions = []
        for name in names:
            positions.append(asdict(player.step(name)))
        return positions

    def inject_fault(self, code: int, message: str | None = None):
        if isinstance(code, bool) or not isinstance(code, int) or code >= 0:
            raise ScenarioControlError("fault code must be a negative SCPI integer")
        if message is not None and (not isinstance(message, str) or not message.strip()):
            raise ScenarioControlError("fault message must be non-empty text")
        self.instrument.error_queue.push(code, message)
        self.last_fault = {"code": code, "message": message}
        return self.inspect()

    def inspect(self) -> dict[str, object]:
        if self.player is None:
            return {
                "selected": False,
                "state": "empty",
                "scenario": None,
                "seed": None,
                "streams": [],
                "last_fault": self.last_fault,
            }
        return {
            "selected": True,
            "state": "paused" if self.player.paused else "running",
            "scenario": self.player.definition.name,
            "seed": self.player.seed,
            "elapsed_seconds": self.player.elapsed_seconds(),
            "streams": [asdict(position) for position in self.player.positions()],
            "last_fault": self.last_fault,
        }

    def _require_player(self):
        if self.player is None:
            raise ScenarioControlError("no scenario is selected")
        return self.player
