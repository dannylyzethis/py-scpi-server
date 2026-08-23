# Dashboard control room

The optional web dashboard is a development control plane for a running virtual bench. It shows
the state owned by each instrument rather than maintaining a separate UI-only state model.

Start a local dashboard with:

```powershell
scpi-emulator --load detailed_instruments.csv --start --web
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
