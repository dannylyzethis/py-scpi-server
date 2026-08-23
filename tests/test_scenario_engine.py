import base64
import json
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest

from scpi_emulator.scenario import (
    BINARY_MAGIC,
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioFormatError,
    ScenarioPlayer,
    ScenarioSample,
    ScenarioStream,
    StreamExhausted,
    StreamKind,
    StreamNotFound,
    StreamNotReady,
    dump_scenario_binary,
    dumps_scenario,
    load_scenario,
    load_scenario_bytes,
    loads_scenario,
)


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def stream(
    name: str,
    values,
    *,
    kind: StreamKind = StreamKind.SCALAR,
    advance: AdvancePolicy = AdvancePolicy.READ,
    end: EndPolicy = EndPolicy.ERROR,
) -> ScenarioStream:
    return ScenarioStream(
        name,
        kind,
        tuple(ScenarioSample(value) for value in values),
        advance,
        end,
    )


def scenario(*streams: ScenarioStream, seed: int = 17) -> ScenarioDefinition:
    return ScenarioDefinition("test-scenario", streams, seed=seed)


def test_advance_on_read_and_explicit_exhaustion_are_observable() -> None:
    player = ScenarioPlayer(scenario(stream("voltage", (1.0, 1.5))))

    assert player.read("voltage") == 1.0
    assert player.position("voltage").index == 1
    assert player.read("voltage") == 1.5
    position = player.position("voltage")
    assert position.exhausted is True
    assert position.ready is False
    assert position.reads == 2
    assert position.advances == 2
    with pytest.raises(StreamExhausted):
        player.read("voltage")


def test_hold_last_and_loop_are_distinct_end_policies() -> None:
    player = ScenarioPlayer(
        scenario(
            stream("held", (10, 20), end=EndPolicy.HOLD_LAST),
            stream("looped", (1, 2), end=EndPolicy.LOOP),
        )
    )

    assert [player.read("held") for _ in range(4)] == [10, 20, 20, 20]
    assert [player.read("looped") for _ in range(5)] == [1, 2, 1, 2, 1]
    assert player.position("held").cycles == 0
    assert player.position("looped").cycles == 2


def test_trigger_operation_and_manual_streams_advance_only_on_their_events() -> None:
    player = ScenarioPlayer(
        scenario(
            stream("triggered", (1, 2), advance=AdvancePolicy.TRIGGER),
            stream("operation", (3, 4), advance=AdvancePolicy.OPERATION),
            stream("manual", (5, 6), advance=AdvancePolicy.MANUAL),
        )
    )

    assert player.read("triggered") == 1
    assert player.read("operation") == 3
    assert player.notify_trigger() == ("triggered",)
    assert player.read("triggered") == 2
    assert player.read("operation") == 3
    assert player.notify_operation_complete("operation") == ("operation",)
    assert player.read("operation") == 4
    assert player.step("manual").index == 1
    assert player.read("manual") == 6


def test_timed_samples_use_an_injected_clock_and_reset_point() -> None:
    clock = ManualClock()
    definition = ScenarioDefinition(
        "timed",
        (
            ScenarioStream(
                "temperature",
                StreamKind.SCALAR,
                (ScenarioSample(20, at_seconds=2), ScenarioSample(21, at_seconds=5)),
            ),
        ),
    )
    player = ScenarioPlayer(definition, clock=clock)

    with pytest.raises(StreamNotReady, match="due at 2s"):
        player.read("temperature")
    clock.now = 2
    assert player.read("temperature") == 20
    assert player.position("temperature").ready is False
    clock.now = 5
    assert player.read("temperature") == 21
    clock.now = 10
    player.reset()
    assert player.elapsed_seconds() == 0
    with pytest.raises(StreamNotReady):
        player.read("temperature")


def test_pause_freezes_time_and_automatic_advancement_but_manual_step_works() -> None:
    clock = ManualClock()
    definition = ScenarioDefinition(
        "controlled",
        (
            ScenarioStream(
                "voltage",
                StreamKind.SCALAR,
                (ScenarioSample(1), ScenarioSample(2, at_seconds=5)),
                advance=AdvancePolicy.READ,
                end=EndPolicy.HOLD_LAST,
            ),
        ),
    )
    player = ScenarioPlayer(definition, clock=clock)

    player.pause()
    clock.now = 20
    assert player.elapsed_seconds() == 0
    assert player.read("voltage") == 1
    assert player.position("voltage").index == 0
    assert player.step("voltage").index == 1
    assert player.position("voltage").ready is False

    player.resume()
    clock.now = 25
    assert player.position("voltage").ready is True
    assert player.read("voltage") == 2


def test_seeded_randomness_and_playback_reset_are_repeatable() -> None:
    player = ScenarioPlayer(scenario(stream("reading", (1, 2)), seed=1234))

    first_draws = [player.random_uniform(-1, 1) for _ in range(3)]
    assert player.read("reading") == 1
    player.reset()
    assert [player.random_uniform(-1, 1) for _ in range(3)] == first_draws
    assert player.random_draws == 3
    assert player.read("reading") == 1
    player.reset(seed=99)
    assert player.seed == 99
    assert player.random_uniform() != first_draws[0]


