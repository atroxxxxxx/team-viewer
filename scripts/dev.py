"""Project-local ticket-service setup, PostgreSQL, test and server commands."""
import argparse
import os
import secrets
import subprocess
import sys
import urllib.request
import uuid
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".dev"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
POSTGRES_URL = "https://get.enterprisedb.com/postgresql/postgresql-16.15-1-windows-x64-binaries.zip"


def run(args, **kwargs):
    if os.name == "nt":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)
    try:
        result = subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True,
                                text=True, errors="replace", **kwargs)
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        raise
    if result.stdout:
        print(result.stdout, end="")
    return result


def load_env():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env.dev")


def setup(with_postgres):
    (STATE / "pytest-cache").mkdir(parents=True, exist_ok=True)
    if not PYTHON.is_file():
        venv.create(ROOT / ".venv", with_pip=False)
    run([sys.executable, "-m", "pip", "--python", PYTHON, "install", "pip", "-r", "requirements-dev.txt"])
    path = ROOT / ".env.dev"
    if not path.exists():
        password = secrets.token_hex(24)
        prefix = f"postgresql+psycopg://support_dev:{password}@127.0.0.1:15432/"
        values = {
            "DATABASE_URL": prefix + "support_dev",
            "TEST_DATABASE_URL": prefix + "support_test",
            "CLIENT_USER": "client", "CLIENT_PASSWORD": secrets.token_hex(24),
            "OPERATOR_USER": "operator", "OPERATOR_PASSWORD": secrets.token_hex(24),
            "RELAY_INGEST_URL": "http://127.0.0.1:18082",
            "RELAY_INGEST_TOKEN": secrets.token_hex(24),
            "HTTP_ADDR": "127.0.0.1:18080", "SHARD_COUNT": "4",
        }
        path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    if with_postgres:
        install_postgres()
    print("Environment ready: .venv and .env.dev (existing secrets preserved).")


def pg_bin():
    directory = Path(os.environ.get("PG_BIN", str(STATE / "pgsql/bin")))
    if not (directory / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")).is_file():
        raise RuntimeError("Set PG_BIN to PostgreSQL's bin directory or run setup --postgres on Windows")
    return directory


def install_postgres():
    if os.name != "nt":
        raise RuntimeError("Automatic portable PostgreSQL download is Windows-only; set PG_BIN instead")
    if (STATE / "pgsql/bin/pg_ctl.exe").is_file():
        return
    STATE.mkdir(exist_ok=True)
    archive = STATE / "postgresql.zip"
    print("Downloading PostgreSQL 16 from EDB into .dev ...", flush=True)
    urllib.request.urlretrieve(POSTGRES_URL, archive)
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            if item.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                target = (STATE / item.filename).resolve()
                if not target.is_relative_to(STATE.resolve()):
                    raise RuntimeError("Unsafe PostgreSQL archive path")
                zipped.extract(item, STATE)


def db_start():
    from sqlalchemy.engine import make_url
    import psycopg

    load_env()
    url = make_url(os.environ["DATABASE_URL"])
    # This command manages only the project's private loopback cluster.
    if url.host != "127.0.0.1" or url.port != 15432:
        raise RuntimeError("db-start requires the local 127.0.0.1:15432 DATABASE_URL")
    binaries = pg_bin()
    data = STATE / "pgdata"
    STATE.mkdir(exist_ok=True)
    if not (data / "PG_VERSION").is_file():
        secret = STATE / "pg-password.tmp"
        try:
            secret.write_text(url.password, encoding="utf-8")
            run([binaries / "initdb", "-D", data, "-U", url.username, "--encoding=UTF8",
                 "--locale=C", "--auth=scram-sha-256", f"--pwfile={secret}"],
                stdout=subprocess.DEVNULL)
        finally:
            secret.unlink(missing_ok=True)
    connection_args = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    try:
        with psycopg.connect(**connection_args, dbname="postgres", connect_timeout=2):
            running = True
    except psycopg.OperationalError:
        running = False
    if not running:
        run([binaries / "pg_ctl", "-D", data, "-l", STATE / "postgres.log",
             "-o", "-h 127.0.0.1 -p 15432", "-w", "start"])
    test = make_url(os.environ["TEST_DATABASE_URL"])
    if (test.host, test.port, test.username, test.password) != (url.host, url.port, url.username, url.password):
        raise RuntimeError("Local development and test URLs must use the same local cluster")
    with psycopg.connect(**connection_args, dbname="postgres", autocommit=True) as connection:
        for name in (url.database, test.database):
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                connection.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
    with psycopg.connect(**connection_args, dbname=url.database) as connection:
        if connection.execute("SELECT to_regclass('public.shard_directory')").fetchone()[0] is None:
            connection.execute((ROOT / "postgres-redis/init/001_schema.sql").read_text(encoding="utf-8"), prepare=False)
    print("PostgreSQL ready on 127.0.0.1:15432; development schema initialized.")


def serve(fake_relay):
    load_env()
    sys.path.insert(0, str(ROOT / "ticket-service"))
    if not fake_relay:
        from ticket_service.__main__ import main
        main()
        return
    import uvicorn
    from ticket_service.__main__ import _addr, _wait_db
    from ticket_service.app import build_app
    from ticket_service.auth import Checker

    class DevRelay:
        def open(self, client_id, ticket_id, camera):
            return uuid.uuid4()

        def close(self, ticket_id):
            pass

    print("Development relay stub enabled: HTTP workflows only, no screen/camera transport.", flush=True)
    count = int(os.environ.get("SHARD_COUNT", "4"))
    factory = _wait_db(os.environ["DATABASE_URL"], count)
    credentials = Checker(*(os.environ[name] for name in (
        "CLIENT_USER", "CLIENT_PASSWORD", "OPERATOR_USER", "OPERATOR_PASSWORD")))
    app = build_app(factory, credentials, DevRelay(), count, "/openapi/tickets.json")
    host, port = _addr(os.environ.get("HTTP_ADDR", "127.0.0.1:18080"))
    uvicorn.run(app, host=host, port=port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup").add_argument("--postgres", action="store_true")
    commands.add_parser("db-start")
    commands.add_parser("db-stop")
    commands.add_parser("test", add_help=False)
    commands.add_parser("serve").add_argument("--fake-relay", action="store_true")
    commands.add_parser("check")
    args, extra = parser.parse_known_args()
    if extra and args.command != "test":
        parser.error("unrecognized arguments: " + " ".join(extra))
    if args.command == "setup":
        setup(args.postgres)
    elif args.command == "db-start":
        db_start()
    elif args.command == "db-stop":
        load_env()
        run([pg_bin() / "pg_ctl", "-D", STATE / "pgdata", "-m", "fast", "-w", "stop"])
    elif args.command == "test":
        load_env()
        run([PYTHON, "-m", "pytest", *extra])
    elif args.command == "serve":
        serve(args.fake_relay)
    elif args.command == "check":
        run([PYTHON, "-m", "pip", "check"])
        run([PYTHON, "-m", "compileall", "-q", "ticket-service", "support-protocol", "operator-app", "scripts"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
