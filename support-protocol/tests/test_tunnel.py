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


def test_tls_bridge_carries_traffic_in_both_directions(monkeypatch, tmp_path):
    import shutil
    import ssl
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    import pytest

    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is required to generate the local test certificate")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True, timeout=10,
    )
    monkeypatch.setenv("SUPPORT_RELAY_TLS", "1")
    monkeypatch.setenv("SUPPORT_RELAY_CA", str(cert))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    with socket.create_server(("127.0.0.1", 0)) as server, ThreadPoolExecutor(max_workers=1) as pool:
        server.settimeout(3)
        bridge = LocalRTSPBridge("127.0.0.1", server.getsockname()[1], "OPERATOR", "test", "a" * 64)

        def serve():
            conn, _ = server.accept()
            with conn:
                conn.settimeout(3)
                with context.wrap_socket(conn, server_side=True) as peer, peer.makefile("rb") as incoming:
                    assert incoming.readline() == b"OPERATOR test " + b"a" * 64 + b"\n"
                    for _ in range(30):
                        assert incoming.readline() == b"request\n"
                        peer.sendall(b"response\n")
                    assert incoming.read(1) == b""

        backend = pool.submit(serve)
        try:
            with socket.create_connection(("127.0.0.1", bridge.port), timeout=3) as local, local.makefile("rb") as incoming:
                for _ in range(30):
                    local.sendall(b"request\n")
                    assert incoming.readline() == b"response\n"
                bridge.close()
                assert incoming.read(1) == b""
            backend.result(3)
        finally:
            bridge.close()
