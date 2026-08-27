"""Binary-safe raw-socket server for one virtual SCPI instrument."""

import logging
import socket
import threading
import time

from .socket_transport import MessageTooLarge, SocketMessageFramer, SocketTransportConfig

logger = logging.getLogger(__name__)


class SCPIServer:
    """Serve one physical-style SCPI session over a raw TCP socket."""

    def __init__(
        self,
        instrument,
        manager,
        host="localhost",
        port=5025,
        *,
        transport_config=None,
    ):
        self.instrument = instrument
        self.manager = manager
        self.host = host
        self.port = port
        self.transport_config = transport_config or SocketTransportConfig()
        self.socket = None
        self.running = False
        self.clients = []
        self.thread = None
        self._client_thread = None
        self._clients_lock = threading.RLock()
        self._session_lock = threading.Lock()

    def start(self):
        """Start the TCP listener."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(self.transport_config.backlog)
            self.socket.settimeout(self.transport_config.accept_poll_interval)
            self.port = self.socket.getsockname()[1]
            self.running = True
            self.thread = threading.Thread(target=self._server_loop, daemon=True)
            self.thread.start()
            logger.info(
                "Started SCPI server for %r on %s:%s",
                self.instrument.name,
                self.host,
                self.port,
            )
            self._notify_dashboard_state("server-started")
            return True
        except Exception as error:
            logger.error("Failed to start server for %s: %s", self.instrument.name, error)
            if self.socket:
                self.socket.close()
                self.socket = None
            return False

    def stop(self):
        """Stop the listener and its active client session."""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

        with self._clients_lock:
            clients = tuple(self.clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass

        current = threading.current_thread()
        if self._client_thread and self._client_thread is not current:
            self._client_thread.join(timeout=1)
        if self.thread and self.thread is not current:
            self.thread.join(timeout=1)
        logger.info("Stopped SCPI server for %r", self.instrument.name)
        self._notify_dashboard_state("server-stopped")

    def _server_loop(self):
        while self.running:
            try:
                client_socket, address = self.socket.accept()
                if not self._session_lock.acquire(blocking=False):
                    logger.warning(
                        "Rejected additional client for %s from %s",
                        self.instrument.name,
                        address,
                    )
                    client_socket.close()
                    continue
                logger.info("Client connected to %s from %s", self.instrument.name, address)
                self._client_thread = threading.Thread(
                    target=self._run_client,
                    args=(client_socket, address),
                    daemon=True,
                )
                self._client_thread.start()
            except socket.timeout:
                continue
            except (OSError, AttributeError):
                if self.running:
                    logger.error("Server socket error for %s", self.instrument.name)
                break

    def _run_client(self, client_socket, address):
        try:
            self._handle_client(client_socket, address)
        finally:
            with self._clients_lock:
                if client_socket in self.clients:
                    self.clients.remove(client_socket)
            try:
                client_socket.close()
            except OSError:
                pass
            self._session_lock.release()
            self._notify_dashboard_state("client-disconnected")

    def _handle_client(self, client_socket, address):
        with self._clients_lock:
            self.clients.append(client_socket)
        self._notify_dashboard_state("client-connected")
        try:
            self.instrument.visa_device_clear()
            config = self.transport_config
            framer = SocketMessageFramer(config)
            poll_timeout = min(0.1, config.accept_poll_interval)
            client_socket.settimeout(poll_timeout)
            last_activity = time.monotonic()
            while self.running:
                try:
                    data = client_socket.recv(config.receive_chunk_size)
                    if not data:
                        break
                    last_activity = time.monotonic()
                    for message in framer.feed(data):
                        if message.strip():
                            self._execute_message(client_socket, message)
                except socket.timeout:
                    now = time.monotonic()
                    if (
                        framer.buffered_bytes
                        and config.idle_frame_timeout is not None
                        and now - last_activity >= config.idle_frame_timeout
                    ):
                        message = framer.flush_unterminated()
                        if message and message.strip():
                            self._execute_message(client_socket, message)
                        last_activity = now
                    if (
                        config.client_idle_timeout is not None
                        and now - last_activity >= config.client_idle_timeout
                    ):
                        break
                except (ConnectionResetError, BrokenPipeError):
                    break
                except MessageTooLarge as error:
                    self.instrument.error_queue.push(-363, str(error))
                    logger.warning("Closed oversized SCPI message from %s: %s", address, error)
                    break
                except Exception as error:
                    logger.error("Client handling error: %s", error)
                    break
        except Exception as error:
            logger.error("Client %s error: %s", address, error)

    def _execute_message(self, client_socket, message):
        self.instrument.queue_command_response(
            message,
            termination=self.transport_config.write_termination,
        )
        if self.instrument.output_queue:
            client_socket.settimeout(self.transport_config.send_timeout)
            client_socket.sendall(self.instrument.read_output())
            client_socket.settimeout(min(0.1, self.transport_config.accept_poll_interval))

    def _notify_dashboard_state(self, reason):
        dashboard = getattr(self.manager, "web_dashboard", None)
        if dashboard is not None:
            dashboard.emit_state_changed(reason)

    def execute_control_command(self, command):
        """Serialize dashboard execution with the physical TCP session."""
        return self.execute_control_action(
            lambda instrument: instrument.process_command(command)
        )

    def execute_control_action(self, action):
        """Run one control mutation under the instrument session lock."""
        if not self._session_lock.acquire(blocking=False):
            raise RuntimeError("instrument is busy with an active client session")
        try:
            return action(self.instrument)
        finally:
            self._session_lock.release()
