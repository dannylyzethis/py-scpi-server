# HiSLIP transport and LXI discovery

The emulator implements the interoperable HiSLIP 1.0 subset used by ordinary VISA clients. HiSLIP
is only a LAN transport: every received SCPI program message is executed by the same instrument,
status, operation, acquisition, and scenario systems used by raw TCP and VXI-11.

## Sessions and protocol behavior

A HiSLIP session uses the required synchronous and asynchronous TCP channels. The server negotiates
the protocol version and maximum message size, reassembles bounded `Data`/`DataEnd` messages, and
returns byte-preserving `DataEnd` responses with the matching message ID. One paired session owns an
instrument at a time; another client is refused until both sockets from the first session close.

The transport bridges these VISA and IEEE operations into the common instrument core:

- Device Clear abandons pending operations and clears transport, output, error, and event status,
  but does not erase configured instrument values.
- Trigger delivers a bus-trigger event to the acquisition controller.
- asynchronous status queries return the live IEEE 488.2 status byte;
- exclusive lock request, release, and lock-info transactions are supported; and
- a rising internal service-request condition sends `AsyncServiceRequest`, including the classic
  `*ESE 1`; `*SRE 32`; `*OPC` completion handshake.

The server bounds both individual payloads and reassembled program messages. Malformed headers,
invalid initialization, unsupported messages, and oversized payloads receive protocol errors and do
not reach the SCPI command registry. HiSLIP 2.0 TLS, SASL authentication, and descriptors are not
implemented; the server negotiates version 1.0 for broad compatibility.

## Direct use

```python
from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.hislip_transport import HiSLIPServer

instrument = SCPIInstrument("Virtual DMM", "dmm-1")
server = HiSLIPServer(instrument, host="0.0.0.0", port=4880)
if not server.start():
    raise RuntimeError("HiSLIP port could not be opened")
```

The standard VISA resource is:

```text
TCPIP0::<host>::hislip0::INSTR
```

For a nonstandard development port, PyVISA-Py accepts:

```text
TCPIP0::<host>::hislip0,<port>::INSTR
```

Reusable bench definitions select `"transport": "hislip"`; their `resource.port` is the HiSLIP
listener port. Both the built-in VNA and DMM drivers advertise this transport as
implemented.

## Optional mDNS/DNS-SD advertisement

Install the discovery extra:

```bash
python -m pip install -e ".[discovery]"
```

Then request advertisement when constructing the server:

```python
server = HiSLIPServer(
    instrument,
    host="0.0.0.0",
    port=4880,
    advertise=True,
    discovery_hostname="virtual-dmm",
)
```

This publishes `_hislip._tcp.local.` on the configured port with `txtvers`, `Manufacturer`,
`Model`, `SerialNumber`, and `FirmwareVersion` values derived from `*IDN?`. The same service instance
name can be used for `_vxi-11._tcp.local.` by constructing `LXIDiscoveryAdvertiser` directly and
passing both `hislip_port` and `vxi11_port`.

`zeroconf` is optional because multicast discovery is not needed for explicit VISA resource names.
It is licensed LGPL-2.1-or-later; it is not bundled into this MIT-licensed repository. Local mDNS
also depends on operating-system multicast routing and firewall policy.

The implementation follows the official
[IVI-6.1 HiSLIP 2.0 specification](https://www.ivifoundation.org/downloads/Protocol%20Specifications/IVI-6.1_HiSLIP-2.0-2020-04-23.pdf)
(while negotiating its backward-compatible 1.0 mode),
[LXI HiSLIP Extended Function 1.4](https://public.lxistandard.org/specifications/LXI_1.6_Specifications/LXI_HiSLIP_Extended_Function_1.4_2025-09-10.pdf),
and
[LXI VXI-11 Discovery and Identification Extended Function 1.1](https://public.lxistandard.org/specifications/LXI_1.6_Specifications/LXI_VXI-11_Discovery_and_Identification_Extended_Function_1.1_2023-06-26.pdf).
