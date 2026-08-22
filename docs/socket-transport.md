# Raw SCPI socket transport

The raw TCP server behaves like a directly connected instrument, not a shared multi-user application
server. Its default port is 5025 and VISA clients address it with a SOCKET resource such as
`TCPIP0::127.0.0.1::5025::SOCKET`.

## Session behavior

One client owns an instrument connection at a time. A second TCP connection is rejected while the
first remains active, so two test programs cannot mutate the same channel, trigger, status, or error
state concurrently. Disconnecting, reaching the configured client-idle timeout, or stopping the
server releases the session.

Opening a session performs VISA Device Clear once. Device Clear resets transport-facing queues and
pending operations; it does not erase configured instrument values. This is deliberately distinct
from `*CLS` and `*RST`.

## Message framing

`SocketTransportConfig` controls the transport:

```python
from scpi_emulator.socket_transport import SocketTransportConfig

config = SocketTransportConfig(
    read_terminations=(b"\r\n", b"\n", b"\r"),
    write_termination=b"\n",
    max_message_size=16 * 1024 * 1024,
    idle_frame_timeout=0.3,
    client_idle_timeout=None,
    send_timeout=10.0,
)
```

Pass the configuration as `transport_config=` when constructing `SCPIServer`. Read terminators are
recognized across fragmented TCP receives. Terminator bytes inside quoted strings or an IEEE 488.2
definite binary block are data and do not end the message. Multiple complete messages received in a
single packet are dispatched in order.

The default 0.3-second framing timeout retains compatibility with older clients that omit a write
terminator. Set `idle_frame_timeout=None` to require explicit termination. `client_idle_timeout`
controls how long an otherwise idle session may retain the instrument; its default `None` leaves the
session open until disconnect.

## Bounds and backpressure

Input is accumulated only up to `max_message_size`. Oversized messages and declared binary blocks
are rejected and the connection is closed, preventing an unbounded receive buffer. Each response is
held in the bounded SCPI output queue and sent with `send_timeout`; a client that stops reading cannot
block the instrument indefinitely.

Stopping a server closes its listener and active client, waits for the client worker to leave, and
releases the session lock. The same server object can therefore be replaced cleanly by transactional
bench composition.

## Binary data

Commands remain bytes through transport framing and typed SCPI parsing. Definite binary-block
payloads can contain arbitrary bytes, including CR, LF, semicolons, and non-UTF-8 values. Text
decoding is used only for legacy command fallback and human-readable dashboard logging. Responses
use the output queue's configured termination and preserve IEEE binary blocks without conversion.

The regression suite covers fragmented and chained messages, quoted terminators, binary input,
megabyte-scale binary output, custom termination, oversized input, session rejection, idle release,
OPC/status handshakes, and shutdown with an active client.
