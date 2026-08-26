# Remote ATE development workflow

The emulator separates the virtual bench (which instruments and network addresses exist) from the
DUT scenario (which values and faults those instruments observe). A remote test developer can keep
the bench running, select a scenario, start or pause it, inspect exact playback positions, reset it
to reproduce a run, and inject a SCPI-visible fault without restarting an instrument server.

The complete runnable example is in `examples/remote_ate`.

For ordinary local use, no control script is required: load `dut-cycle.json` from the dashboard's
**Scenario file** panel, or use `scenario load dmm1 examples/remote_ate/dut-cycle.json` followed by
`scenario start dmm1` in the interactive manager. The API workflow below remains useful for CI and
remote orchestration.

## Start the bench host

Install the project with the web extra, then run:

```powershell
python examples/remote_ate/run_bench.py
```

This starts a dmm-compatible DMM on raw TCP port 15025 and the control API on port 18081. To
serve a remote network, bind explicitly and supply an authentication token:

```powershell
python examples/remote_ate/run_bench.py --bind 0.0.0.0 `
  --dashboard-bind 0.0.0.0 --token "replace-with-a-secret"
```

The SCPI socket and control API serve different purposes. Production ATE code talks only SCPI. A
developer, test fixture, or CI orchestrator uses the authenticated control API to choose the DUT
case around that code.

## Run the remote workflow

From another process or machine, run:

```powershell
python examples/remote_ate/ate_client.py
```

Add `--token "replace-with-a-secret"` when the host requires authentication. The client obtains an
authenticated CSRF session token, uploads `dut-cycle.json`, starts it from its deterministic reset
point, reads three DMM values through the SCPI socket, injects an overvoltage error, and inspects the
player state.

The failure is visible through normal instrument semantics: `*STB?` reports the error-queue summary
and `SYST:ERR?` returns `-222`. Code using `*ESE`, `*SRE`, serial poll, or SRQ sees the same status
machinery; the control API does not maintain a private side-channel error flag.

## Scenario control API

All mutation requests require `X-SCPI-CSRF`; non-loopback deployments also require the configured
bearer token. `GET /api/session` returns the CSRF value after the normal authentication check.

| Request | Behavior |
| --- | --- |
| `PUT /api/scenario/<instrument>` | Validate and select a schema-1 scenario; selection starts paused by default. |
| `GET /api/scenario/<instrument>` | Inspect scenario name, state, seed, elapsed time, and every stream position. |
| `POST .../start` | Resume; `{ "reset": true, "seed": 7 }` starts a reproducible fresh run. |
| `POST .../pause` | Freeze scenario time and automatic read/trigger/operation advancement. |
| `POST .../step` | Manually advance one named stream, or every stream when omitted. |
| `POST .../reset` | Restore all stream positions and counters, optionally with a new seed. |
| `POST .../fault` | Queue a negative SCPI error code through the instrument status system. |

While paused, reads return the current sample without advancing it. Manual stepping still works,
which lets a developer place a DUT at an exact state before running an automation fragment. Invalid
scenario documents are rejected before replacing the active scenario.
