"""VXI-11 Revision 1.0 transport over ONC RPC/XDR."""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from itertools import count


PORTMAP_PROGRAM = 100000
PORTMAP_VERSION = 2
DEVICE_CORE_PROGRAM = 0x0607AF
DEVICE_ASYNC_PROGRAM = 0x0607B0
DEVICE_INTR_PROGRAM = 0x0607B1
DEVICE_VERSION = 1

RPC_CALL = 0
RPC_REPLY = 1
RPC_VERSION = 2
RPC_ACCEPTED = 0
RPC_SUCCESS = 0
RPC_PROC_UNAVAILABLE = 3
RPC_GARBAGE_ARGS = 4

VXI_SUCCESS = 0
VXI_SYNTAX_ERROR = 1
VXI_DEVICE_NOT_ACCESSIBLE = 3
VXI_INVALID_LINK = 4
VXI_PARAMETER_ERROR = 5
VXI_CHANNEL_NOT_ESTABLISHED = 6
VXI_OPERATION_NOT_SUPPORTED = 8
VXI_DEVICE_LOCKED = 11
VXI_NO_LOCK_HELD = 12
VXI_IO_TIMEOUT = 15

FLAG_WAITLOCK = 1
FLAG_END = 8
FLAG_TERMCHAR = 128
READ_REASON_REQCNT = 1
READ_REASON_TERMCHAR = 2
READ_REASON_END = 4


class XdrError(ValueError):
    """An RPC value is truncated or malformed."""


class XdrReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def u32(self) -> int:
        if self.offset + 4 > len(self.data):
            raise XdrError("truncated XDR integer")
        value = struct.unpack_from("!I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def i32(self) -> int:
        value = self.u32()
        return value - (1 << 32) if value & (1 << 31) else value

    def boolean(self) -> bool:
        value = self.u32()
        if value not in (0, 1):
            raise XdrError("invalid XDR boolean")
        return bool(value)

    def opaque(self, *, maximum: int | None = None) -> bytes:
        length = self.u32()
        if maximum is not None and length > maximum:
            raise XdrError("XDR opaque value exceeds its bound")
        end = self.offset + length
        padded = end + (-length % 4)
        if padded > len(self.data):
            raise XdrError("truncated XDR opaque value")
        value = self.data[self.offset:end]
        self.offset = padded
        return value

    def string(self, *, maximum: int | None = None) -> str:
        try:
            return self.opaque(maximum=maximum).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise XdrError("XDR string is not UTF-8") from exc

    def skip_auth(self) -> None:
        self.u32()
        self.opaque()


class XdrWriter:
    def __init__(self) -> None:
        self.parts: list[bytes] = []

    def u32(self, value: int) -> "XdrWriter":
        self.parts.append(struct.pack("!I", value & 0xFFFFFFFF))
        return self

    def i32(self, value: int) -> "XdrWriter":
        return self.u32(value)

    def boolean(self, value: bool) -> "XdrWriter":
        return self.u32(1 if value else 0)

    def opaque(self, value: bytes) -> "XdrWriter":
        value = bytes(value)
        self.u32(len(value))
        self.parts.append(value)
        if len(value) % 4:
            self.parts.append(b"\0" * (-len(value) % 4))
        return self

    def build(self) -> bytes:
        return b"".join(self.parts)


def _recv_exact(connection: socket.socket, size: int) -> bytes | None:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            return None
        result.extend(chunk)
    return bytes(result)


def receive_record(connection: socket.socket, *, maximum: int = 32 * 1024 * 1024) -> bytes | None:
    record = bytearray()
    while True:
        marker = _recv_exact(connection, 4)
        if marker is None:
            return None if not record else bytes(record)
        value = struct.unpack("!I", marker)[0]
        length = value & 0x7FFFFFFF
        if len(record) + length > maximum:
            raise XdrError("RPC record exceeds input limit")
        fragment = _recv_exact(connection, length)
        if fragment is None:
            raise XdrError("truncated RPC record")
        record.extend(fragment)
        if value & 0x80000000:
            return bytes(record)


def send_record(connection: socket.socket, data: bytes) -> None:
    connection.sendall(struct.pack("!I", 0x80000000 | len(data)) + data)


class RpcTcpServer:
    """Small bounded ONC RPC/TCP server for one program and version."""

    def __init__(self, program: int, version: int, dispatcher, *, host="127.0.0.1", port=0):
        self.program = program
        self.version = version
        self.dispatcher = dispatcher
        self.host = host
        self.port = port
        self.socket: socket.socket | None = None
        self.running = False
        self.thread: threading.Thread | None = None
        self._clients: set[socket.socket] = set()
        self._lock = threading.RLock()

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(8)
        listener.settimeout(0.2)
        self.socket = listener
        self.port = listener.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.socket:
            self.socket.close()
            self.socket = None
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)

    def _accept(self) -> None:
        while self.running:
            try:
                client, _ = self.socket.accept()
            except socket.timeout:
                continue
            except (OSError, AttributeError):
                break
            client.settimeout(5)
            with self._lock:
                self._clients.add(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, connection: socket.socket) -> None:
        try:
            while self.running:
                record = receive_record(connection)
                if record is None:
                    break
                send_record(connection, self._reply(record))
        except (OSError, XdrError):
            pass
        finally:
            with self._lock:
                self._clients.discard(connection)
            connection.close()

    def _reply(self, record: bytes) -> bytes:
        reader = XdrReader(record)
        xid = reader.u32()
        try:
            if reader.u32() != RPC_CALL or reader.u32() != RPC_VERSION:
                raise XdrError("invalid RPC call header")
            program, version, procedure = reader.u32(), reader.u32(), reader.u32()
            reader.skip_auth()
            reader.skip_auth()
            if program != self.program or version != self.version:
                status, payload = RPC_PROC_UNAVAILABLE, b""
            else:
                payload = self.dispatcher(procedure, reader)
                status = RPC_SUCCESS
        except XdrError:
            status, payload = RPC_GARBAGE_ARGS, b""
        return (
            XdrWriter()
            .u32(xid)
            .u32(RPC_REPLY)
            .u32(RPC_ACCEPTED)
            .u32(0)
            .opaque(b"")
            .u32(status)
            .build()
            + payload
        )


