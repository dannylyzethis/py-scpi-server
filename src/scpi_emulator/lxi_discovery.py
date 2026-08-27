"""Optional LXI-compatible DNS-SD advertisements for LAN transports."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

HISLIP_SERVICE_TYPE = "_hislip._tcp.local."
VXI11_SERVICE_TYPE = "_vxi-11._tcp.local."


class DiscoveryUnavailable(RuntimeError):
    """The optional zeroconf discovery dependency is not installed."""


@dataclass(frozen=True)
class LXIServiceRecord:
    service_type: str
    name: str
    server: str
    address: bytes
    port: int
    properties: dict[str, str]


def build_lxi_service_records(
    instrument,
    *,
    host: str,
    hostname: str | None = None,
    service_name: str | None = None,
    hislip_port: int | None = None,
    vxi11_port: int | None = None,
) -> tuple[LXIServiceRecord, ...]:
    """Build the records shared by zeroconf advertising and deterministic tests."""
    if hislip_port is None and vxi11_port is None:
        raise ValueError("at least one discovery transport port is required")
    identity = str(instrument.process_command("*IDN?")).split(",", 3)
    identity.extend([""] * (4 - len(identity)))
    manufacturer, model, serial, firmware = (part.strip() for part in identity[:4])
    properties = {
        "txtvers": "1",
        "Manufacturer": manufacturer,
        "Model": model,
        "SerialNumber": serial,
        "FirmwareVersion": firmware,
    }
    resolved = _advertised_address(host)
    advertised_hostname = _hostname(hostname or socket.gethostname())
    instance = (service_name or f"{model}-{serial}").strip() or "SCPI-Instrument"
    records: list[LXIServiceRecord] = []
    if hislip_port is not None:
        hislip_properties = dict(properties)
        hislip_properties["VisaAddress"] = (
            f"TCPIP::{advertised_hostname}::hislip0,{hislip_port}::INSTR"
        )
        records.append(
            LXIServiceRecord(
                HISLIP_SERVICE_TYPE,
                f"{instance}.{HISLIP_SERVICE_TYPE}",
                advertised_hostname,
                resolved.packed,
                _port(hislip_port),
                hislip_properties,
            )
        )
    if vxi11_port is not None:
        vxi11_properties = dict(properties)
        vxi11_properties["VisaAddress"] = f"TCPIP::{advertised_hostname}::inst0::INSTR"
        records.append(
            LXIServiceRecord(
                VXI11_SERVICE_TYPE,
                f"{instance}.{VXI11_SERVICE_TYPE}",
                advertised_hostname,
                resolved.packed,
                _port(vxi11_port),
                vxi11_properties,
            )
        )
    return tuple(records)


class LXIDiscoveryAdvertiser:
    """Register one or both transport records through the optional zeroconf package."""

    def __init__(
        self,
        instrument,
        *,
        host: str,
        hostname: str | None = None,
        service_name: str | None = None,
        hislip_port: int | None = None,
        vxi11_port: int | None = None,
        interfaces=None,
    ) -> None:
        self.records = build_lxi_service_records(
            instrument,
            host=host,
            hostname=hostname,
            service_name=service_name,
            hislip_port=hislip_port,
            vxi11_port=vxi11_port,
        )
        self.interfaces = interfaces
        self.zeroconf = None
        self.services: list[object] = []

    def start(self) -> None:
        if self.zeroconf is not None:
            raise RuntimeError("LXI discovery is already running")
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError as error:
            raise DiscoveryUnavailable(
                "LXI discovery requires the 'discovery' extra: "
                "pip install scpi-instrument-emulator[discovery]"
            ) from error
        kwargs = {} if self.interfaces is None else {"interfaces": self.interfaces}
        zeroconf = Zeroconf(**kwargs)
        services = []
        try:
            for record in self.records:
                info = ServiceInfo(
                    record.service_type,
                    record.name,
                    addresses=[record.address],
                    port=record.port,
                    properties=record.properties,
                    server=record.server,
                )
                zeroconf.register_service(info)
                services.append(info)
        except Exception:
            for info in reversed(services):
                zeroconf.unregister_service(info)
            zeroconf.close()
            raise
        self.zeroconf = zeroconf
        self.services = services

    def stop(self) -> None:
        zeroconf, self.zeroconf = self.zeroconf, None
        services, self.services = self.services, []
        if zeroconf is None:
            return
        for info in reversed(services):
            zeroconf.unregister_service(info)
        zeroconf.close()

    def __enter__(self) -> "LXIDiscoveryAdvertiser":
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()


def _hostname(value: str) -> str:
    value = value.strip().rstrip(".")
    if not value:
        raise ValueError("discovery hostname must be non-empty")
    return f"{value}.local."


def _advertised_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    selected = host.strip()
    if selected in {"", "0.0.0.0", "::"}:
        selected = socket.gethostbyname(socket.gethostname())
    elif selected.casefold() == "localhost":
        selected = "127.0.0.1"
    try:
        return ipaddress.ip_address(selected)
    except ValueError:
        return ipaddress.ip_address(socket.gethostbyname(selected))


def _port(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("discovery port must be between 1 and 65535")
    return value
