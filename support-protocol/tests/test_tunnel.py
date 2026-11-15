import socket

from support_protocol.tunnel import LocalRTSPBridge


def test_close_disconnects_active_peer(monkeypatch):
    monkeypatch.delenv("SUPPORT_RELAY_TLS", raising=False)
    with socket.create_server(("127.0.0.1", 0)) as server:
        server.settimeout(2)
        bridge = LocalRTSPBridge("127.0.0.1", server.getsockname()[1], "OPERATOR", "test", "a" * 64)
        try:
            with socket.create_connection(("127.0.0.1", bridge.port), timeout=2) as local:
                remote, _ = server.accept()
                with remote:
                    remote.settimeout(2)
                    local.sendall(b"test payload")
                    assert remote.makefile("rb", buffering=0).readline() == b"OPERATOR test " + b"a" * 64 + b"\n"
                    bridge.close()
                    assert remote.recv(64) in {b"", b"test payload"}
                    assert remote.recv(64) == b""
                    assert local.recv(1) == b""
        finally:
            bridge.close()
