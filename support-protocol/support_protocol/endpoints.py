import os
from pathlib import Path

import yaml


def config_file(default: Path, env_name: str) -> Path:
    override = os.environ.get(env_name, "").strip()
    if override:
        return Path(override)
    return default


def load_endpoints(path: Path) -> tuple[str, str, int, str, str, bool]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    ticket = data["ticket"]
    relay = data["relay"]
    auth = data["auth"]
    scheme = str(ticket.get("scheme") or "http")
    api = f"{scheme}://{ticket['host']}:{int(ticket['port'])}"
    verify = ticket.get("tls_verify", True)
    if isinstance(verify, str):
        verify = verify.lower() not in {"0", "false", "no"}
    return api, str(relay["host"]), int(relay["port"]), str(auth["username"]), str(auth["password"]), bool(verify)
