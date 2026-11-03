import json
import os
import stat

import pytest
from support_protocol.settings import UserSettings
from support_protocol.endpoints import load_endpoints


@pytest.fixture(autouse=True)
def settings_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPPORT_SETTINGS_DIR", str(tmp_path))


def test_restart_and_separate_roles():
    client = UserSettings("client")
    client.update(api_base="https://support.example:443", relay_host="relay.example", relay_port=8554,
                  tls_verify=True, camera=True, dangerous=True, video_fps=20, video_crf=25)
    restored = UserSettings("client")
    assert restored.values == client.values
    assert UserSettings("operator").values == {}
    assert restored.endpoints(("old", "host", 1, "user", "secret", False)) == (
        "https://support.example:443", "relay.example", 8554, "user", "secret", True)
    if os.name != "nt":
        assert stat.S_IMODE(client.path.stat().st_mode) == 0o600


@pytest.mark.parametrize("content", ["{broken", "[]", '{"version":99,"values":{}}', '{"values":null}'])
def test_invalid_file_is_not_overwritten_on_read(tmp_path, content):
    path = tmp_path / "operator.json"
    path.write_text(content)
    assert UserSettings("operator").values == {}
    assert path.read_text() == content


def test_old_and_partial_settings(tmp_path):
    (tmp_path / "client.json").write_text(json.dumps({"camera": True, "relay_port": -1, "password": "secret"}))
    assert UserSettings("client").values == {"camera": True}


def test_secrets_and_credentials_in_urls_rejected():
    store = UserSettings("client")
    for values in ({"password": "secret"}, {"ticket_token": "secret"}, {"api_base": "http://u:secret@localhost"}):
        with pytest.raises(ValueError):
            store.update(**values)
    assert not store.path.exists()


def test_failed_write_keeps_existing_values(monkeypatch):
    store = UserSettings("client")
    store.update(camera=True)
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.update(camera=False)
    assert store.get("camera") is True
    assert UserSettings("client").get("camera") is True


def test_missing_and_corrupt_base_config(tmp_path):
    path = tmp_path / "base.yaml"
    assert load_endpoints(path)[3:5] == ("", "")
    path.write_text("ticket: [invalid")
    assert load_endpoints(path)[-1] is True


def test_restore_in_separate_process():
    import subprocess
    import sys
    store = UserSettings("operator")
    store.update(api_base="https://saved.example", tls_verify=True, allow_model_input=True)
    output = subprocess.check_output([sys.executable, "-c",
        "from support_protocol.settings import UserSettings; import json; print(json.dumps(UserSettings('operator').values))"], text=True, env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    assert json.loads(output) == store.values
