"""Bounded HiSLIP 1.0 transport for one SCPI instrument session."""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from itertools import count

HEADER = struct.Struct("!2sBBIQ")
PROLOGUE = b"HS"
PROTOCOL_VERSION = 0x0100
DEFAULT_PORT = 4880
DEFAULT_MAX_MESSAGE_SIZE = 1024 * 1024

INITIALIZE = 0
INITIALIZE_RESPONSE = 1
FATAL_ERROR = 2
ERROR = 3
ASYNC_LOCK = 4
ASYNC_LOCK_RESPONSE = 5
DATA = 6
DATA_END = 7
DEVICE_CLEAR_COMPLETE = 8
DEVICE_CLEAR_ACKNOWLEDGE = 9
ASYNC_REMOTE_LOCAL_CONTROL = 10
ASYNC_REMOTE_LOCAL_RESPONSE = 11
TRIGGER = 12
INTERRUPTED = 13
ASYNC_INTERRUPTED = 14
ASYNC_MAX_MSG_SIZE = 15
ASYNC_MAX_MSG_SIZE_RESPONSE = 16
ASYNC_INITIALIZE = 17
ASYNC_INITIALIZE_RESPONSE = 18
ASYNC_DEVICE_CLEAR = 19
ASYNC_SERVICE_REQUEST = 20
ASYNC_STATUS_QUERY = 21
ASYNC_STATUS_RESPONSE = 22
ASYNC_DEVICE_CLEAR_ACKNOWLEDGE = 23
ASYNC_LOCK_INFO = 24
ASYNC_LOCK_INFO_RESPONSE = 25

FATAL_POOR_HEADER = 1
FATAL_CHANNELS_NOT_ESTABLISHED = 2
FATAL_INITIALIZATION = 3
FATAL_MAX_CLIENTS = 4
ERROR_UNRECOGNIZED_MESSAGE = 1
ERROR_UNRECOGNIZED_CONTROL = 2
ERROR_MESSAGE_TOO_LARGE = 4


class HiSLIPProtocolError(ValueError):
    """A HiSLIP message is malformed or violates a configured bound."""

    def __init__(self, message: str, *, code: int = FATAL_POOR_HEADER) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HiSLIPMessage:
    message_type: int
    control_code: int = 0
    parameter: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        payload = bytes(self.payload)
        return (
            HEADER.pack(
                PROLOGUE,
                self.message_type & 0xFF,
                self.control_code & 0xFF,
                self.parameter & 0xFFFFFFFF,
                len(payload),
            )
            + payload
        )


def _recv_exact(connection: socket.socket, size: int) -> bytes | None:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            if result:
                raise HiSLIPProtocolError("truncated HiSLIP message")
            return None
        result.extend(chunk)
    return bytes(result)


def receive_message(
    connection: socket.socket, *, maximum: int = DEFAULT_MAX_MESSAGE_SIZE
) -> HiSLIPMessage | None:
    header = _recv_exact(connection, HEADER.size)
    if header is None:
        return None
    prologue, message_type, control_code, parameter, payload_length = HEADER.unpack(header)
    if prologue != PROLOGUE:
        raise HiSLIPProtocolError("invalid HiSLIP prologue")
    if payload_length > maximum:
        raise HiSLIPProtocolError(
            f"HiSLIP payload exceeds {maximum} bytes", code=ERROR_MESSAGE_TOO_LARGE
        )
    payload = _recv_exact(connection, payload_length)
    if payload is None and payload_length:
        raise HiSLIPProtocolError("truncated HiSLIP payload")
    return HiSLIPMessage(message_type, control_code, parameter, payload or b"")


def send_message(connection: socket.socket, message: HiSLIPMessage) -> None:
    connection.sendall(message.encode())


@dataclass
class _Session:
    identifier: int
    sync: socket.socket
    async_socket: socket.socket | None = None
    input_buffer: bytearray = field(default_factory=bytearray)
    max_message_size: int = DEFAULT_MAX_MESSAGE_SIZE
    locked: bool = False
    clearing: bool = False
    closed: bool = False
    async_send_lock: threading.Lock = field(default_factory=threading.Lock)
    state_lock: threading.RLock = field(default_factory=threading.RLock)
    ready: threading.Event = field(default_factory=threading.Event)


