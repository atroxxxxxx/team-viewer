import json
import sys
from datetime import datetime, timezone


def info(msg: str, **fields) -> None:
    _write("info", msg, fields)


def error(msg: str, **fields) -> None:
    _write("error", msg, fields)


def _write(level: str, msg: str, fields: dict) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "service": "ticket-service",
        "msg": msg,
    }
    row.update(fields)
    sys.stdout.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
