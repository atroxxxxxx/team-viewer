import logging
import os
from pathlib import Path

import yaml
from support_protocol.settings import valid_base, valid_host


def config_file(default: Path, env_name: str) -> Path:
    override = os.environ.get(env_name, "").strip()
    if override:
        return Path(override)
    return default


def load_endpoints(path: Path) -> tuple[str, str, int, str, str, bool]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        ticket, relay, auth = data["ticket"], data["relay"], data["auth"]
        scheme = str(ticket.get("scheme") or "http")
        api = f"{scheme}://{ticket['host']}:{int(ticket['port'])}"
        port = int(relay["port"])
        if not valid_base(api) or not valid_host(relay["host"]) or not 1 <= port <= 65535:
            raise ValueError("invalid endpoints")
        if not isinstance(auth["username"], str) or not isinstance(auth["password"], str):
            raise ValueError("invalid credentials")
        verify = ticket.get("tls_verify", True)
        if isinstance(verify, str):
            verify = verify.lower() not in {"0", "false", "no"}
        return api, relay["host"], port, auth["username"], auth["password"], bool(verify)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as exc:
        # YAML traceback может содержать строку с паролем: пишем тип и позицию без исходного текста.
        mark = getattr(exc, "problem_mark", None)
        logging.getLogger(__name__).warning(
            "Invalid base configuration (%s), line %s; authentication must be configured",
            type(exc).__name__, mark.line + 1 if mark else "unknown")
        # Нет пароля по умолчанию. Сервер продолжает требовать аутентификацию.
        return "http://127.0.0.1:8080", "127.0.0.1", 8554, "", "", True
