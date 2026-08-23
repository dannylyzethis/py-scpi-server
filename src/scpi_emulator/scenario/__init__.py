"""Generic deterministic scenario and queued-data engine."""

from .codec import (
    BINARY_MAGIC,
    dump_scenario_binary,
    dumps_scenario,
    load_scenario,
    load_scenario_bytes,
    loads_scenario,
)
from .control import ScenarioControlError, ScenarioController
from .model import (
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioError,
    ScenarioFormatError,
    ScenarioSample,
    ScenarioStream,
    StreamExhausted,
    StreamKind,
    StreamNotFound,
    StreamNotReady,
)
from .runtime import PlaybackPosition, ScenarioPlayer

__all__ = [
    "BINARY_MAGIC",
    "AdvancePolicy",
    "EndPolicy",
    "PlaybackPosition",
    "ScenarioDefinition",
    "ScenarioControlError",
    "ScenarioController",
    "ScenarioError",
    "ScenarioFormatError",
    "ScenarioPlayer",
    "ScenarioSample",
    "ScenarioStream",
    "StreamExhausted",
    "StreamKind",
    "StreamNotFound",
    "StreamNotReady",
    "dump_scenario_binary",
    "dumps_scenario",
    "load_scenario",
    "load_scenario_bytes",
    "loads_scenario",
]
