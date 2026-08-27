# Dashboard security

The web dashboard is a control plane: its routes can start and stop servers, restart instruments,
and execute SCPI commands. It therefore binds to `127.0.0.1` by default and is not remotely exposed
unless authentication is explicitly configured.

## Remote access

Set a strong random token in the process environment and choose a non-loopback bind address:

```powershell
$env:SCPI_EMULATOR_WEB_TOKEN = '<random secret>'
scpi-emulator --web --web-host 0.0.0.0
```

The browser asks for this token and keeps it in session storage. Same-origin API requests send it as
a bearer token. The server refuses a remote bind when no token is configured. Avoid putting the
token directly on a command line, where process inspection and shell history can expose it.

## Request protections

All state-changing API routes require a per-process CSRF token in `X-SCPI-CSRF`. JSON command input
must be an object containing text, and both Flask request size and SCPI command size are bounded.
Responses include no-sniff, anti-framing, referrer, no-store API headers, and a strict CSP that
allows scripts and styles only from the dashboard's own packaged static directory.

Dashboard commands acquire the same exclusive session lock as a raw TCP client. When a client owns
the instrument, a dashboard command receives HTTP 409 instead of mutating its state concurrently.

Authenticated automation clients can obtain the same per-process mutation token from
`GET /api/session`. Scenario selection and lifecycle endpoints use the identical bearer/CSRF checks;
fault injection queues an error through the instrument's SCPI status system rather than creating a
dashboard-only failure flag.
Start, stop, and restart actions are also serialized with one another.

Instrument names, commands, responses, and errors are escaped before history-card rendering.
Untrusted instrument data used for live command results is assigned through `textContent`.
