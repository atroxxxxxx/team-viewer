import os
import time

import uvicorn

from ticket_service.app import build_app
from ticket_service.auth import Checker
from ticket_service.db import engine_from_url, ensure_step_order, local_shard_count, session_factory
from ticket_service.log import error, info
from ticket_service.relay import Relay


def main() -> None:
    creds = Checker(
        _env("CLIENT_USER", ""),
        _env("CLIENT_PASSWORD", ""),
        _env("OPERATOR_USER", ""),
        _env("OPERATOR_PASSWORD", ""),
    )
    shard_count = int(_env("SHARD_COUNT", "4"))
    if shard_count <= 0:
        error("shard count", config=shard_count)
        raise SystemExit(1)
    factory = _wait_db(_env("DATABASE_URL", "postgres://support:support@127.0.0.1:5432/support?sslmode=disable"), shard_count)
    ensure_step_order(factory)
    app = build_app(
        factory,
        creds,
        Relay(_env("RELAY_INGEST_URL", "http://127.0.0.1:18082"), _env("RELAY_INGEST_TOKEN", "")),
        shard_count,
        _env("SWAGGER_DOC_URL", "/openapi/tickets.json"),
    )
    host, port = _addr(_env("HTTP_ADDR", "127.0.0.1:18080"))
    info("listen", addr=f"{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_config=None)


def _wait_db(url: str, shard_count: int):
    deadline = time.time() + 60
    last = "нет соединения"
    while True:
        try:
            factory = session_factory(engine_from_url(url))
            if local_shard_count(factory) == shard_count:
                return factory
            last = "shard count mismatch"
        except Exception as exc:
            last = str(exc)
        if time.time() > deadline:
            error("dependencies", err=last)
            raise SystemExit(1)
        error("dependencies retry", err=last)
        time.sleep(1)


def _addr(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port)


def _env(key: str, fallback: str) -> str:
    return os.environ.get(key) or fallback


if __name__ == "__main__":
    main()
