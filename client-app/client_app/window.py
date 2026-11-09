import os
import time
import uuid
import logging
from pathlib import Path
from collections import deque

from client_app.input_session import InputSession

from PyQt5.QtCore import QProcess, QTimer, Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget, QTabWidget, QScrollArea,
)

from support_protocol.labels import step_line, ticket_status
from support_protocol.ui import Background, STYLE, ask_yes_no, connection_settings, card, header, label, secondary, StatusLabel, CheckBox, sync_rows
from support_protocol.settings import UserSettings
from support_protocol.api import TicketAPI
from support_protocol.endpoints import config_file, load_endpoints
from support_protocol.media import FRAME_HEIGHT, FRAME_WIDTH, DEFAULT_FPS, media_action, publish_args, session_ended
from support_protocol.policy import validate_input
from support_protocol.tunnel import LocalRTSPBridge

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state" / "account_id"


class ClientWindow(QWidget, Background):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Клиент техподдержки")
        self.settings = UserSettings("client")
        api_base, relay_host, relay_port, username, password, tls_verify = self.settings.endpoints(load_endpoints(
            config_file(ROOT / "config.yaml", "SUPPORT_CLIENT_CONFIG")
        ))
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
        self._input_queue = deque()
        self._input_session = InputSession()
        self._input_busy = False
        self._control = {}
        self.procs: list[QProcess] = []
        self.scale = 1
        self.init_background()
        self._polling = False
        self._creating = False
        self._status_until = 0.0
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self._next_probe = 0.0
        QTimer.singleShot(0, self.refresh)

    def _build(self) -> None:
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Что случилось")
        self.camera = CheckBox("Отдать камеру")
        self.dangerous = CheckBox("На экране диалог удаления или сброса")
        self.status = StatusLabel("Заявки нет")
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
        yes = QPushButton("Решено")
        yes.clicked.connect(lambda: self.resolve(True))
        no = QPushButton("Не решено")
        no.clicked.connect(lambda: self.resolve(False))
        self.description.setPlaceholderText("Описание проблемы")
        self.chat_line.setPlaceholderText("Сообщение оператору")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        secondary(no)
        secondary(note)
        secondary(send)

        device, device_layout = card()
        device_layout.addWidget(label("ВАШЕ УСТРОЙСТВО", "eyebrow"))
        identity_box, identity_layout = card("inset", 14, 7)
        identity_layout.addWidget(label("ИДЕНТИФИКАТОР УСТРОЙСТВА", "eyebrow"))
        identity = label(self.account_id, "identity", True)
        identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        identity_layout.addWidget(identity)
        device_layout.addWidget(identity_box)
        state_card, state_layout = card()
        state_layout.addWidget(label("СОСТОЯНИЕ ЗАЯВКИ", "eyebrow"))
        state_layout.addWidget(self.status)
        left_column = QVBoxLayout()
        left_column.setSpacing(16)
        left_column.addWidget(device)
        left_column.addWidget(state_card)
        left_column.addStretch()

        request = QWidget()
        request.setObjectName("tabPage")
        form = QVBoxLayout(request)
        form.setContentsMargins(0, 18, 0, 0)
        form.setSpacing(13)
        form.addWidget(label("Опишите проблему", "cardTitle"))
        form.addWidget(self.description, 1)
        form.addWidget(self.camera)
        form.addWidget(self.dangerous)
        form.addWidget(start)
        discussion = QWidget()
        discussion.setObjectName("tabPage")
        chat_layout = QVBoxLayout(discussion)
        chat_layout.setContentsMargins(0, 18, 0, 0)
        chat_layout.setSpacing(12)
        chat_layout.addWidget(label("Сообщения", "cardTitle"))
        chat_layout.addWidget(self.chat, 1)
        chat_layout.addWidget(self.chat_line)
        chat_layout.addWidget(send)
        plan_page = QWidget()
        plan_page.setObjectName("tabPage")
        plan_layout = QVBoxLayout(plan_page)
        plan_layout.setContentsMargins(0, 18, 0, 0)
        plan_layout.setSpacing(10)
        plan_layout.addWidget(label("План действий", "cardTitle"))
        plan_layout.addWidget(self.steps, 1)
        plan_layout.addWidget(label("Ответ оператор увидит в чате и в этом списке", "muted"))
        plan_layout.addWidget(self.observation)
        plan_layout.addWidget(note)
        resolution = QHBoxLayout()
        resolution.addWidget(yes)
        resolution.addWidget(no)
        plan_layout.addLayout(resolution)
        tabs = QTabWidget()
        tabs.addTab(request, "Заявка")
        tabs.addTab(discussion, "Чат")
        tabs.addTab(plan_page, "План")
        workspace, workspace_layout = card()
        workspace_layout.addWidget(label("ОБРАЩЕНИЕ В ПОДДЕРЖКУ", "eyebrow"))
        workspace_layout.addWidget(tabs, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 28)
        layout.setSpacing(16)
        layout.addLayout(header("Удалённая поддержка", lambda: connection_settings(self)))
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addLayout(left_column, 4)
        columns.addWidget(workspace, 6)
        layout.addLayout(columns, 1)
        self.setStyleSheet(STYLE)
        self.resize(1050, 690)
        self.setMinimumSize(720, 540)
        self.restore_preferences(tabs)

    def start_ticket(self) -> None:
        text = self.description.toPlainText().strip()
        if not text:
            self.status.setText("Нужно описание проблемы")
            return
        if self._creating:
            return
        if self.ticket_id:
            self.status.setText("Сначала завершите текущую заявку")
            return
        self.status.setText("Создание заявки...")
        camera = self.camera.isChecked()
        self._creating = True
        def done(created):
            self._creating = False
            self._cancel_inputs()
            self._input_session = InputSession()
            self.ticket_id = created["ticket_id"]
            self.api.ticket_token = created["ticket_token"]
            self.scale = 2
            self.status.setText(f"Заявка {self.ticket_id} ждёт оператора")
        def failed(error):
            self._creating = False
            self.report_error(error)
        self.run_background(lambda: self.api.create_ticket(self.account_id, text, camera), done, failed)
    def _publish(self, mode: str, url: str, gen: int, role: str) -> QProcess | None:
        try:
            args = publish_args(url, mode, os.environ.get("DISPLAY", ":0"),
                                fps=self.settings.get("video_fps", DEFAULT_FPS), crf=self.settings.get("video_crf", 23))
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return None
        proc = QProcess(self)
        proc.finished.connect(lambda *_args, g=gen, r=role, p=proc: self._on_publish_exit(g, r, p))
        proc.errorOccurred.connect(lambda _error, g=gen, r=role, p=proc: self._on_publish_exit(g, r, p))
        self.procs.append(proc)
        proc.start(args[0], args[1:])
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
        if err:
            log.error("FFmpeg publisher stopped: %s", err)
        proc.deleteLater()
        self.status.setText("Передача экрана прервана. Повторное подключение...")

    def _start_tunnel(self, account_id: str, tunnel_id: str, camera: bool) -> None:
        self._starting = True
        self.stop_media()
        gen = self.media_gen
        try:
            self.bridge = LocalRTSPBridge(self.relay_host, self.relay_port, "CLIENT", account_id)
            screen = self.bridge.url(f"/tunnels/{tunnel_id}/screen")
            screen_proc = self._publish("screen", screen, gen, "screen")
            if screen_proc is None:
                self.screen_failed = True
                self.next_start = time.monotonic() + 2
                self.stop_media()
                self.status.setText("Не удалось запустить публикацию экрана")
                return
            camera_unavailable = False
            if camera:
                camera_url = self.bridge.url(f"/tunnels/{tunnel_id}/camera")
                if os.path.exists("/dev/video0"):
                    self._publish("camera", camera_url, gen, "camera")
                else:
                    camera_unavailable = True
            self.tunnel_id = tunnel_id
            self.status.setText("Запуск передачи экрана. Камера недоступна" if camera_unavailable else "Запуск передачи экрана")
        finally:
            self._starting = False

    def refresh(self) -> None:
        if self._closing or self._editing_settings or self._polling:
            return
        if not self.ticket_id:
            if time.monotonic() < self._next_probe:
                return
            self._next_probe = time.monotonic() + 3
            self._polling = True
            def healthy(_):
                self._polling = False
                self.service_recovered()
            def unavailable(error):
                self._polling = False
                self.report_error(error)
            self.run_background(self.api.health, healthy, unavailable)
            return
        ticket_id = self.ticket_id
        self._polling = True
        def done(session):
            self._polling = False
            if ticket_id == self.ticket_id:
                self.service_recovered()
                self._show_session(session)
        def failed(error):
            self._polling = False
            self.report_error(error)
        self.run_background(lambda: self.api.session(ticket_id), done, failed)

    def _show_session(self, session: dict) -> None:
        ticket = session.get("ticket") or {}
        status = ticket.get("status", "")
        tunnel_id = ticket.get("tunnel_id") or ""
        if not self._starting:
            action = media_action(self.tunnel_id, tunnel_id, status)
            if action == "stop":
                self.stop_media()
                self._show_status(status)
            elif action == "start" and time.monotonic() >= self.next_start:
                self._start_tunnel(ticket.get("account_id") or self.account_id, tunnel_id, bool(ticket.get("camera")))
            elif self.bridge is None:
                self._show_status(status)
        lines = [f"{item.get('author')}: {item.get('body')}" for item in session.get("messages") or []]
        self.chat.setPlainText("\n".join(lines))
        sync_rows(self.steps, [(str(step.get("id", "")), step_line(step)) for step in session.get("steps") or []])
        if session_ended(status):
            self._cancel_inputs()
            self.stop_media()
            self.ticket_id = ""
            self.api.ticket_token = ""
            self.answered_grant = ""
            self.handled_inputs.clear()
            self.status.setText("Заявка завершена")
            return
        control = session.get("control") or {}
        self._control = control
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
        allow = ask_yes_no(self, "Управление", "Разрешить оператору мышь и клавиатуру в этой сессии? Оболочка и удаление файлов недоступны.")
        ticket_id = self.ticket_id
        self.run_background(lambda: self.api.decide_control(ticket_id, grant_id, allow, "pointer,key,text" if allow else ""),
                            failed=lambda error: self._grant_failed(error))

    def _grant_failed(self, error):
        self.answered_grant = ""
        self.report_error(error)
    def _cancel_inputs(self):
        self._input_session.cancel()
        self._input_queue.clear()
        self._input_busy = False
        self._control = {}

    def _apply_input(self, action_id: str, item: dict) -> None:
        self._input_queue.append((action_id, item))
        self._drain_input()

    def _drain_input(self):
        if self._input_busy or not self._input_queue or self._closing or not self.ticket_id:
            return
        token = self._input_session
        if token.cancelled.is_set():
            return
        action_id, item = self._input_queue.popleft()
        ticket_id = self.ticket_id
        self._input_busy = True
        detail = item.get("Detail") or item.get("detail") or {}
        if not isinstance(detail, dict):
            detail = {}
        reason = validate_input(detail, self._control, FRAME_WIDTH, FRAME_HEIGHT)
        if reason is None and self.dangerous.isChecked():
            if not ask_yes_no(self, "Опасный диалог", "На экране системный диалог. Исполнить это событие?"):
                reason = "клиент остановил ввод на системном диалоге"
        # Модальный диалог обрабатывает события, в том числе завершение сеанса/отзыв гранта.
        if token.cancelled.is_set() or token is not self._input_session or self.ticket_id != ticket_id:
            return
        reason = reason or validate_input(detail, self._control, FRAME_WIDTH, FRAME_HEIGHT)

        def execute():
            if token.cancelled.is_set():
                return
            current_reason = reason or validate_input(detail, self._control, FRAME_WIDTH, FRAME_HEIGHT)
            observation = current_reason or self._inject(detail, token)
            if token.cancelled.is_set():
                return
            phase = "applied" if not current_reason and observation == "ввод исполнен в сессии клиента" else "rejected_by_client"
            return self.api.finish_input(ticket_id, action_id, phase, observation)

        def finished(_):
            if token is self._input_session and not token.cancelled.is_set():
                self._input_busy = False
                QTimer.singleShot(0, self._drain_input)

        def failed(error):
            if token is self._input_session and not token.cancelled.is_set():
                self.report_error(error)
                finished(None)

        self.run_background(execute, finished, failed)

    def _inject(self, event: dict, token: InputSession) -> str:
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
        try:
            applied = token.execute(args)
        except OSError:
            log.exception("Cannot start OS input")
            return "xdotool недоступен"
        if not applied:
            return "ввод отменён или xdotool не исполнил событие"
        return "ввод исполнен в сессии клиента"

    def send_chat(self) -> None:
        text, ticket_id = self.chat_line.text().strip(), self.ticket_id
        if text and ticket_id:
            draft = self.chat_line.text()
            self.run_background(lambda: self.api.message(ticket_id, "client", text),
                                lambda _: self.chat_line.clear() if self.ticket_id == ticket_id and self.chat_line.text() == draft else None)
    def send_observation(self) -> None:
        step_id, text, ticket_id = _selected_id(self.steps), self.observation.text().strip(), self.ticket_id
        if step_id and text and ticket_id:
            draft = self.observation.text()
            self._note("Отправляю ответ оператору...")
            self.run_background(
                lambda: self.api.observe(ticket_id, step_id, text),
                lambda _: self._observation_sent(ticket_id, draft),
            )

    def _observation_sent(self, ticket_id: str, draft: str) -> None:
        if self.ticket_id == ticket_id and self.observation.text() == draft:
            self.observation.clear()
        if self.ticket_id == ticket_id:
            self._note("Ответ по шагу отправлен оператору: он в чате и в плане")

    def _show_status(self, status: str) -> None:
        if time.monotonic() < self._status_until:
            return
        self.status.setText(ticket_status(status, self.ticket_id))

    def _note(self, text: str) -> None:
        self._status_until = time.monotonic() + 5
        self.status.setText(text)

    def resolve(self, accept: bool) -> None:
        ticket_id = self.ticket_id
        if not ticket_id:
            return
        if not accept:
            review = self._ask_unresolved()
            if not review:
                return
            self._note("Отправляю отзыв и закрываю заявку...")
            self.run_background(
                lambda: self.api.resolve(ticket_id, "client", False, review, close=True),
                lambda result: self._after_resolve(ticket_id, result),
            )
            return
        self._note("Закрываю заявку...")
        self.run_background(
            lambda: self.api.resolve(ticket_id, "client", True),
            lambda result: self._after_resolve(ticket_id, result),
        )

    def _after_resolve(self, ticket_id: str, result: dict) -> None:
        if self.ticket_id != ticket_id:
            return
        status = str((result or {}).get("status") or "")
        if session_ended(status):
            self._status_until = 0.0
            self._show_session({"ticket": {"status": status}})
            return
        self._note("Сервис не закрыл заявку. Повторите.")

    def _ask_unresolved(self) -> str:
        dialog = QDialog(self)
        dialog.setWindowTitle("Не решено")
        what = QPlainTextEdit()
        what.setPlaceholderText("Что не получилось")
        why = QPlainTextEdit()
        why.setPlaceholderText("Почему")
        hint = QLabel("")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Отправить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")

        def accept_review() -> None:
            if not what.toPlainText().strip() or not why.toPlainText().strip():
                hint.setText("Нужно описать, что и почему не получилось")
                return
            dialog.accept()

        buttons.button(QDialogButtonBox.Ok).clicked.connect(accept_review)
        buttons.button(QDialogButtonBox.Cancel).clicked.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("Что не получилось", "cardTitle"))
        layout.addWidget(what)
        layout.addWidget(label("Почему", "cardTitle"))
        layout.addWidget(why)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return ""
        return f"Что не получилось: {what.toPlainText().strip()}\nПочему: {why.toPlainText().strip()}"
    def _guarded(self, call) -> None:
        if self.ticket_id:
            self.run_background(call)
    def stop_media(self) -> None:
        self.media_gen += 1
        self.tunnel_id = ""
        for proc in self.procs:
            proc.finished.connect(proc.deleteLater)
            proc.kill()
        self.procs.clear()
        if self.bridge is not None:
            self.bridge.close()
            self.bridge = None

    def closeEvent(self, event) -> None:
        self._cancel_inputs()
        self.close_background()
        self.timer.stop()
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
    return str(item.data(Qt.UserRole) or "")
