# Deterministic scenario and queued-data engine

The scenario engine provides one instrument-neutral way to describe changing DUT measurements and
events. A DMM adapter can consume scalar readings from it; a VNA adapter can consume complex traces;
and future instruments can use the same timing, advancement, reset, and exhaustion behavior.

The engine does not implement SCPI commands itself. Instrument drivers map their commands,
triggering, status, and errors onto named scenario streams. This prevents each instrument family
from inventing a separate playback system.

## Load a scenario without writing code

With a bench loaded in the no-flag interactive manager, scenarios can be changed while its servers
remain running:

```text
SCPI-MGR> scenario load dmm1 examples/remote_ate/dut-cycle.json
SCPI-MGR> scenario status dmm1
SCPI-MGR> scenario start dmm1
SCPI-MGR> scenario pause dmm1
SCPI-MGR> scenario step dmm1 voltage.dc
SCPI-MGR> scenario reset dmm1
```

`scenario load` validates the complete file before replacing the selected scenario and initially
pauses it. Quote a path only when it contains spaces. The dashboard provides the same operation in
each instrument card: choose a schema-1 JSON file, choose whether to start immediately, and select
**Load JSON**. The stream dropdown below it is specifically for adding deterministic noise to a
stream that is already loaded; it does not select or load a scenario file.

## Human-readable JSON format

Scenario documents use schema version 1:

```json
{
  "schema_version": 1,
  "name": "supply-startup",
  "description": "Nominal startup followed by drift and a limit failure.",
  "seed": 12345,
  "metadata": {"dut": "controller-a"},
  "streams": {
    "dmm.voltage": {
      "kind": "scalar",
      "advance": "read",
      "end": "hold-last",
      "samples": [
        {"value": 0.0, "at": 0.0, "label": "off"},
        {"value": 3.30, "at": 1.0, "label": "nominal"},
        {"value": 3.10, "at": 2.0, "label": "drift"},
        {"value": 2.70, "at": 3.0, "label": "failure"}
      ]
    },
    "fixture.event": {
      "kind": "event",
      "advance": "manual",
      "end": "error",
      "samples": [
        {"value": {"name": "power-applied"}},
        {"value": {"name": "interlock-open"}}
      ]
    }
  }
}
```

Every stream has ordered samples and one data shape:

- `scalar`: a single number, string, boolean, byte value, complex value, or null;
- `trace`: a numeric or complex vector;
- `table`: rows with equal width;
- `event`: an object describing an instrument/DUT-visible event;
- `error`: an object an adapter can translate into its native error/status system.

Sample `at` values are seconds relative to the most recent player reset. They must be non-negative
and non-decreasing within a stream. A read before the current sample is due raises `StreamNotReady`;
adapters decide whether that becomes waiting, a timeout, or an instrument-specific response.

## Advancement policies

`advance` controls what moves a stream to its next sample:

- `read`: return the current sample and advance immediately afterward;
- `trigger`: advance when `notify_trigger()` is called;
- `operation`: advance when `notify_operation_complete()` is called;
- `manual`: advance only through `step()`.

For trigger- and operation-driven streams, an adapter normally reads the current sample for the
measurement and sends the notification after the corresponding event completes. This makes the next
sample active for the next measurement cycle.

`end` controls the final sample:

- `hold-last`: retain and repeatedly return the final sample;
- `loop`: return to sample zero and increment the observable cycle count;
- `error`: return the final sample once, then raise `StreamExhausted`.

Playback position exposes the index, sample count, reads, advances, completed loops, exhaustion,
current due time, elapsed time, and readiness. All mutation is protected by a lock, so concurrent
consumers cannot take the same queued sample twice.

`pause()` freezes elapsed scenario time and prevents read, trigger, and operation policies from
advancing. Current samples remain readable, and `step()` remains available for deliberate manual
placement. `resume()` continues from the frozen time and position.

## Determinism and reset

`ScenarioPlayer` uses a shared scenario seed and offers locked `random_uniform()` draws for
procedural sample producers. `reset()` restores every stream to sample zero, clears counts and loop
state, establishes a new timing origin, and reseeds randomness. A caller may supply a replacement
seed for an intentional alternate run. The clock is injectable so tests and remote orchestration can
advance time without sleeping.

## Complex and binary values

JSON represents one complex number as:

```json
{"$complex": [1.0, -0.25]}
```

Arbitrary bytes use `{"$bytes": "<base64>"}`. Large numeric vectors can avoid decimal expansion
with a typed base64 payload:

```json
{
  "$binary": {
    "dtype": "complex64",
    "byte_order": "little",
    "data": "<base64 interleaved real/imaginary float32 bytes>"
  }
}
```

Supported numeric types are `int16`, `int32`, `float32`, `float64`, `complex64`, and `complex128`.
Complex payloads contain interleaved real/imaginary values. Length, alignment, encoding, type, and
byte order are validated before a scenario is accepted.

## Binary scenario container

`dump_scenario_binary()` creates a deterministic compressed container containing the same schema-1
JSON document. `load_scenario()` detects either UTF-8 JSON or the binary magic/version header; file
extensions are not used to decide the format. The binary container is intended for storage and
transport, not as a substitute for an instrument's IEEE binary-block response. Instrument adapters
remain responsible for formatting scenario values as SCPI ASCII or binary output.

```python
from scpi_emulator.scenario import ScenarioPlayer, load_scenario

definition = load_scenario("dut-cycle.json")
player = ScenarioPlayer(definition)
reading = player.read("dmm.voltage")
position = player.position("dmm.voltage")
```
