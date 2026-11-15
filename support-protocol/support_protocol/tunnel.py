import asyncio
import ipaddress
import logging
import os
import socket
import ssl
import threading

log = logging.getLogger(__name__)


class LocalRTSPBridge:
    """Передаёт локальный RTSP в сессионный порт ретранслятора."""

    def __init__(self, relay_host: str, relay_port: int, role: str, client_id: str, secret: str):
        self.relay_host = relay_host
        self.relay_port = int(relay_port)
        if len(secret) != 64:
            raise ValueError("нет секрета сессии")
        self.hello = f"{role} {client_id} {secret}\n".encode()
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
        self._tls_tasks: set[tuple[asyncio.AbstractEventLoop, asyncio.Task]] = set()
        threading.Thread(target=self._accept, daemon=True).start()

    def url(self, path: str) -> str:
        return f"rtsp://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sockets = tuple(self._connections)
            for loop, task in self._tls_tasks:
                loop.call_soon_threadsafe(task.cancel)
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
            if self._tls is not None:
                asyncio.run(self._pipe_tls(local))
                return
            remote = socket.create_connection((self.relay_host, self.relay_port), timeout=3)
            if not self._track(remote):
                return
            remote.sendall(self.hello)
            local.settimeout(None)
            remote.settimeout(None)
            outgoing = threading.Thread(target=_copy, args=(local, remote), daemon=True)
            outgoing.start()
            _copy(remote, local)
            outgoing.join()
        except asyncio.CancelledError:
            log.debug("TLS relay pipe stopped")
        except OSError as exc:
            if not self._closed:
                log.warning("Relay connection failed (%s)", type(exc).__name__)
        finally:
            for conn in (local, remote):
                if conn is not None:
                    _close(conn)
                    with self._lock:
                        self._connections.discard(conn)


    async def _pipe_tls(self, local: socket.socket) -> None:
        # Обе стороны SSL обслуживает один event loop, чтобы чтение и запись
        # не меняли состояние одного SSL-объекта из разных потоков.
        binding = (asyncio.get_running_loop(), asyncio.current_task())
        with self._lock:
            if self._closed:
                return
            self._connections.discard(local)
            self._tls_tasks.add(binding)
        writers = []
        tasks = []
        try:
            local_reader, local_writer = await asyncio.open_connection(sock=local)
            writers.append(local_writer)
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(self.relay_host, self.relay_port, ssl=self._tls,
                                        server_hostname=self.relay_host),
                timeout=3,
            )
            writers.append(remote_writer)
            remote_writer.write(self.hello)
            await remote_writer.drain()
            tasks = [
                asyncio.create_task(_copy_stream(local_reader, remote_writer)),
                asyncio.create_task(_copy_stream(remote_reader, local_writer)),
            ]
            finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                task.result()
        finally:
            for writer in writers:
                writer.transport.abort()
            for task in tasks:
                task.cancel()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                with self._lock:
                    self._tls_tasks.discard(binding)


async def _copy_stream(reader, writer) -> None:
    while chunk := await reader.read(65536):
        writer.write(chunk)
        await writer.drain()


def _close(conn: socket.socket) -> None:
    if conn.fileno() < 0:
        return
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        log.debug("Socket already disconnected")
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