class HiSLIPServer:
    """Expose one SCPI instrument using a single realistic HiSLIP session."""

    def __init__(
        self,
        instrument,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        subaddress: str = "hislip0",
        max_message_size: int = DEFAULT_MAX_MESSAGE_SIZE,
        backlog: int = 4,
        advertise: bool = False,
        discovery_hostname: str | None = None,
        discovery_service_name: str | None = None,
        discovery_interfaces=None,
    ) -> None:
        if max_message_size < HEADER.size:
            raise ValueError("max_message_size must be at least 16 bytes")
        self.instrument = instrument
        self.host = host
        self.port = port
        self.subaddress = subaddress
        self.max_message_size = max_message_size
        self.backlog = backlog
        self.advertise = advertise
        self.discovery_hostname = discovery_hostname
        self.discovery_service_name = discovery_service_name
        self.discovery_interfaces = discovery_interfaces
        self.discovery = None
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.running = False
        self._session: _Session | None = None
        self._session_ids = count(1)
        self._lock = threading.RLock()
        self._client_threads: set[threading.Thread] = set()
        self._srq_thread: threading.Thread | None = None
        self._srq_asserted = False
        self._srq_pending_since: float | None = None

    def start(self) -> bool:
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen(self.backlog)
            listener.settimeout(0.2)
        except OSError:
            try:
                listener.close()
            except (OSError, UnboundLocalError):
                pass
            return False
        self.socket = listener
        self.port = listener.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()
        self._srq_thread = threading.Thread(target=self._monitor_srq, daemon=True)
        self._srq_thread.start()
        if self.advertise:
            from .lxi_discovery import LXIDiscoveryAdvertiser

            self.discovery = LXIDiscoveryAdvertiser(
                self.instrument,
                host=self.host,
                hostname=self.discovery_hostname,
                service_name=self.discovery_service_name,
                hislip_port=self.port,
                interfaces=self.discovery_interfaces,
            )
            try:
                self.discovery.start()
            except Exception:
                self.stop()
                return False
        return True

    def stop(self) -> None:
        self.running = False
        discovery, self.discovery = self.discovery, None
        if discovery is not None:
            discovery.stop()
        listener, self.socket = self.socket, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            session = self._session
        if session is not None:
            self._close_session(session)
        current = threading.current_thread()
        if self.thread and self.thread is not current:
            self.thread.join(timeout=1)
        if self._srq_thread and self._srq_thread is not current:
            self._srq_thread.join(timeout=1)
        with self._lock:
            threads = tuple(self._client_threads)
        for thread in threads:
            if thread is not current:
                thread.join(timeout=1)

    def _accept(self) -> None:
        while self.running:
            try:
                connection, _ = self.socket.accept()
            except socket.timeout:
                continue
            except (OSError, AttributeError):
                break
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.settimeout(5)
            thread = threading.Thread(target=self._negotiate, args=(connection,), daemon=True)
            with self._lock:
                self._client_threads.add(thread)
            thread.start()

    def _negotiate(self, connection: socket.socket) -> None:
        session: _Session | None = None
        try:
            message = receive_message(connection, maximum=self.max_message_size)
            if message is None:
                return
            if message.message_type == INITIALIZE:
                session = self._initialize(connection, message)
                if session is not None:
                    self._serve_sync(session)
            elif message.message_type == ASYNC_INITIALIZE:
                session = self._async_initialize(connection, message)
                if session is not None:
                    self._serve_async(session)
            else:
                self._fatal(connection, FATAL_INITIALIZATION, "initialization required")
        except HiSLIPProtocolError as error:
            message_type = ERROR if error.code == ERROR_MESSAGE_TOO_LARGE else FATAL_ERROR
            self._safe_send(
                connection, HiSLIPMessage(message_type, error.code, payload=str(error).encode())
            )
        except (OSError, ValueError):
            pass
        finally:
            if session is not None:
                self._close_session(session)
            else:
                try:
                    connection.close()
                except OSError:
                    pass
            with self._lock:
                self._client_threads.discard(threading.current_thread())

    def _initialize(self, connection: socket.socket, message: HiSLIPMessage) -> _Session | None:
        major = (message.parameter >> 24) & 0xFF
        minor = (message.parameter >> 16) & 0xFF
        try:
            subaddress = message.payload.decode("ascii")
        except UnicodeDecodeError:
            self._fatal(connection, FATAL_INITIALIZATION, "subaddress must be ASCII")
            return None
        if major < 1 or (major, minor) < (1, 0) or subaddress != self.subaddress:
            self._fatal(connection, FATAL_INITIALIZATION, "unsupported version or subaddress")
            return None
        with self._lock:
            if self._session is not None and not self._session.closed:
                self._fatal(
                    connection, FATAL_MAX_CLIENTS, "instrument already has an active session"
                )
                return None
            identifier = next(self._session_ids) & 0xFFFF
            if identifier == 0:
                identifier = next(self._session_ids) & 0xFFFF
            session = _Session(identifier, connection, max_message_size=self.max_message_size)
            self._session = session
        self.instrument.visa_device_clear()
        self._srq_asserted = False
        self._srq_pending_since = None
        send_message(
            connection,
            HiSLIPMessage(
                INITIALIZE_RESPONSE,
                parameter=(PROTOCOL_VERSION << 16) | identifier,
            ),
        )
        connection.settimeout(None)
        return session

    def _async_initialize(
        self, connection: socket.socket, message: HiSLIPMessage
    ) -> _Session | None:
        with self._lock:
            session = self._session
            if (
                session is None
                or session.closed
                or session.identifier != (message.parameter & 0xFFFF)
                or session.async_socket is not None
            ):
                self._fatal(connection, FATAL_INITIALIZATION, "unknown HiSLIP session")
                return None
            session.async_socket = connection
            session.ready.set()
        send_message(
            connection,
            HiSLIPMessage(
                ASYNC_INITIALIZE_RESPONSE,
                parameter=int.from_bytes(b"SCPI", "big"),
            ),
        )
        connection.settimeout(None)
        return session

    def _serve_sync(self, session: _Session) -> None:
        while self.running and not session.closed:
            message = receive_message(session.sync, maximum=session.max_message_size)
            if message is None:
                break
            if not session.ready.is_set():
                self._fatal(
                    session.sync,
                    FATAL_CHANNELS_NOT_ESTABLISHED,
                    "both HiSLIP channels must be established",
                )
                break
            if session.clearing and message.message_type != DEVICE_CLEAR_COMPLETE:
                continue
            if message.message_type in (DATA, DATA_END):
                self._receive_data(session, message)
            elif message.message_type == TRIGGER:
                if message.control_code not in (0, 1):
                    self._error(session.sync, ERROR_UNRECOGNIZED_CONTROL, "invalid trigger control")
                else:
                    self.instrument.acquisition.bus_trigger()
            elif message.message_type == DEVICE_CLEAR_COMPLETE:
                with session.state_lock:
                    session.clearing = False
                    session.input_buffer.clear()
                send_message(
                    session.sync,
                    HiSLIPMessage(DEVICE_CLEAR_ACKNOWLEDGE, message.control_code & 1),
                )
            else:
                self._error(session.sync, ERROR_UNRECOGNIZED_MESSAGE, "unsupported sync message")

    def _receive_data(self, session: _Session, message: HiSLIPMessage) -> None:
        if message.control_code not in (0, 1):
            self._error(session.sync, ERROR_UNRECOGNIZED_CONTROL, "invalid data control")
            return
        with session.state_lock:
            if len(session.input_buffer) + len(message.payload) > session.max_message_size:
                session.input_buffer.clear()
                self._error(session.sync, ERROR_MESSAGE_TOO_LARGE, "program message too large")
                return
            session.input_buffer.extend(message.payload)
            if message.message_type != DATA_END:
                return
            command = bytes(session.input_buffer)
            session.input_buffer.clear()
        for termination in (b"\r\n", b"\n", b"\r"):
            if command.endswith(termination):
                command = command[: -len(termination)]
                break
        self.instrument.queue_command_response(command, termination=b"\n")
        response = self.instrument.read_output()
        if response:
            send_message(
                session.sync,
                HiSLIPMessage(DATA_END, parameter=message.parameter, payload=response),
            )

    def _serve_async(self, session: _Session) -> None:
        connection = session.async_socket
        while self.running and not session.closed and connection is not None:
            message = receive_message(connection, maximum=session.max_message_size)
            if message is None:
                break
            if message.message_type == ASYNC_MAX_MSG_SIZE:
                if len(message.payload) != 8:
                    self._error(connection, ERROR_UNRECOGNIZED_CONTROL, "invalid maximum size")
                    continue
                requested = struct.unpack("!Q", message.payload)[0]
                negotiated = min(max(requested, HEADER.size), self.max_message_size)
                session.max_message_size = negotiated
                self._async_send(
                    session,
                    HiSLIPMessage(
                        ASYNC_MAX_MSG_SIZE_RESPONSE,
                        payload=struct.pack("!Q", negotiated),
                    ),
                )
            elif message.message_type == ASYNC_DEVICE_CLEAR:
                with session.state_lock:
                    session.clearing = True
                    session.input_buffer.clear()
                self.instrument.visa_device_clear()
                self._async_send(session, HiSLIPMessage(ASYNC_DEVICE_CLEAR_ACKNOWLEDGE))
            elif message.message_type == ASYNC_STATUS_QUERY:
                with self._lock:
                    if self.instrument.status.requesting_service:
                        self._srq_asserted = True
                        self._srq_pending_since = None
                self._async_send(
                    session,
                    HiSLIPMessage(
                        ASYNC_STATUS_RESPONSE,
                        control_code=self.instrument.status.status_byte,
                    ),
                )
            elif message.message_type == ASYNC_LOCK:
                self._handle_lock(session, message)
            elif message.message_type == ASYNC_LOCK_INFO:
                self._async_send(
                    session,
                    HiSLIPMessage(
                        ASYNC_LOCK_INFO_RESPONSE,
                        control_code=1 if session.locked else 0,
                        parameter=1 if session.locked else 0,
                    ),
                )
            elif message.message_type == ASYNC_REMOTE_LOCAL_CONTROL:
                if message.control_code not in range(0, 7):
                    self._error(
                        connection, ERROR_UNRECOGNIZED_CONTROL, "invalid remote/local control"
                    )
                else:
                    self._async_send(session, HiSLIPMessage(ASYNC_REMOTE_LOCAL_RESPONSE))
            else:
                self._error(connection, ERROR_UNRECOGNIZED_MESSAGE, "unsupported async message")

    def _handle_lock(self, session: _Session, message: HiSLIPMessage) -> None:
        if message.control_code == 1:
            response = 3 if session.locked else 1
            if response == 1:
                session.locked = True
        elif message.control_code == 0:
            response = 1 if session.locked else 3
            if response == 1:
                session.locked = False
        else:
            self._error(session.async_socket, ERROR_UNRECOGNIZED_CONTROL, "invalid lock control")
            return
        self._async_send(session, HiSLIPMessage(ASYNC_LOCK_RESPONSE, response))

    def _monitor_srq(self) -> None:
        while self.running:
            requesting = self.instrument.status.requesting_service
            with self._lock:
                session = self._session
                now = time.monotonic()
                if requesting and not self._srq_asserted:
                    if self._srq_pending_since is None:
                        self._srq_pending_since = now
                    elif now - self._srq_pending_since >= 0.05:
                        self._srq_asserted = True
                        self._srq_pending_since = None
                        if session is not None and session.ready.is_set() and not session.closed:
                            self._async_send(
                                session,
                                HiSLIPMessage(
                                    ASYNC_SERVICE_REQUEST,
                                    control_code=self.instrument.status.status_byte,
                                ),
                            )
                elif not requesting:
                    self._srq_asserted = False
                    self._srq_pending_since = None
            time.sleep(0.01)

    def _async_send(self, session: _Session, message: HiSLIPMessage) -> None:
        connection = session.async_socket
        if connection is None:
            return
        with session.async_send_lock:
            self._safe_send(connection, message)

    def _close_session(self, session: _Session) -> None:
        with self._lock:
            if session.closed:
                return
            session.closed = True
            if self._session is session:
                self._session = None
        for connection in (session.sync, session.async_socket):
            if connection is None:
                continue
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        self.instrument.status.set_output_queue_count(0)

    @staticmethod
    def _safe_send(connection: socket.socket, message: HiSLIPMessage) -> None:
        try:
            send_message(connection, message)
        except OSError:
            pass

    def _fatal(self, connection: socket.socket, code: int, text: str) -> None:
        self._safe_send(connection, HiSLIPMessage(FATAL_ERROR, code, payload=text.encode("ascii")))

    def _error(self, connection: socket.socket | None, code: int, text: str) -> None:
        if connection is not None:
            self._safe_send(connection, HiSLIPMessage(ERROR, code, payload=text.encode("ascii")))
