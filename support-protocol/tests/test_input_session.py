import sys
import threading
import time

import pytest

pytest.importorskip("PyQt5")
from client_app.input_session import InputSession


def test_cancels_running_os_input_and_blocks_further_launches(tmp_path):
    marker = tmp_path / "started"
    session = InputSession()
    result = []
    command = [sys.executable, "-c", f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(10)"]
    task = threading.Thread(target=lambda: result.append(session.execute(command)))
    task.start()
    deadline = time.monotonic() + 1
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.005)
    assert marker.exists()
    session.cancel()
    task.join(1)
    assert not task.is_alive() and result == [False]
    assert session.execute(["this-command-must-not-be-started"]) is False
