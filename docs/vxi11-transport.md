# VXI-11 INSTR transport

The emulator implements the VXI-11 Revision 1.0 core and asynchronous RPC programs so ordinary VISA
clients can use `TCPIP::<host>::INSTR` resources. Instrument command behavior remains in the shared
SCPI core; VXI-11 is only a transport and bus-operation adapter.

## RPC programs

- Device core `0x0607AF`, version 1: link creation, reads, writes, serial poll, trigger, clear,
  remote/local, lock/unlock, SRQ configuration, and interrupt-channel lifecycle.
- Device asynchronous `0x0607B0`, version 1: abort pending instrument work for a link.
- Device interrupt `0x0607B1`, version 1: client callback used to deliver service requests.
- ONC portmapper `100000`, version 2: TCP `GETPORT` for the core and abort programs.

RPC-over-TCP record fragments and XDR variable data are bounded. The maximum accepted VXI-11 write
message defaults to 1 MiB and is returned by `create_link` so clients split larger transfers into
legal chunks.

## Instrument semantics

One VXI-11 link owns an instrument at a time, preventing concurrent mutation by multiple clients.
VXI-11 writes are accumulated until the protocol END flag and then dispatched as one byte-preserving
SCPI message. Reads are chunked to the requested size and report request-count, termination-character,
and message-end reasons correctly.

Bus operations map directly onto shared behavior:

- `device_clear` invokes VISA Device Clear without erasing configured measurement values;
- `device_trigger` injects the acquisition controller's bus trigger;
- `device_readstb` returns the computed IEEE 488.2 status byte;
- `device_abort` cancels pending operations;
- lock and link requests enforce exclusive ownership.

After a client creates an interrupt channel and enables SRQ, a rising master-status-summary condition
causes a `device_intr_srq` callback carrying the client's opaque handle. This makes the classic
`*ESE 1`, `*SRE 32`, `*OPC` handshake observable through a transport-level service request rather
than requiring status polling.

## Running and testing

The standard portmapper listens on TCP port 111. Binding that port can require elevated privileges
on Unix-like systems. `VXI11Server` also supports an arbitrary portmapper port and an ephemeral core
port for isolated tests. PyVISA-Py supports bypassing portmapper lookup with the development resource
form `TCPIP0::127.0.0.1,<core-port>::inst0::INSTR`.

The test suite exercises RPC record/XDR encoding, port lookup, real PyVISA-Py INSTR query/write/read,
Device Clear state preservation, bus trigger, exclusive links, abort, and serial poll. A native
NI-VISA session also enables the PyVISA service-request event queue and proves that an OPC completion
arrives through the VXI-11 interrupt channel as SRQ.