def test_json_codec_supports_all_stream_shapes_and_immutable_values() -> None:
    raw = {
        "schema_version": 1,
        "name": "dut-cycle",
        "seed": 42,
        "metadata": {"owner": "ATE"},
        "streams": {
            "voltage": {
                "kind": "scalar",
                "advance": "read",
                "end": "hold-last",
                "samples": [{"value": 3.3, "label": "nominal"}],
            },
            "s21": {
                "kind": "trace",
                "advance": "operation",
                "end": "loop",
                "samples": [
                    {
                        "value": [
                            {"$complex": [1.0, 0.5]},
                            {"$complex": [0.25, -0.1]},
                        ]
                    }
                ],
            },
            "limits": {
                "kind": "table",
                "samples": [{"value": [[1, 2], [3, 4]]}],
            },
            "door": {
                "kind": "event",
                "samples": [{"value": {"state": "open"}}],
            },
            "overload": {
                "kind": "error",
                "samples": [{"value": {"code": -222, "message": "overload"}}],
            },
        },
    }

    definition = loads_scenario(json.dumps(raw))

    assert definition.seed == 42
    assert definition.metadata == {"owner": "ATE"}
    assert definition.stream("s21").samples[0].value == (1 + 0.5j, 0.25 - 0.1j)
    assert definition.stream("limits").samples[0].value == ((1, 2), (3, 4))
    assert definition.stream("door").samples[0].value == {"state": "open"}
    assert loads_scenario(dumps_scenario(definition)) == definition
    with pytest.raises(TypeError):
        definition.metadata["owner"] = "changed"


def test_numeric_binary_payload_supports_real_and_complex_traces() -> None:
    real_payload = base64.b64encode(struct.pack("<3f", 1.0, 2.0, 3.0)).decode()
    complex_payload = base64.b64encode(struct.pack(">4d", 1.0, 0.5, 2.0, -0.25)).decode()
    raw = {
        "schema_version": 1,
        "name": "binary-traces",
        "streams": {
            "real": {
                "kind": "trace",
                "samples": [
                    {
                        "value": {
                            "$binary": {
                                "dtype": "float32",
                                "byte_order": "little",
                                "data": real_payload,
                            }
                        }
                    }
                ],
            },
            "complex": {
                "kind": "trace",
                "samples": [
                    {
                        "value": {
                            "$binary": {
                                "dtype": "complex128",
                                "byte_order": "big",
                                "data": complex_payload,
                            }
                        }
                    }
                ],
            },
        },
    }

    definition = loads_scenario(json.dumps(raw))

    assert definition.stream("real").samples[0].value == (1.0, 2.0, 3.0)
    assert definition.stream("complex").samples[0].value == (1 + 0.5j, 2 - 0.25j)


def test_compressed_binary_container_is_deterministic_and_round_trips(tmp_path) -> None:
    definition = scenario(
        stream("trace", ((1 + 2j, 3 + 4j),), kind=StreamKind.TRACE),
    )

    first = dump_scenario_binary(definition)
    second = dump_scenario_binary(definition)
    assert first == second
    assert first.startswith(BINARY_MAGIC)
    assert load_scenario_bytes(first) == definition

    binary_path = tmp_path / "scenario.scenario"
    json_path = tmp_path / "scenario.json"
    binary_path.write_bytes(first)
    json_path.write_text(dumps_scenario(definition), encoding="utf-8")
    assert load_scenario(binary_path) == definition
    assert load_scenario(json_path) == definition


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"schema_version": 2, "name": "bad", "streams": {}}, "schema_version"),
        ({"schema_version": 1, "name": "bad", "streams": {}}, "non-empty"),
        (
            {
                "schema_version": 1,
                "name": "bad",
                "streams": {"x": {"kind": "unknown", "samples": [{"value": 1}]}},
            },
            "unknown policy or kind",
        ),
        (
            {
                "schema_version": 1,
                "name": "bad",
                "streams": {"x": {"kind": "scalar", "samples": [{}]}},
            },
            "requires a value",
        ),
    ],
)
def test_invalid_scenario_documents_fail_before_playback(raw, message: str) -> None:
    with pytest.raises(ScenarioFormatError, match=message):
        loads_scenario(json.dumps(raw))


def test_invalid_binary_payloads_and_unknown_streams_are_deterministic_errors() -> None:
    broken = {
        "schema_version": 1,
        "name": "bad-binary",
        "streams": {
            "trace": {
                "kind": "trace",
                "samples": [
                    {
                        "value": {
                            "$binary": {
                                "dtype": "float64",
                                "data": base64.b64encode(b"short").decode(),
                            }
                        }
                    }
                ],
            }
        },
    }
    with pytest.raises(ScenarioFormatError, match="not aligned"):
        loads_scenario(json.dumps(broken))
    with pytest.raises(ScenarioFormatError, match="binary scenario container"):
        load_scenario_bytes(BINARY_MAGIC + b"not gzip")

    player = ScenarioPlayer(scenario(stream("known", (1,))))
    with pytest.raises(StreamNotFound):
        player.read("missing")


def test_concurrent_reads_consume_each_queued_sample_once() -> None:
    count = 100
    player = ScenarioPlayer(scenario(stream("queue", tuple(range(count)))))

    with ThreadPoolExecutor(max_workers=10) as executor:
        values = list(executor.map(lambda _: player.read("queue"), range(count)))

    assert sorted(values) == list(range(count))
    assert player.position("queue").exhausted is True
