import ipaddress
import logging
import os
import socket
import ssl
import threading

log = logging.getLogger(__name__)


class LocalRTSPBridge:
    """Передаёт локальный RTSP в сессионный порт ретранслятора."""

    def __init__(self, relay_host: str, relay_port: int, role: str, client_id: str):
        self.relay_host = relay_host
        self.relay_port = int(relay_port)
        self.hello = f"{role} {client_id}\n".encode()
        try:
            loopback = ipaddress.ip_address(relay_host).is_loopback
        except ValueError:
            loopback = False
        self._tls = None
        if not loopback or os.environ.get("SUPPORT_RELAY_TLS") == "1":
            self._tls = ssl.create_default_context(cafile=os.environ.get("SUPPORT_RELAY_CA") or None)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.server.settimeout(0.25)
        self.port = self.server.getsockname()[1]
        self._lock = threading.Lock()
        self._closed = False
        self._connections: set[socket.socket] = set()
        threading.Thread(target=self._accept, daemon=True).start()

    def url(self, path: str) -> str:
        return f"rtsp://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sockets = tuple(self._connections)
        self.server.close()
        for conn in sockets:
            _close(conn)

    def _track(self, conn: socket.socket) -> bool:
        with self._lock:
            if not self._closed:
                self._connections.add(conn)
                return True
        _close(conn)
        return False

    def _accept(self) -> None:
        while not self._closed:
            try:
                local, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if self._track(local):
                threading.Thread(target=self._pipe, args=(local,), daemon=True).start()

    def _pipe(self, local: socket.socket) -> None:
        remote = None
        try:
            remote = socket.create_connection((self.relay_host, self.relay_port), timeout=3)
            if self._tls is not None:
                remote = self._tls.wrap_socket(remote, server_hostname=self.relay_host, do_handshake_on_connect=False)
            if not self._track(remote):
                return
            if isinstance(remote, ssl.SSLSocket):
                remote.do_handshake()
            remote.sendall(self.hello)
            local.settimeout(None)
            remote.settimeout(None)
            outgoing = threading.Thread(target=_copy, args=(local, remote), daemon=True)
            outgoing.start()
            _copy(remote, local)
            outgoing.join()
        except OSError as exc:
            if not self._closed:
                log.warning("Relay connection failed (%s)", type(exc).__name__)
        finally:
            for conn in (local, remote):
                if conn is not None:
                    _close(conn)
                    with self._lock:
                        self._connections.discard(conn)


def _close(conn: socket.socket) -> None:
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        log.debug("Socket already disconnected", exc_info=True)
    conn.close()


def _copy(src: socket.socket, dst: socket.socket) -> None:
    try:
        while chunk := src.recv(65536):
            dst.sendall(chunk)
    except OSError:
        log.debug("RTSP pipe disconnected", exc_info=True)
    finally:
        _close(src)
        _close(dst)
