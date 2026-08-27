"""Catalog-backed transactional bench composition and socket startup."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from scpi_emulator.drivers import (
    CatalogError,
    DriverCatalog,
    InstrumentRequest,
    SupportLevel,
)

from .model import (
    BenchCompositionError,
    BenchDefinition,
    BenchInstrument,
    BenchStartError,
    thaw,
)


@dataclass(frozen=True)
class ComposedInstrument:
    definition: BenchInstrument
    instrument: object
    resource_template: str

    def resource_name(self, *, host: str | None = None) -> str:
        return self.definition.resource.render(self.resource_template, host=host)


@dataclass(frozen=True)
class ComposedBench:
    definition: BenchDefinition
    instruments: tuple[ComposedInstrument, ...]

    def instrument(self, instrument_id: str) -> ComposedInstrument:
        for instrument in self.instruments:
            if instrument.definition.id.casefold() == instrument_id.casefold():
                return instrument
        raise BenchCompositionError(f"composed bench has no instrument {instrument_id!r}")

    def resources(self, *, host: str | None = None) -> dict[str, str]:
        return {
            instrument.definition.id: instrument.resource_name(host=host)
            for instrument in self.instruments
        }

    def start(self, *, bind_host: str | None = None) -> "BenchRuntime":
        runtime = BenchRuntime(self)
        runtime.start(bind_host=bind_host)
        return runtime


class BenchComposer:
    """Validate all selections into local state before exposing a composed bench."""

    def __init__(self, catalog: DriverCatalog) -> None:
        self.catalog = catalog

    def compose(self, definition: BenchDefinition) -> ComposedBench:
        plans = tuple(self._plan(item) for item in definition.instruments)
        created: list[ComposedInstrument] = []
        try:
            for item, template in plans:
                request = InstrumentRequest(
                    instrument_id=item.id,
                    name=item.name,
                    model=item.model,
                    firmware=item.firmware,
                    serial_number=item.serial_number,
                    reported_model=item.reported_model,
                    configuration=thaw(item.configuration),
                )
                created.append(
                    ComposedInstrument(item, self.catalog.create(item.driver, request), template)
                )
        except Exception as error:
            raise BenchCompositionError(
                f"bench {definition.name!r} could not compose instrument {item.id!r}: {error}"
            ) from error
        return ComposedBench(definition, tuple(created))

    def _plan(self, item: BenchInstrument) -> tuple[BenchInstrument, str]:
        try:
            descriptor = self.catalog.get(item.driver).descriptor
            descriptor.model(item.model)
        except CatalogError as error:
            raise BenchCompositionError(f"instrument {item.id!r}: {error}") from error
        transport = next(
            (
                transport
                for transport in descriptor.transports
                if transport.name.casefold() == item.resource.transport.casefold()
            ),
            None,
        )
        if transport is None:
            raise BenchCompositionError(
                f"instrument {item.id!r}: driver {item.driver!r} does not advertise "
                f"transport {item.resource.transport!r}"
            )
        if transport.support is not SupportLevel.IMPLEMENTED:
            raise BenchCompositionError(
                f"instrument {item.id!r}: transport {transport.name!r} is "
                f"{transport.support.value}, not implemented"
            )
        return item, transport.resource_template


class BenchRuntime:
    """Rollback-safe collection of transport servers for one composed bench."""

    def __init__(self, bench: ComposedBench) -> None:
        self.bench = bench
        self.web_dashboard = None
        self.servers: dict[str, object] = {}
        self.running = False
        self.bind_host: str | None = None
        self._last_bind_host: str | None = None
        self._lock = threading.RLock()

    def start(self, *, bind_host: str | None = None) -> None:
        from scpi_emulator.hislip_transport import HiSLIPServer
        from scpi_emulator.raw_server import SCPIServer
        from scpi_emulator.vxi11_transport import VXI11Server

        with self._lock:
            if self.running or self.servers:
                raise BenchStartError("bench runtime is already started")
            endpoints: set[tuple[str, int]] = set()
            for composed in self.bench.instruments:
                resource = composed.definition.resource
                if resource.transport.casefold() not in {"raw-socket", "vxi-11", "hislip"}:
                    raise BenchStartError(
                        f"runtime cannot start transport {resource.transport!r} yet"
                    )
                host = resource.host if bind_host is None else bind_host
                if not isinstance(host, str) or not host.strip():
                    raise BenchStartError("bind host must be a non-empty string")
                endpoint = host.casefold(), resource.port
                if endpoint in endpoints:
                    raise BenchStartError(
                        f"bind-host override creates duplicate endpoint {host}:{resource.port}"
                    )
                endpoints.add(endpoint)

            started: list[object] = []
            try:
                for composed in self.bench.instruments:
                    resource = composed.definition.resource
                    host = resource.host if bind_host is None else bind_host
                    if resource.transport.casefold() == "raw-socket":
                        server = SCPIServer(
                            composed.instrument,
                            self,
                            host=host,
                            port=resource.port,
                        )
                    elif resource.transport.casefold() == "vxi-11":
                        server = VXI11Server(
                            composed.instrument,
                            host=host,
                            portmapper_port=resource.port,
                        )
                    else:
                        server = HiSLIPServer(
                            composed.instrument,
                            host=host,
                            port=resource.port,
                        )
                    if not server.start():
                        server.stop()
                        raise BenchStartError(
                            f"could not bind {composed.definition.id!r} to {host}:{resource.port}"
                        )
                    self.servers[composed.definition.id] = server
                    started.append(server)
            except Exception:
                for server in reversed(started):
                    server.stop()
                    if server.thread:
                        server.thread.join(timeout=1)
                self.servers.clear()
                self.running = False
                raise
            self.bind_host = bind_host
            self._last_bind_host = bind_host
            self.running = True

    def stop(self) -> None:
        with self._lock:
            for server in self.servers.values():
                server.stop()
            for server in self.servers.values():
                if server.thread:
                    server.thread.join(timeout=1)
            self.servers.clear()
            self.running = False
            self.bind_host = None

    @property
    def instruments(self) -> dict[str, dict[str, object]]:
        return {
            item.definition.id: {
                "instrument": item.instrument,
                "port": item.definition.resource.port,
            }
            for item in self.bench.instruments
        }

    def start_all_servers(self) -> bool:
        if not self.running:
            self.start(bind_host=self._last_bind_host)
        return True

    def stop_all_servers(self) -> None:
        self.stop()

    def shutdown(self) -> None:
        if self.web_dashboard is not None:
            self.web_dashboard.stop()
            self.web_dashboard = None
        self.stop()

    def start_web_dashboard(
        self,
        host: str = "127.0.0.1",
        port: int = 8081,
        *,
        auth_token: str | None = None,
    ) -> bool:
        from scpi_emulator.dashboard import WebDashboard

        self.web_dashboard = WebDashboard(self, host, port, auth_token=auth_token)
        return self.web_dashboard.start()

    def __enter__(self) -> "BenchRuntime":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()
