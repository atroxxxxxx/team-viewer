"""Версионированные пользовательские настройки без учётных данных."""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)


def config_dir() -> Path:
    if override := os.environ.get("SUPPORT_SETTINGS_DIR"):
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "RemoteSupport"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/RemoteSupport"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "remote-support"


def valid_base(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname) and
                (parsed.port is None or 1 <= parsed.port <= 65535) and
                not parsed.username and not parsed.password and
                parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def valid_host(value):
    return isinstance(value, str) and bool(value.strip()) and not any(c in value for c in "/@?# \n\r\t")


VALIDATORS = {
    "api_base": valid_base,
    "relay_host": valid_host,
    "relay_port": lambda v: type(v) is int and 1 <= v <= 65535,
    "tls_verify": lambda v: type(v) is bool,
    "camera": lambda v: type(v) is bool,
    "dangerous": lambda v: type(v) is bool,
    "allow_model_input": lambda v: type(v) is bool,
    "geometry": lambda v: isinstance(v, str) and len(v) <= 4096,
    "splitter": lambda v: isinstance(v, str) and len(v) <= 4096,
    "tab": lambda v: type(v) is int and 0 <= v <= 3,
    "video_fps": lambda v: type(v) is int and 5 <= v <= 30,
    "video_crf": lambda v: type(v) is int and 18 <= v <= 35,
}


class UserSettings:
    def __init__(self, role: str):
        if role not in {"client", "operator"}:
            raise ValueError("unknown desktop role")
        self.path = config_dir() / f"{role}.json"
        self.values = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            # Старый плоский формат также принимается. Неизвестные версии не перезаписываются при чтении.
            if not isinstance(data, dict) or data.get("version", 1) != 1:
                raise ValueError("unsupported settings format")
            values = data.get("values", data)
            if not isinstance(values, dict):
                raise ValueError("invalid settings values")
            self.values = {k: v for k, v in values.items() if k in VALIDATORS and VALIDATORS[k](v)}
        except FileNotFoundError:
            log.debug("User settings not created yet")
        except (OSError, ValueError, TypeError):
            log.warning("Cannot read user settings; keeping base configuration", exc_info=True)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def update(self, **values):
        if any(k not in VALIDATORS or not VALIDATORS[k](v) for k, v in values.items()):
            raise ValueError("invalid or secret settings field")
        updated = dict(self.values, **values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "values": updated}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.values = updated

    def endpoints(self, base):
        api, host, port, username, password, verify = base
        return (self.get("api_base", api), self.get("relay_host", host), self.get("relay_port", port),
                username, password, self.get("tls_verify", verify))
