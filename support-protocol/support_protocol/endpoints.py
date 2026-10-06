from pathlib import Path

import yaml


def load_endpoints(path: Path) -> tuple[str, str, str, str, bool]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    ticket = data["ticket"]
    relay = data["relay"]
    auth = data["auth"]
    scheme = str(ticket.get("scheme") or "http")
    api = f"{scheme}://{ticket['host']}:{int(ticket['port'])}"
    rtsp = f"rtsp://{relay['host']}:{int(relay['port'])}"
    verify = ticket.get("tls_verify", True)
    if isinstance(verify, str):
        verify = verify.lower() not in {"0", "false", "no"}
    return api, rtsp, str(auth["username"]), str(auth["password"]), bool(verify)
