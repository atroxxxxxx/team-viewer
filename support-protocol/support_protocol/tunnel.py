import socket
import threading


class LocalRTSPBridge:
    """Принимает RTSP от ffmpeg на localhost и отдаёт его в сессионный порт ретранслятора."""

    def __init__(self, relay_host: str, relay_port: int, role: str, client_id: str, secret: str):
        self.relay_host = relay_host
        self.relay_port = int(relay_port)
        if len(secret) != 64:
            raise ValueError("нет секрета сессии")
        self.hello = f"{role} {client_id} {secret}\n".encode()
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def url(self, path: str) -> str:
        return f"rtsp://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self._closed = True
        try:
            self.server.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._closed:
            try:
                local, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._pipe, args=(local,), daemon=True).start()

    def _pipe(self, local: socket.socket) -> None:
        remote = None
        try:
            remote = socket.create_connection((self.relay_host, self.relay_port), timeout=10)
            remote.sendall(self.hello)
            local.settimeout(None)
            remote.settimeout(None)
            threading.Thread(target=_copy, args=(local, remote), daemon=True).start()
            _copy(remote, local)
        except OSError:
            pass
        finally:
            try:
                local.close()
            except OSError:
                pass
            if remote is not None:
                try:
                    remote.close()
                except OSError:
                    pass


def _copy(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
