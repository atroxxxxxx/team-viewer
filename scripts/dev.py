"""Project-local ticket-service setup, PostgreSQL, test and server commands."""
import argparse
import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".dev"
IS_WINDOWS = os.name == "nt"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
POSTGRES_URL = "https://get.enterprisedb.com/postgresql/postgresql-16.15-1-windows-x64-binaries.zip"
POSTGRES_ROOTS = (Path("/usr/lib/postgresql"), Path("/usr"))


def run(args, **kwargs):
    if IS_WINDOWS:
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
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required")
    (STATE / "pytest-cache").mkdir(parents=True, exist_ok=True)
    if not PYTHON.is_file():
        other_python = ROOT / ".venv" / ("bin/python" if IS_WINDOWS else "Scripts/python.exe")
        if other_python.is_file():
            raise RuntimeError("This .venv belongs to another OS; use a separate checkout and create a new environment")
        venv.create(ROOT / ".venv", with_pip=False)
    ensure_pip()
    run([PYTHON, "-m", "pip", "install", "-r", "requirements-dev.txt"])
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
        content = "".join(f"{key}={value}\n" for key, value in values.items())
        # O_EXCL preserves existing secrets; mode 0600 protects new Linux files.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
    if with_postgres:
        install_postgres()
    print("Environment ready: .venv and .env.dev (existing secrets preserved).")


def ensure_pip():
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
    result = subprocess.run([str(PYTHON), "-m", "pip", "--version"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    if result.returncode == 0:
        return
    try:
        run([PYTHON, "-m", "ensurepip", "--upgrade"])
    except subprocess.CalledProcessError:
        if importlib.util.find_spec("pip") is None:
            raise RuntimeError("Install Python's venv support (Debian/Ubuntu: sudo apt install python3-venv) and rerun setup") from None
        # Some embedded Windows runtimes lack ensurepip. Install into the venv,
        # never into the system interpreter (including externally managed Linux).
        run([sys.executable, "-m", "pip", "--python", PYTHON, "install", "pip"])


def valid_pg_bin(directory):
    suffix = ".exe" if IS_WINDOWS else ""
    return all((directory / (name + suffix)).is_file() for name in ("pg_ctl", "initdb", "postgres"))


def pg_bin():
    configured = os.environ.get("PG_BIN")
    if configured:
        directory = Path(configured).expanduser()
        if not valid_pg_bin(directory):
            raise RuntimeError("PG_BIN must contain pg_ctl, initdb and postgres")
        return directory.resolve()
    candidates = [STATE / "pgsql/bin"]
    pg_ctl = shutil.which("pg_ctl")
    if pg_ctl:
        candidates.append(Path(pg_ctl).parent)
    pg_config = shutil.which("pg_config")
    if pg_config:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
        result = subprocess.run([pg_config, "--bindir"], capture_output=True, text=True, **options)
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(Path(result.stdout.strip()))
    if not IS_WINDOWS:
        for root in POSTGRES_ROOTS:
            directories = list(root.glob("*/bin")) if root.name == "postgresql" else list(root.glob("pgsql-*/bin"))
            candidates.extend(sorted(directories, key=pg_version_key, reverse=True))
    for directory in candidates:
        if valid_pg_bin(directory):
            return directory.resolve()
    raise RuntimeError("PostgreSQL server binaries not found. Install postgresql (Debian/Ubuntu) or postgresql-server (Fedora), or set PG_BIN")


def pg_version_key(directory):
    version = directory.parent.name.removeprefix("pgsql-")
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def install_postgres():
    if not IS_WINDOWS or os.environ.get("PG_BIN"):
        print(f"Using PostgreSQL binaries: {pg_bin()}")
        return
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


def require_regular_user():
    if not IS_WINDOWS and getattr(os, "geteuid", lambda: -1)() == 0:
        raise RuntimeError("Run db-start/db-stop as a regular user, without sudo")


def postgres_options():
    options = "-h 127.0.0.1 -p 15432"
    if not IS_WINDOWS:
        # Distro builds may default to /var/run/postgresql, which the developer
        # cannot write. This private cluster is accessed exclusively over TCP.
        options += " -c unix_socket_directories="
    return options


def db_start():
    from sqlalchemy.engine import make_url
    import psycopg

    load_env()
    require_regular_user()
    url = make_url(os.environ["DATABASE_URL"])
    # This command manages only the project's private loopback cluster.
    if url.host != "127.0.0.1" or url.port != 15432:
        raise RuntimeError("db-start requires the local 127.0.0.1:15432 DATABASE_URL")
    binaries = pg_bin()
    data = STATE / "pgdata"
    STATE.mkdir(exist_ok=True)
    if not (data / "PG_VERSION").is_file():
        secret = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="pg-password-",
                                             dir=STATE, delete=False) as stream:
                secret = Path(stream.name)
                stream.write(url.password)
            run([binaries / "initdb", "-D", data, "-U", url.username, "--encoding=UTF8",
                 "--locale=C", "--auth=scram-sha-256", f"--pwfile={secret}"],
                stdout=subprocess.DEVNULL)
        finally:
            if secret is not None:
                secret.unlink(missing_ok=True)
    connection_args = {"host": url.host, "port": url.port, "user": url.username, "password": url.password}
    try:
        with psycopg.connect(**connection_args, dbname="postgres", connect_timeout=2):
            running = True
    except psycopg.OperationalError:
        running = False
    if not running:
        # A background server can inherit a pipe on Windows and keep the CLI
        # waiting for EOF after pg_ctl exits. Use a regular file for startup.
        try:
            with (STATE / "pg_ctl.log").open("a", encoding="utf-8") as output:
                run([binaries / "pg_ctl", "-D", data, "-l", STATE / "postgres.log",
                     "-o", postgres_options(), "-w", "start"], stdout=output)
        except subprocess.CalledProcessError:
            raise RuntimeError("PostgreSQL did not start; see .dev/postgres.log and .dev/pg_ctl.log") from None
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
        require_regular_user()
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