@dataclass
class _Link:
    identifier: int
    writes: bytearray = field(default_factory=bytearray)
    response: bytearray = field(default_factory=bytearray)
    locked: bool = False
    srq_enabled: bool = False
    srq_handle: bytes = b""


class VXI11Server:
    """Expose one SCPI instrument through VXI-11 core, async, and interrupt RPC."""

    def __init__(
        self,
        instrument,
        *,
        host="127.0.0.1",
        core_port=0,
        abort_port=0,
        portmapper_port=111,
        max_receive_size=1024 * 1024,
    ) -> None:
        self.instrument = instrument
        self.host = host
        self.portmapper_port = portmapper_port
        self.max_receive_size = max_receive_size
        self._links: dict[int, _Link] = {}
        self._next_link = count(1)
        self._lock = threading.RLock()
        self._interrupt_target: tuple[str, int, int, int] | None = None
        self._srq_asserted = False
        self.running = False
        self.core = RpcTcpServer(
            DEVICE_CORE_PROGRAM, DEVICE_VERSION, self._dispatch_core, host=host, port=core_port
        )
        self.async_server = RpcTcpServer(
            DEVICE_ASYNC_PROGRAM,
            DEVICE_VERSION,
            self._dispatch_async,
            host=host,
            port=abort_port,
        )
        self.portmapper = RpcTcpServer(
            PORTMAP_PROGRAM,
            PORTMAP_VERSION,
            self._dispatch_portmap,
            host=host,
            port=portmapper_port,
        )
        self._srq_thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.core.port

    @property
    def thread(self):
        """Compatibility with bench runtime server lifecycle handling."""
        return self.core.thread

    def start(self) -> bool:
        try:
            self.async_server.start()
            self.core.start()
            self.portmapper.start()
        except OSError:
            self.stop()
            return False
        self.running = True
        self._srq_thread = threading.Thread(target=self._monitor_srq, daemon=True)
        self._srq_thread.start()
        return True

    def stop(self) -> None:
        self.running = False
        self.portmapper.stop()
        self.core.stop()
        self.async_server.stop()
        with self._lock:
            self._links.clear()
            self._interrupt_target = None
        if self._srq_thread and self._srq_thread is not threading.current_thread():
            self._srq_thread.join(timeout=1)

    def _dispatch_portmap(self, procedure: int, reader: XdrReader) -> bytes:
        if procedure == 0:
            return b""
        if procedure != 3:
            raise XdrError("unsupported portmapper procedure")
        program, version, protocol = reader.u32(), reader.u32(), reader.u32()
        reader.u32()
        port = 0
        if version == DEVICE_VERSION and protocol == socket.IPPROTO_TCP:
            if program == DEVICE_CORE_PROGRAM:
                port = self.core.port
            elif program == DEVICE_ASYNC_PROGRAM:
                port = self.async_server.port
        return XdrWriter().u32(port).build()

    def _dispatch_core(self, procedure: int, reader: XdrReader) -> bytes:
        handlers = {
            0: lambda value: b"",
            10: self._create_link,
            11: self._device_write,
            12: self._device_read,
            13: self._device_read_stb,
            14: self._device_trigger,
            15: self._device_clear,
            16: self._generic_success,
            17: self._generic_success,
            18: self._device_lock,
            19: self._device_unlock,
            20: self._device_enable_srq,
            23: self._destroy_link,
            25: self._create_interrupt_channel,
            26: self._destroy_interrupt_channel,
        }
        try:
            handler = handlers[procedure]
        except KeyError as exc:
            raise XdrError("unsupported VXI-11 procedure") from exc
        return handler(reader)

    def _dispatch_async(self, procedure: int, reader: XdrReader) -> bytes:
        if procedure == 0:
            return b""
        if procedure != 1:
            raise XdrError("unsupported async procedure")
        link = self._link(reader.i32())
        if link is None:
            return self._error(VXI_INVALID_LINK)
        link.response.clear()
        self.instrument.status.set_output_queue_count(0)
        self.instrument.operation_manager.abort()
        return self._error(VXI_SUCCESS)

    def _create_link(self, reader: XdrReader) -> bytes:
        reader.i32()
        lock_device = reader.boolean()
        reader.u32()
        device = reader.string(maximum=255)
        if not device:
            return XdrWriter().i32(VXI_DEVICE_NOT_ACCESSIBLE).i32(0).u32(0).u32(0).build()
        with self._lock:
            if self._links:
                return XdrWriter().i32(VXI_DEVICE_LOCKED).i32(0).u32(0).u32(0).build()
            identifier = next(self._next_link)
            self._links[identifier] = _Link(identifier, locked=lock_device)
        self.instrument.visa_device_clear()
        return (
            XdrWriter()
            .i32(VXI_SUCCESS)
            .i32(identifier)
            .u32(self.async_server.port)
            .u32(self.max_receive_size)
            .build()
        )

    def _device_write(self, reader: XdrReader) -> bytes:
        link = self._link(reader.i32())
        reader.u32()
        reader.u32()
        flags = reader.i32()
        data = reader.opaque(maximum=self.max_receive_size)
        if link is None:
            return XdrWriter().i32(VXI_INVALID_LINK).u32(0).build()
        if len(link.writes) + len(data) > self.max_receive_size:
            link.writes.clear()
            return XdrWriter().i32(VXI_PARAMETER_ERROR).u32(0).build()
        link.writes.extend(data)
        if flags & FLAG_END:
            message = bytes(link.writes)
            link.writes.clear()
            for terminator in (b"\r\n", b"\n", b"\r"):
                if message.endswith(terminator):
                    message = message[: -len(terminator)]
                    break
            self.instrument.queue_command_response(message, termination=b"")
            link.response[:] = self.instrument.read_output()
            self.instrument.status.set_output_queue_count(len(link.response))
        return XdrWriter().i32(VXI_SUCCESS).u32(len(data)).build()

    def _device_read(self, reader: XdrReader) -> bytes:
        link = self._link(reader.i32())
        request_size = reader.u32()
        reader.u32()
        reader.u32()
        flags = reader.i32()
        term_char = reader.u32() & 0xFF
        if link is None:
            return XdrWriter().i32(VXI_INVALID_LINK).i32(0).opaque(b"").build()
        if request_size < 1:
            return XdrWriter().i32(VXI_PARAMETER_ERROR).i32(0).opaque(b"").build()
        if not link.response:
            return XdrWriter().i32(VXI_IO_TIMEOUT).i32(0).opaque(b"").build()
        length = min(request_size, len(link.response))
        reason = 0
        if flags & FLAG_TERMCHAR:
            try:
                length = min(length, link.response.index(term_char, 0, length) + 1)
                reason |= READ_REASON_TERMCHAR
            except ValueError:
                pass
        data = bytes(link.response[:length])
        del link.response[:length]
        self.instrument.status.set_output_queue_count(len(link.response))
        if length == request_size:
            reason |= READ_REASON_REQCNT
        if not link.response:
            reason |= READ_REASON_END
        return XdrWriter().i32(VXI_SUCCESS).i32(reason).opaque(data).build()

    def _device_read_stb(self, reader: XdrReader) -> bytes:
        link = self._generic_link(reader)
        error = VXI_SUCCESS if link else VXI_INVALID_LINK
        return XdrWriter().i32(error).u32(self.instrument.status.status_byte if link else 0).build()

    def _device_trigger(self, reader: XdrReader) -> bytes:
        link = self._generic_link(reader)
        if link is None:
            return self._error(VXI_INVALID_LINK)
        self.instrument.acquisition.bus_trigger()
        return self._error(VXI_SUCCESS)

    def _device_clear(self, reader: XdrReader) -> bytes:
        link = self._generic_link(reader)
        if link is None:
            return self._error(VXI_INVALID_LINK)
        link.writes.clear()
        link.response.clear()
        self.instrument.visa_device_clear()
        return self._error(VXI_SUCCESS)

    def _generic_success(self, reader: XdrReader) -> bytes:
        return self._error(VXI_SUCCESS if self._generic_link(reader) else VXI_INVALID_LINK)

    def _device_lock(self, reader: XdrReader) -> bytes:
        link = self._link(reader.i32())
        reader.i32()
        reader.u32()
        if link is None:
            return self._error(VXI_INVALID_LINK)
        if link.locked:
            return self._error(VXI_DEVICE_LOCKED)
        link.locked = True
        return self._error(VXI_SUCCESS)

    def _device_unlock(self, reader: XdrReader) -> bytes:
        link = self._link(reader.i32())
        if link is None:
            return self._error(VXI_INVALID_LINK)
        if not link.locked:
            return self._error(VXI_NO_LOCK_HELD)
        link.locked = False
        return self._error(VXI_SUCCESS)

    def _device_enable_srq(self, reader: XdrReader) -> bytes:
        link = self._link(reader.i32())
        enable = reader.boolean()
        handle = reader.opaque(maximum=40)
        if link is None:
            return self._error(VXI_INVALID_LINK)
        link.srq_enabled = enable
        link.srq_handle = handle
        return self._error(VXI_SUCCESS)

    def _destroy_link(self, reader: XdrReader) -> bytes:
        identifier = reader.i32()
        with self._lock:
            link = self._links.pop(identifier, None)
            if link is None:
                return self._error(VXI_INVALID_LINK)
        if link.response:
            self.instrument.status.set_output_queue_count(0)
        return self._error(VXI_SUCCESS)

    def _create_interrupt_channel(self, reader: XdrReader) -> bytes:
        host_address = reader.u32()
        host_port = reader.u32()
        program = reader.u32()
        version = reader.u32()
        family = reader.i32()
        if family != 0 or not host_port:
            return self._error(VXI_PARAMETER_ERROR)
        host = socket.inet_ntoa(struct.pack("!I", host_address))
        self._interrupt_target = host, host_port, program, version
        return self._error(VXI_SUCCESS)

    def _destroy_interrupt_channel(self, reader: XdrReader) -> bytes:
        self._interrupt_target = None
        return self._error(VXI_SUCCESS)

    def _generic_link(self, reader: XdrReader) -> _Link | None:
        link = self._link(reader.i32())
        reader.i32()
        reader.u32()
        reader.u32()
        return link

    def _link(self, identifier: int) -> _Link | None:
        with self._lock:
            return self._links.get(identifier)

    @staticmethod
    def _error(error: int) -> bytes:
        return XdrWriter().i32(error).build()

    def _monitor_srq(self) -> None:
        while self.running:
            requesting = self.instrument.status.requesting_service
            if requesting and not self._srq_asserted:
                self._srq_asserted = True
                self._send_srq()
            elif not requesting:
                self._srq_asserted = False
            time.sleep(0.01)

    def _send_srq(self) -> None:
        target = self._interrupt_target
        if target is None:
            return
        with self._lock:
            link = next((item for item in self._links.values() if item.srq_enabled), None)
        if link is None:
            return
        host, port, program, version = target
        xid = int(time.monotonic_ns() & 0xFFFFFFFF)
        payload = (
            XdrWriter()
            .u32(xid)
            .u32(RPC_CALL)
            .u32(RPC_VERSION)
            .u32(program or DEVICE_INTR_PROGRAM)
            .u32(version or DEVICE_VERSION)
            .u32(30)
            .u32(0)
            .opaque(b"")
            .u32(0)
            .opaque(b"")
            .opaque(link.srq_handle)
            .build()
        )
        try:
            with socket.create_connection((host, port), timeout=1) as connection:
                send_record(connection, payload)
                connection.settimeout(1)
                receive_record(connection)
        except OSError:
            pass
