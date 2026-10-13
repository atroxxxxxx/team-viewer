import os
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from support_protocol.api import TicketAPI
from support_protocol.endpoints import config_file, load_endpoints
from support_protocol.media import FRAME_HEIGHT, FRAME_WIDTH, media_action, publish_args
from support_protocol.policy import validate_input
from support_protocol.tunnel import LocalRTSPBridge

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state" / "account_id"


class ClientWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Клиент техподдержки")
        api_base, relay_host, relay_port, username, password, tls_verify = load_endpoints(
            config_file(ROOT / "config.yaml", "SUPPORT_CLIENT_CONFIG")
        )
        self.api = TicketAPI(api_base, username, password, verify=tls_verify)
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.account_id = _load_id(STATE)
        self.ticket_id = ""
        self.tunnel_id = ""
        self.media_gen = 0
        self._starting = False
        self.screen_failed = False
        self.next_start = 0.0
        self.bridge: LocalRTSPBridge | None = None
        self.answered_grant = ""
        self.handled_inputs: set[str] = set()
        self.procs: list[QProcess] = []
        self.scale = 1
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

    def _build(self) -> None:
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Что случилось")
        self.camera = QCheckBox("Отдать камеру")
        self.dangerous = QCheckBox("На экране диалог удаления или сброса")
        self.status = QLabel("Заявки нет")
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat_line = QLineEdit()
        self.steps = QListWidget()
        self.observation = QLineEdit()
        self.observation.setPlaceholderText("Что получилось на этом шаге")
        start = QPushButton("Создать заявку")
        start.clicked.connect(self.start_ticket)
        send = QPushButton("Отправить в чат")
        send.clicked.connect(self.send_chat)
        note = QPushButton("Сообщить по шагу")
        note.clicked.connect(self.send_observation)
        yes = QPushButton("Проблема решена")
        yes.clicked.connect(lambda: self.resolve(True))
        no = QPushButton("Проблема осталась")
        no.clicked.connect(lambda: self.resolve(False))
        form = QFormLayout()
        form.addRow("Описание", self.description)
        form.addRow(self.camera)
        form.addRow(self.dangerous)
        buttons = QHBoxLayout()
        buttons.addWidget(yes)
        buttons.addWidget(no)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(start)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Чат"))
        layout.addWidget(self.chat)
        layout.addWidget(self.chat_line)
        layout.addWidget(send)
        layout.addWidget(QLabel("Шаги"))
        layout.addWidget(self.steps)
        layout.addWidget(self.observation)
        layout.addWidget(note)
        layout.addLayout(buttons)
        self.resize(520, 720)

    def start_ticket(self) -> None:
        text = self.description.toPlainText().strip()
        if not text:
            self.status.setText("Нужно описание")
            return
        self.stop_media()
        self.answered_grant = ""
        self.handled_inputs.clear()
        try:
            created = self.api.create_ticket(self.account_id, text, self.camera.isChecked())
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        self.ticket_id = created["ticket_id"]
        self.api.ticket_token = created["ticket_token"]
        self.scale = 2
        self.status.setText(f"Заявка {self.ticket_id} ждёт оператора")

    def _publish(self, mode: str, url: str, gen: int, role: str) -> QProcess | None:
        try:
            args = publish_args(url, mode, os.environ.get("DISPLAY", ":0"))
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return None
        proc = QProcess(self)
        proc.finished.connect(lambda *_args, g=gen, r=role, p=proc: self._on_publish_exit(g, r, p))
        proc.start(args[0], args[1:])
        if not proc.waitForStarted(3000) or proc.state() == QProcess.ProcessState.NotRunning:
            proc.kill()
            return None
        self.procs.append(proc)
        return proc

    def _on_publish_exit(self, gen: int, role: str, proc: QProcess) -> None:
        if gen != self.media_gen or self._starting:
            return
        if proc in self.procs:
            self.procs.remove(proc)
        if role != "screen":
            return
        err = bytes(proc.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if not self.tunnel_id:
            self.screen_failed = True
        self.next_start = time.monotonic() + 2
        self.stop_media()
        self.status.setText(err.splitlines()[-1][:180] if err else "Публикация экрана оборвалась, повторяю")

    def _start_tunnel(self, account_id: str, tunnel_id: str, camera: bool) -> None:
        self._starting = True
        self.stop_media()
        gen = self.media_gen
        try:
            self.bridge = LocalRTSPBridge(self.relay_host, self.relay_port, "CLIENT", account_id)
            screen = self.bridge.url(f"/tunnels/{tunnel_id}/screen")
            screen_mode = "testsrc" if self.screen_failed else "screen"
            screen_proc = self._publish(screen_mode, screen, gen, "screen")
            if screen_proc is None and screen_mode == "screen":
                self.scale = 1
                self.screen_failed = True
                screen_proc = self._publish("testsrc", screen, gen, "screen")
            if screen_proc is None or screen_proc.state() == QProcess.ProcessState.NotRunning:
                self.screen_failed = True
                self.next_start = time.monotonic() + 2
                self.stop_media()
                self.status.setText("Не удалось запустить публикацию экрана")
                return
            if camera:
                camera_url = self.bridge.url(f"/tunnels/{tunnel_id}/camera")
                camera_mode = "camera" if os.path.exists("/dev/video0") else "testsrc"
                if self._publish(camera_mode, camera_url, gen, "camera") is None and camera_mode == "camera":
                    self._publish("testsrc", camera_url, gen, "camera")
            self.tunnel_id = tunnel_id
            self.status.setText(f"Экран идёт в туннель {tunnel_id}")
        finally:
            self._starting = False

    def refresh(self) -> None:
        if not self.ticket_id:
            return
        try:
            session = self.api.session(self.ticket_id)
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        ticket = session.get("ticket") or {}
        status = ticket.get("status", "")
        tunnel_id = ticket.get("tunnel_id") or ""
        if not self._starting:
            action = media_action(self.tunnel_id, tunnel_id, status)
            if action == "stop":
                self.stop_media()
                self.status.setText(f"{status} · {self.ticket_id}")
            elif action == "start" and time.monotonic() >= self.next_start:
                self._start_tunnel(ticket.get("account_id") or self.account_id, tunnel_id, bool(ticket.get("camera")))
            elif self.bridge is None:
                self.status.setText(f"{status} · {self.ticket_id}")
        lines = [f"{item.get('author')}: {item.get('body')}" for item in session.get("messages") or []]
        self.chat.setPlainText("\n".join(lines))
        self.steps.clear()
        for step in session.get("steps") or []:
            self.steps.addItem(f"{step.get('status')} {step.get('id')} {step.get('body')}")
        control = session.get("control") or {}
        request = session.get("control_request") or {}
        pending = request if request.get("status") == "pending" else control
        grant_id = pending.get("grant_id") or ""
        if pending.get("status") == "pending" and grant_id and grant_id != self.answered_grant:
            self.answered_grant = grant_id
            self._ask_control(grant_id)
        for item in session.get("inputs") or []:
            action_id = str(item.get("ID") or item.get("id") or "")
            if action_id and action_id not in self.handled_inputs:
                self.handled_inputs.add(action_id)
                self._apply_input(action_id, item)

    def _ask_control(self, grant_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "Управление",
            "Разрешить оператору мышь и клавиатуру в этой сессии? Оболочка и удаление файлов недоступны.",
        )
        allow = answer == QMessageBox.StandardButton.Yes
        try:
            self.api.decide_control(self.ticket_id, grant_id, allow, "pointer,key,text" if allow else "")
        except RuntimeError as exc:
            self.status.setText(str(exc))

    def _apply_input(self, action_id: str, item: dict) -> None:
        detail = item.get("Detail") or item.get("detail") or {}
        if isinstance(detail, str):
            detail = {}
        grant = {"status": "active", "scope": "pointer,key,text"}
        try:
            session = self.api.session(self.ticket_id)
            grant = session.get("control") or grant
        except RuntimeError:
            pass
        reason = validate_input(detail, grant, FRAME_WIDTH, FRAME_HEIGHT)
        if reason is None and self.dangerous.isChecked():
            confirm = QMessageBox.question(self, "Опасный диалог", "На экране системный диалог. Исполнить это событие?")
            if confirm != QMessageBox.StandardButton.Yes:
                reason = "клиент остановил ввод на системном диалоге"
        if reason is not None:
            phase = "rejected_by_client"
            observation = reason
        else:
            observation = self._inject(detail)
            phase = "applied" if observation == "ввод исполнен в сессии клиента" else "rejected_by_client"
        try:
            self.api.finish_input(self.ticket_id, action_id, phase, observation or "")
        except RuntimeError as exc:
            self.status.setText(str(exc))

    def _inject(self, event: dict) -> str:
        xdotool = "xdotool"
        kind = event.get("type")
        if kind == "pointer":
            pointer = event.get("pointer") or {}
            x = int(pointer["x"]) * self.scale
            y = int(pointer["y"]) * self.scale
            args = [xdotool, "mousemove", str(x), str(y), "click", "1"]
        elif kind == "text":
            args = [xdotool, "type", "--delay", "12", "--", str(event.get("text"))]
        else:
            code = str((event.get("key") or {}).get("code") or "")
            if not code.replace("_", "").isalnum():
                return "клавиша отклонена"
            args = [xdotool, "key", "--", code]
        proc = QProcess(self)
        proc.start(args[0], args[1:])
        if not proc.waitForStarted(1000):
            return "валидатор пропустил событие, xdotool недоступен"
        if not proc.waitForFinished(3000) or proc.exitCode() != 0:
            return "xdotool не исполнил событие"
        return "ввод исполнен в сессии клиента"

    def send_chat(self) -> None:
        self._guarded(lambda: self.api.message(self.ticket_id, "client", self.chat_line.text().strip()))
        self.chat_line.clear()

    def send_observation(self) -> None:
        step_id = _selected_id(self.steps)
        if not step_id:
            return
        self._guarded(lambda: self.api.observe(self.ticket_id, step_id, self.observation.text().strip()))

    def resolve(self, accept: bool) -> None:
        self._guarded(lambda: self.api.resolve(self.ticket_id, "client", accept))

    def _guarded(self, call) -> None:
        if not self.ticket_id:
            return
        try:
            call()
        except RuntimeError as exc:
            self.status.setText(str(exc))

    def stop_media(self) -> None:
        self.media_gen += 1
        self.tunnel_id = ""
        for proc in self.procs:
            proc.kill()
        self.procs.clear()
        if self.bridge is not None:
            self.bridge.close()
            self.bridge = None

    def closeEvent(self, event) -> None:
        self.stop_media()
        super().closeEvent(event)


def _load_id(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = str(uuid.uuid4())
    path.write_text(value, encoding="utf-8")
    return value


def _selected_id(widget: QListWidget) -> str:
    item = widget.currentItem()
    if item is None:
        return ""
    parts = item.text().split()
    return parts[1] if len(parts) > 1 else ""
