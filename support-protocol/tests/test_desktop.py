import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PyQt5")
from PyQt5.QtCore import Qt, QThreadPool
from PyQt5.QtWidgets import QApplication, QTabWidget
from support_protocol.api import TicketAPI
from support_protocol.errors import ServiceUnavailable, CONNECTION_MESSAGE
from support_protocol.settings import UserSettings
from client_app import window as client_module
from operator_app import window as operator_module


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    QThreadPool.globalInstance().waitForDone(4000)


def pump(app, predicate, timeout=2):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def windows(app, monkeypatch, tmp_path):
    monkeypatch.setenv("SUPPORT_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(client_module, "STATE", tmp_path / "client-id")
    monkeypatch.setattr(operator_module, "STATE", tmp_path / "operator-id")
    monkeypatch.setattr(TicketAPI, "health", lambda self: {"status": "ok"})
    monkeypatch.setattr(TicketAPI, "list_queued", lambda self: [])
    c, o = client_module.ClientWindow(), operator_module.OperatorWindow()
    pump(app, lambda: not o._tasks)
    yield c, o
    c.close(); o.close()
    pump(app, lambda: QThreadPool.globalInstance().activeThreadCount() == 0)
    app.processEvents()


def test_preferences_survive_window_restart(app, windows):
    c, o = windows
    c.camera.setChecked(True)
    c.dangerous.setChecked(True)
    o.allow_model_input.setChecked(True)
    c.findChild(QTabWidget).setCurrentIndex(2)
    assert c.save_preferences(api_base="http://localhost:9999", video_fps=25, tls_verify=True)
    assert o.save_preferences(relay_port=8888)
    # Значения уже записаны, даже пока первые окна открыты.
    assert UserSettings("client").get("camera") is True
    next_c, next_o = client_module.ClientWindow(), operator_module.OperatorWindow()
    assert next_c.camera.isChecked() and next_c.dangerous.isChecked()
    assert next_o.allow_model_input.isChecked()
    assert next_c.findChild(QTabWidget).currentIndex() == 2
    assert next_c.api.base == "http://localhost:9999" and next_c.api.verify
    assert next_o.relay_port == 8888
    next_c.close(); next_o.close()


def test_queue_updates_preserves_selection_and_does_not_rebuild_unchanged(app, windows):
    _, o = windows
    o.show()
    app.processEvents()
    assert o.queue_timer.interval() == 3000
    tickets = [{"ticket_id": str(i), "description": f"item {i}"} for i in range(30)]
    o.api.list_queued = lambda: list(tickets)
    o.reload_queue()
    pump(app, lambda: not o._queue_polling)
    o.queue.setCurrentRow(10)
    item = o.queue.currentItem()
    o.queue.verticalScrollBar().setValue(5)
    o.reload_queue()
    pump(app, lambda: not o._queue_polling)
    assert o.queue.currentItem() is item
    assert o.queue.verticalScrollBar().value() == 5
    tickets[10] = {"ticket_id": "10", "description": "changed"}
    tickets.pop(0)
    tickets.append({"ticket_id": "new", "description": "created"})
    o.queue_timer.setInterval(20)
    pump(app, lambda: o.queue.item(0).data(Qt.UserRole) == "1")
    assert o.queue.currentItem().data(Qt.UserRole) == "10"
    assert "changed" in o.queue.currentItem().text()
    assert o.queue.item(o.queue.count() - 1).data(Qt.UserRole) == "new"


def test_queue_non_overlapping_and_closes_running_task(app, windows):
    c, o = windows
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow():
        entered.set(); calls.append(1); release.wait(1)
        return []
    o.api.list_queued = slow
    o.reload_queue()
    assert entered.wait(.5)
    for _ in range(10): o.reload_queue()
    assert len(calls) == 1
    before = o.status.text()
    o.close()
    assert not o._tasks and not o.timer.isActive() and not o.queue_timer.isActive() and not o.render_timer.isActive()
    o.reload_queue()
    release.set()
    pump(app, lambda: QThreadPool.globalInstance().activeThreadCount() == 0)
    app.processEvents()
    assert o.status.text() == before
    assert len(calls) == 1


@pytest.mark.parametrize("during_session", [False, True])
def test_client_outage_recovery_without_changing_settings(app, windows, during_session):
    c, _ = windows
    c.save_preferences(camera=True)
    if during_session: c.ticket_id = "active"
    def down(*_): raise ServiceUnavailable()
    c.api.health = down
    c.api.session = down
    c._next_probe = 0
    c.refresh()
    pump(app, lambda: not c._polling)
    assert c.status.text() == CONNECTION_MESSAGE
    assert c.settings.get("camera") is True
    c.api.health = lambda: {}
    c.api.session = lambda _: {"ticket": {"status": "queued"}}
    c._next_probe = 0
    c.refresh()
    pump(app, lambda: not c._polling)
    assert c.status.text() != CONNECTION_MESSAGE
    assert c.settings.get("camera") is True


def test_operator_down_at_start_and_automatic_recovery(app, monkeypatch, tmp_path):
    monkeypatch.setenv("SUPPORT_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(operator_module, "STATE", tmp_path / "id")
    def down(_): raise ServiceUnavailable()
    monkeypatch.setattr(TicketAPI, "list_queued", down)
    o = operator_module.OperatorWindow()
    try:
        pump(app, lambda: not o._queue_polling)
        assert o.status.text() == CONNECTION_MESSAGE
        o.api.list_queued = lambda: [{"ticket_id": "new", "description": "restored"}]
        o.queue_timer.setInterval(20)
        pump(app, lambda: o.queue.count() == 1)
        assert o.status.text() != CONNECTION_MESSAGE
    finally:
        o.close()


def test_latest_frame_only_is_presented(windows):
    _, o = windows
    size = 640 * 360 * 3
    class Player:
        def readAllStandardOutput(self): return b"\x01" * size + b"\x02" * size + b"\x03" * 10
    o.player = Player()
    o._read_frame()
    assert o._pending_frame == b"\x02" * size
    assert len(o.buffer) == 10
    o._present_frame()
    assert o._pending_frame is None
    assert o.frame.pixelColor(0, 0).red() == 2
    o.player = None


def test_cancel_queued_work_and_no_callbacks_after_close(app, windows):
    _, o = windows
    pool = QThreadPool.globalInstance()
    old_limit = pool.maxThreadCount()
    pool.setMaxThreadCount(1)
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []
    try:
        o.run_background(lambda: (entered.set(), release.wait(1)), lambda _: results.append("late"))
        assert entered.wait(.5)
        o.run_background(lambda: calls.append("queued"))
        o.close()
        release.set()
        pump(app, lambda: pool.activeThreadCount() == 0)
        app.processEvents()
        assert not calls and not results and not o._tasks
    finally:
        release.set()
        pool.setMaxThreadCount(old_limit)


def test_connection_dialog_applies_and_persists(app, windows):
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QDialogButtonBox, QFormLayout
    from support_protocol.ui import connection_settings
    c, _ = windows
    pump(app, lambda: not c._tasks)
    def apply():
        dialog = app.activeModalWidget()
        form = dialog.findChild(QFormLayout)
        form.itemAt(0, QFormLayout.FieldRole).widget().setText("https://new.example:8443")
        form.itemAt(1, QFormLayout.FieldRole).widget().setText("new-relay.example")
        form.itemAt(2, QFormLayout.FieldRole).widget().setValue(9999)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
    QTimer.singleShot(0, apply)
    connection_settings(c)
    assert c.api.base == "https://new.example:8443"
    assert c.relay_host == "new-relay.example" and c.relay_port == 9999
    assert UserSettings("client").get("api_base") == c.api.base
    assert not c._editing_settings


def test_connection_error_fits_small_client_window(app, windows):
    c, _ = windows
    c._next_probe = float("inf")
    c.resize(720, 540)
    c.show()
    app.processEvents()
    c.report_error(ServiceUnavailable())
    app.processEvents()
    pump(app, lambda: c.status.parent().height() >= c.status.parent().minimumSizeHint().height())
    assert c.status.height() >= c.status.heightForWidth(c.status.width())


def _grant(client):
    client.ticket_id = "test-ticket"
    client._control = {"status": "active", "scope": "pointer,key,text", "grant_id": "grant"}
    client._next_probe = float("inf")


def test_input_commands_are_serial(app, windows):
    c, _ = windows
    _grant(c)
    entered, release = threading.Event(), threading.Event()
    order = []
    def inject(detail, token):
        order.append(("start", detail["type"]))
        if detail["type"] == "text":
            entered.set(); release.wait(1)
        order.append(("end", detail["type"]))
        return "ввод исполнен в сессии клиента"
    c._inject = inject
    c.api.finish_input = lambda *args: {}
    c._apply_input("text", {"detail": {"type": "text", "intent": "type_in_field", "text": "hello"}})
    assert entered.wait(.5)
    c._apply_input("pointer", {"detail": {"type": "pointer", "intent": "adjust_app_setting", "pointer": {"x": 1, "y": 1}}})
    assert order == [("start", "text")]
    release.set()
    pump(app, lambda: not c._input_busy and not c._input_queue)
    assert order == [("start", "text"), ("end", "text"), ("start", "pointer"), ("end", "pointer")]


@pytest.mark.parametrize("end_session", [True, False])
def test_queued_input_rechecks_session_and_permission(app, windows, end_session):
    c, o = windows
    _grant(c)
    pool = QThreadPool.globalInstance()
    limit = pool.maxThreadCount()
    pool.setMaxThreadCount(1)
    entered, release = threading.Event(), threading.Event()
    injected, results = [], []
    try:
        o.run_background(lambda: (entered.set(), release.wait(1)))
        assert entered.wait(.5)
        c._inject = lambda *_: injected.append("bad")
        c.api.finish_input = lambda *args: results.append(args)
        c._apply_input("queued", {"detail": {"type": "text", "intent": "type_in_field", "text": "hello"}})
        if end_session:
            c._show_session({"ticket": {"status": "resolved"}})
        else:
            c._control = {"status": "revoked"}
        release.set()
        pump(app, lambda: pool.activeThreadCount() == 0)
        app.processEvents()
        assert not injected
        if not end_session:
            assert results[0][2] == "rejected_by_client"
    finally:
        release.set(); pool.setMaxThreadCount(limit)


def test_ending_session_inside_confirmation_cancels_input(app, windows, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    c, _ = windows
    _grant(c)
    c.dangerous.setChecked(True)
    calls = []
    c._inject = lambda *_: calls.append("bad")
    def confirm(*_):
        c._show_session({"ticket": {"status": "resolved"}})
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", confirm)
    c._apply_input("confirm", {"detail": {"type": "text", "intent": "type_in_field", "text": "hello"}})
    assert not calls and not c._input_busy


@pytest.mark.parametrize("role,field", [("client", "chat_line"), ("client", "observation"), ("operator", "chat_line")])
def test_old_send_result_does_not_erase_new_draft(app, windows, role, field):
    c, o = windows
    window = c if role == "client" else o
    window.ticket_id = "ticket"
    entered, release = threading.Event(), threading.Event()
    def send(*_):
        entered.set(); release.wait(1); return {}
    window.api.message = send
    window.api.observe = send
    editor = getattr(window, field)
    editor.setText("first")
    if field == "observation":
        window.steps.addItem("pending step body")
        window.steps.setCurrentRow(0)
        window.send_observation()
    else:
        window.send_chat()
    assert entered.wait(.5)
    editor.setText("second")
    release.set()
    pump(app, lambda: not window._tasks)
    assert editor.text() == "second"


@pytest.mark.parametrize("role", ["client", "operator"])
def test_step_selection_and_scroll_survive_session_updates(app, windows, role):
    c, o = windows
    w = c if role == "client" else o
    w.show(); app.processEvents()
    session = {"ticket": {"status": "queued"}, "steps": [{"id": str(i), "status": "pending", "body": str(i)} for i in range(100)]}
    def update():
        if w is c:
            w._show_session(session)
        else:
            w.session = session; w._show_session()
        app.processEvents()
    update()
    w.steps.setCurrentRow(10)
    w.steps.doItemsLayout()
    w.steps.verticalScrollBar().setValue(40)
    selected, scroll = w.steps.currentItem(), w.steps.verticalScrollBar().value()
    update()
    assert w.steps.currentItem() is selected
    session["steps"][10]["body"] = "changed"
    update()
    assert w.steps.currentItem() is selected
    assert w.steps.verticalScrollBar().value() == scroll


def test_changed_queue_does_not_jump_to_selection(app, windows):
    _, o = windows
    o.show(); app.processEvents()
    tickets = [{"ticket_id": str(i), "description": str(i)} for i in range(100)]
    o.api.list_queued = lambda: tickets
    o.reload_queue(); pump(app, lambda: not o._queue_polling)
    o.queue.setCurrentRow(10)
    o.queue.doItemsLayout()
    o.queue.verticalScrollBar().setValue(40)
    scroll = o.queue.verticalScrollBar().value()
    tickets[10]["description"] = "changed"
    o.reload_queue(); pump(app, lambda: not o._queue_polling)
    app.processEvents()
    assert o.queue.verticalScrollBar().value() == scroll
    assert o.queue.currentItem().data(Qt.UserRole) == "10"


@pytest.mark.parametrize("action", ["chat", "control", "plan", "complete", "problem", "resolve"])
def test_queued_operator_actions_do_not_move_to_new_ticket(app, windows, action):
    _, o = windows
    pool = QThreadPool.globalInstance()
    limit = pool.maxThreadCount(); pool.setMaxThreadCount(1)
    entered, release = threading.Event(), threading.Event()
    calls = []
    try:
        o.run_background(lambda: (entered.set(), release.wait(1)))
        assert entered.wait(.5)
        o.ticket_id = "old"
        o.chat_line.setText("message for old")
        o.plan_text.setPlainText("Откройте настройки")
        o.problem.setText("problem")
        o.steps.addItem("pending step body"); o.steps.setCurrentRow(0)
        for name in ("message", "request_control", "create_plan", "complete_step", "add_problem", "resolve"):
            setattr(o.api, name, lambda *args: calls.append(args))
        {"chat": o.send_chat, "control": o.request_control, "plan": o.publish_plan,
         "complete": o.complete_step, "problem": o.add_problem, "resolve": lambda: o.resolve(True)}[action]()
        o.ticket_id = "new"
        release.set()
        pump(app, lambda: not o._tasks)
        assert not calls
    finally:
        release.set(); pool.setMaxThreadCount(limit)


def test_model_job_returns_result_and_is_killed_on_close(app, windows, monkeypatch, tmp_path):
    from operator_app import model_process
    from PyQt5.QtCore import QProcess
    _, o = windows
    worker = tmp_path / "worker.py"
    worker.write_text("import sys,json,time\np=json.load(sys.stdin)\nif p.get('sleep'): time.sleep(10)\njson.dump({'ok':True},sys.stdout)\n")
    monkeypatch.setattr(model_process, "WORKER", worker)
    results = []
    model_process.ModelProcess(o, "test", {}, results.append, lambda e: pytest.fail(str(e)))
    pump(app, lambda: bool(results))
    assert results == [{"ok": True}] and not o._model_jobs
    job = model_process.ModelProcess(o, "test", {"sleep": True}, results.append, lambda e: pytest.fail(str(e)))
    pump(app, lambda: job.state() == QProcess.Running)
    o.close()
    pump(app, lambda: job.completed)
    assert not o._model_jobs and results == [{"ok": True}]


def test_application_exits_promptly_while_model_worker_is_running(tmp_path):
    import subprocess
    import sys
    worker = tmp_path / "slow_model.py"
    worker.write_text("import json,sys,time\njson.load(sys.stdin)\ntime.sleep(10)\n")
    program = f'''
from pathlib import Path
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from operator_app import window, model_process
from support_protocol.api import TicketAPI
TicketAPI.list_queued = lambda _: []
window.STATE = Path({str(tmp_path / "state")!r})
model_process.WORKER = Path({str(worker)!r})
app = QApplication([])
w = window.OperatorWindow()
w.show()
w.prepare_models()
QTimer.singleShot(100, w.close)
app.exec_()
print("exited", flush=True)
'''
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), SUPPORT_SETTINGS_DIR=str(tmp_path))
    result = subprocess.run([sys.executable, "-c", program], env=environment, capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
    assert "exited" in result.stdout


def test_new_frames_are_presented_on_demand_without_periodic_rate_limit(app, windows):
    _, o = windows
    assert o.render_timer.isSingleShot() and o.render_timer.interval() == 0
    assert not o.render_timer.isActive()
    frame_size = 640 * 360 * 3
    class Player:
        def readAllStandardOutput(self):
            return b"\x07" * frame_size
    o.player = Player()
    o._read_frame()
    assert o.render_timer.isActive()
    pump(app, lambda: o._pending_frame is None)
    assert o.frame.pixelColor(0, 0).red() == 7
    assert not o.render_timer.isActive()
    o.player = None


def test_fps_counter_counts_new_painted_frames_only(app, windows):
    from PyQt5.QtGui import QImage
    c, o = windows
    o.show()
    app.processEvents()
    o.frame = QImage(640, 360, QImage.Format_RGB888)
    o.frame.fill(Qt.black)
    o._render_frame()
    pump(app, lambda: len(o._frame_times) == 1)
    for _ in range(4):
        o.preview.update()
        app.processEvents()
    o._render_frame()  # resize-style rescale of the same frame must not add FPS
    app.processEvents()
    assert len(o._frame_times) == 1


def test_fps_label_uses_measured_rate_and_expires(windows, monkeypatch):
    from types import SimpleNamespace
    _, o = windows
    clock = [0.0]
    monkeypatch.setattr(operator_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    for index in range(61):
        clock[0] = index / 30
        o._record_video_frame()
    o._update_video_rate()
    assert o.video_rate.text() == "30 FPS"
    clock[0] += 3
    o._update_video_rate()
    assert o.video_rate.text() == "— FPS"


def test_client_publishes_60_after_loading_old_15_profile(app, monkeypatch, tmp_path):
    import json
    from support_protocol.media import DEFAULT_FPS
    monkeypatch.setenv("SUPPORT_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(client_module, "STATE", tmp_path / "id")
    monkeypatch.setattr(TicketAPI, "health", lambda _: {})
    (tmp_path / "client.json").write_text(json.dumps({"version": 1, "values": {"video_fps": 15}}))
    client = client_module.ClientWindow()
    try:
        captured = []
        monkeypatch.setattr(client_module, "publish_args", lambda *a, **kw: captured.append(kw) or ["not-started"])
        class Process:
            def __init__(self, *_):
                self.finished = SimpleSignal()
                self.errorOccurred = SimpleSignal()
            def start(self, *_): pass
            def kill(self): pass
            def deleteLater(self): pass
        class SimpleSignal:
            def connect(self, *_): pass
        monkeypatch.setattr(client_module, "QProcess", Process)
        client._publish("screen", "rtsp://localhost/stream", 0, "screen")
        assert captured[0]["fps"] == DEFAULT_FPS == 60
    finally:
        client.close()


@pytest.mark.parametrize("fps", [60, 144, 1000])
def test_fps_dialog_accepts_user_rate_above_previous_cap(app, windows, fps):
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QDialogButtonBox, QFormLayout, QLineEdit
    from support_protocol.ui import connection_settings
    c, _ = windows
    pump(app, lambda: not c._tasks)
    def apply():
        dialog = app.activeModalWidget()
        form = dialog.findChild(QFormLayout)
        field = form.itemAt(4, QFormLayout.FieldRole).widget()
        assert isinstance(field, QLineEdit)
        assert field.text() == "60"
        field.setText(str(fps))
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
    QTimer.singleShot(0, apply)
    connection_settings(c)
    assert UserSettings("client").get("video_fps") == fps
