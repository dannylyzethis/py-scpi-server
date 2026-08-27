# Dashboard control room

The optional web dashboard is a development control plane for a running virtual bench. It shows
the state owned by each instrument rather than maintaining a separate UI-only state model.

Start a local dashboard with:

```powershell
scpi-emulator --load examples/csv/catalog --start --web
```

The default address is `http://127.0.0.1:8081`. A non-loopback bind requires an authentication
token. Mutating API calls also require the session's CSRF token.

## Instrument state

Each instrument card reports:

- server and client-session state;
- status byte, standard event register, ESE, SRE, SRQ, operation, questionable, and output-queue
  state without destructively reading any register;
- pending overlapped operations and acquisition/trigger state;
- VNA channels, measurements, windows, and traces, or scalar function/range/last-value state;
- capability-profile counts and the current deterministic scenario position;
- queued SCPI errors without removing them from the instrument error queue.

The `/api/status` response contains this data under each instrument's `snapshot` field.

## Offline and live updates

The dashboard does not download browser code, fonts, or styles from the internet. Its project-owned
`dashboard.css` and `dashboard.js` assets are included in the Python package and served from the
same local process. They introduce no additional third-party browser dependency or license.

The dashboard observes completed commands at the instrument layer, so raw socket, VXI-11, HiSLIP,
and dashboard-console commands all update the command stream, cards, status registers, counters,
errors, measurements, and scenario position immediately. Server lifecycle, client-session, and
scenario-control changes are reflected through the same authoritative state API.

The browser polls `/api/status` and `/api/commands` once per second. It retains open instrument
details and unsent fault/noise control values while cards refresh. This same-origin polling model
works without a CDN or browser WebSocket library and reconnects automatically after a temporary
server interruption.

Dashboard startup binds the HTTP listener before reporting success. Runtime shutdown explicitly
stops the listener, joins its thread, and detaches instrument observers, which makes repeated
start/stop cycles deterministic in tests and local tools.

## Controls and invariants

The command console is serialized with physical-style client sessions. It returns HTTP `409` when
an external client owns the instrument instead of mutating the same state concurrently.

Scenario start, pause, reset, and manual-step actions use the shared scenario player. Fault
injection pushes a standard negative SCPI error through the instrument error queue, so ESE/SRE,
status-byte, and SRQ behavior remain visible to connected ATE software.

Deterministic noise is configured per stream as an absolute non-negative amplitude. Numeric scalar,
trace, and table values receive stable seed-, stream-, sample-, and element-specific offsets. The
same seed and scenario position always reproduce the same value. An amplitude of zero removes the
noise setting. Noise is applied inside scenario playback, so every consumer sees the same result.

The relevant endpoints are:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/status` | Read system and detailed instrument snapshots |
| `GET` | `/api/scenario/<instrument>` | Read scenario state and stream positions |
| `PUT` | `/api/scenario/<instrument>` | Select a versioned scenario definition |
| `POST` | `/api/scenario/<instrument>/<action>` | Start, pause, reset, step, inject a fault, or set noise |
| `POST` | `/api/send_command/<instrument>` | Execute through the serialized SCPI control path |

The dashboard is intended for trusted development networks. It is not an internet-facing service.
