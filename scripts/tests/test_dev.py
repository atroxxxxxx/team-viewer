import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def dev(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "dev.py"
    spec = importlib.util.spec_from_file_location("project_dev", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "STATE", tmp_path / ".dev")
    monkeypatch.setattr(module, "PYTHON", tmp_path / ".venv/bin/python")
    monkeypatch.setattr(module, "IS_WINDOWS", False)
    monkeypatch.setattr(module, "POSTGRES_ROOTS", (tmp_path / "postgresql", tmp_path / "usr"))
    monkeypatch.delenv("PG_BIN", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    return module


def binaries(path, suffix=""):
    path.mkdir(parents=True)
    for name in ("pg_ctl", "initdb", "postgres"):
        (path / (name + suffix)).touch()
    return path.resolve()


def test_linux_finds_debian_postgres_without_pg_config(dev):
    binaries(dev.ROOT / "postgresql/9.6/bin")
    expected = binaries(dev.ROOT / "postgresql/16/bin")
    incomplete = dev.ROOT / "postgresql/18/bin"
    incomplete.mkdir(parents=True)
    (incomplete / "pg_ctl").touch()
    assert dev.pg_bin() == expected


def test_linux_finds_rpm_postgres(dev):
    expected = binaries(dev.ROOT / "usr/pgsql-16/bin")
    assert dev.pg_bin() == expected


def test_explicit_pg_bin_takes_precedence(dev, monkeypatch):
    binaries(dev.STATE / "pgsql/bin")
    expected = binaries(dev.ROOT / "custom postgres/bin")
    monkeypatch.setenv("PG_BIN", str(expected))
    assert dev.pg_bin() == expected


def test_invalid_pg_bin_is_not_silently_replaced(dev, monkeypatch):
    binaries(dev.STATE / "pgsql/bin")
    monkeypatch.setenv("PG_BIN", str(dev.ROOT / "missing"))
    with pytest.raises(RuntimeError, match="PG_BIN must contain"):
        dev.pg_bin()


def test_linux_finds_postgres_in_path(dev, monkeypatch):
    expected = binaries(dev.ROOT / "local/bin")
    monkeypatch.setattr(dev.shutil, "which", lambda name: str(expected / name) if name == "pg_ctl" else None)
    assert dev.pg_bin() == expected


def test_linux_finds_pg_config_bindir(dev, monkeypatch):
    expected = binaries(dev.ROOT / "custom postgres/bin")
    monkeypatch.setattr(dev.shutil, "which", lambda name: "/usr/bin/pg_config" if name == "pg_config" else None)

    def pg_config(args, **kwargs):
        assert "creationflags" not in kwargs
        return SimpleNamespace(returncode=0, stdout=str(expected) + "\n")

    monkeypatch.setattr(dev.subprocess, "run", pg_config)
    assert dev.pg_bin() == expected


def test_linux_install_uses_system_binaries_without_downloading_zip(dev, monkeypatch):
    binaries(dev.ROOT / "postgresql/16/bin")

    def reject_download(*args):
        pytest.fail("Linux must not download the Windows ZIP")

    monkeypatch.setattr(dev.urllib.request, "urlretrieve", reject_download)
    dev.install_postgres()


def test_missing_linux_postgres_has_installation_hint(dev):
    with pytest.raises(RuntimeError, match="Install postgresql"):
        dev.install_postgres()


def test_windows_bundled_postgres_still_works(dev, monkeypatch):
    monkeypatch.setattr(dev, "IS_WINDOWS", True)
    expected = binaries(dev.STATE / "pgsql/bin", ".exe")
    assert dev.pg_bin() == expected


def test_setup_bootstraps_pip_inside_venv_and_preserves_secrets(dev, monkeypatch):
    calls = []

    def create_venv(path, **kwargs):
        dev.PYTHON.parent.mkdir(parents=True)
        dev.PYTHON.touch()

    monkeypatch.setattr(dev.venv, "create", create_venv)
    monkeypatch.setattr(dev.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(dev, "run", lambda args, **kwargs: calls.append(args))
    dev.setup(False)
    saved = (dev.ROOT / ".env.dev").read_bytes()
    assert calls[0] == [dev.PYTHON, "-m", "ensurepip", "--upgrade"]
    assert calls[1][0] == dev.PYTHON
    dev.setup(False)
    assert (dev.ROOT / ".env.dev").read_bytes() == saved
    assert not any(str(dev.sys.executable) == str(args[0]) for args in calls)


def test_setup_refuses_a_windows_venv_on_linux(dev):
    other = dev.ROOT / ".venv/Scripts/python.exe"
    other.parent.mkdir(parents=True)
    other.touch()
    with pytest.raises(RuntimeError, match="another OS"):
        dev.setup(False)


def test_linux_cluster_uses_tcp_without_system_socket_directory(dev):
    options = dev.postgres_options()
    assert "-h 127.0.0.1" in options
    assert "unix_socket_directories=" in options


def test_linux_database_rejects_root(dev, monkeypatch):
    monkeypatch.setattr(dev.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(RuntimeError, match="without sudo"):
        dev.require_regular_user()
