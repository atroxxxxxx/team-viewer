import uuid
import base64
import logging
from pathlib import Path

from PyQt5.QtCore import QBuffer, QIODevice, QProcess, Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget, QTabWidget, QSplitter,
)

from operator_app.model_process import ModelProcess
from support_protocol.ui import Background, STYLE, connection_settings, card, header, label, secondary, StatusLabel, CheckBox, DesktopPreview, sync_rows
from support_protocol.settings import UserSettings
from support_protocol.api import TicketAPI
from support_protocol.endpoints import config_file, load_endpoints
from support_protocol.media import FRAME_HEIGHT, FRAME_WIDTH, play_args, should_open_player, latest_frame
from support_protocol.policy import intent_allowed, validate_input, validate_step
from support_protocol.tunnel import LocalRTSPBridge

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state" / "operator_id"


class OperatorWindow(QWidget, Background):
    def __init__(self, models: dict | None = None):
        super().__init__()
        self.models = models
        self._model_jobs = set()
        self.setWindowTitle("Оператор техподдержки")
        self.settings = UserSettings("operator")
        api_base, relay_host, relay_port, username, password, tls_verify = self.settings.endpoints(load_endpoints(
            config_file(ROOT / "config.yaml", "SUPPORT_OPERATOR_CONFIG")
        ))
        self.api = TicketAPI(api_base, username, password, verify=tls_verify)
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.operator_id = _load_id(STATE)
        self.ticket_id = ""
        self.playing_tunnel = ""
        self.play_gen = 0
        self.bridge: LocalRTSPBridge | None = None
        self.session: dict = {}
        self.player: QProcess | None = None
        self.buffer = bytearray()
        self.frame: QImage | None = None
        self._pending_frame = None
        self._player_stderr = ""
        self.init_background()
        self._polling = False
        self._claiming = False
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.render_timer = QTimer(self)
        self.render_timer.setInterval(33)
        self.render_timer.timeout.connect(self._present_frame)
        self.render_timer.start()
        self.queue_timer = QTimer(self)
        self.queue_timer.setInterval(3000)
        self.queue_timer.timeout.connect(self.reload_queue)
        self.queue_timer.start()
        self._queue_polling = False
        self._queue_snapshot = None
        self.reload_queue()

    def _build(self) -> None:
        self.queue = QListWidget()
        self.status = StatusLabel("Очередь пуста")
        self.preview = DesktopPreview("Экран появится после захвата")
        self.preview.setFixedSize(FRAME_WIDTH, FRAME_HEIGHT)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.mousePressEvent = self.preview_click
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat_line = QLineEdit()
        self.steps = QListWidget()
        self.plan_text = QPlainTextEdit()
        self.plan_text.setPlaceholderText("По одному шагу на строку")
        self.problem = QLineEdit()
        self.problem.setPlaceholderText("Что сломал этот шаг")
        self.allow_model_input = CheckBox("Модель может предлагать ввод")
        if self.models is None:
            self.model_note = QLabel("Режим человека: модели не скачиваются и шаг модели выключен")
        else:
            self.model_note = QLabel(
                "Профиль {profile}: текст {text_repo}, кадр {vision_repo}, решение {decision_repo}. {reason}".format(**self.models)
            )
        self.model_note.setWordWrap(True)
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Текст в поле программы клиента")
        refresh = QPushButton("Обновить очередь")
        refresh.clicked.connect(self.reload_queue)
        claim = QPushButton("Захватить")
        claim.clicked.connect(self.claim)
        send = QPushButton("Написать")
        send.clicked.connect(self.send_chat)
        control = QPushButton("Запросить управление")
        control.clicked.connect(self.request_control)
        plan = QPushButton("Опубликовать план")
        plan.clicked.connect(self.publish_plan)
        done = QPushButton("Шаг виден на экране")
        done.clicked.connect(self.complete_step)
        nested = QPushButton("Вложенная проблема")
        nested.clicked.connect(self.add_problem)
        close = QPushButton("Предложить закрытие")
        close.clicked.connect(lambda: self.resolve(True))
        self.model_button = QPushButton("Шаг модели")
        self.model_button.clicked.connect(self.model_step)
        self.model_button.setEnabled(self.models is not None)
        self.allow_model_input.setEnabled(self.models is not None)
        typed = QPushButton("Отправить текст")
        typed.clicked.connect(self.send_text)
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.preview.setMinimumSize(280, 160)
        self.preview.setMaximumSize(16777215, 16777215)
        self.preview.setWordWrap(True)
        self.chat_line.setPlaceholderText("Сообщение клиенту")
        for button in (refresh, send, done, nested, close, typed):
            secondary(button)

        queue_page = QWidget()
        queue_page.setObjectName("tabPage")
        queue_layout = QVBoxLayout(queue_page)
        queue_layout.setContentsMargins(0, 18, 0, 0)
        queue_layout.setSpacing(12)
        queue_layout.addWidget(label("Очередь заявок", "cardTitle"))
        queue_layout.addWidget(self.queue, 1)
        queue_layout.addWidget(claim)
        queue_layout.addWidget(refresh)
        chat_page = QWidget()
        chat_page.setObjectName("tabPage")
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 18, 0, 0)
        chat_layout.setSpacing(12)
        chat_layout.addWidget(label("Диалог с клиентом", "cardTitle"))
        chat_layout.addWidget(self.chat, 1)
        chat_layout.addWidget(self.chat_line)
        chat_layout.addWidget(send)
        plan_page = QWidget()
        plan_page.setObjectName("tabPage")
        plan_layout = QVBoxLayout(plan_page)
        plan_layout.setContentsMargins(0, 16, 0, 0)
        plan_layout.setSpacing(8)
        plan_layout.addWidget(self.steps, 1)
        plan_layout.addWidget(self.plan_text, 1)
        plan_row = QHBoxLayout()
        plan_row.addWidget(plan)
        plan_row.addWidget(done)
        plan_layout.addLayout(plan_row)
        plan_layout.addWidget(self.problem)
        plan_layout.addWidget(nested)
        plan_layout.addWidget(close)
        tools_page = QWidget()
        tools_page.setObjectName("tabPage")
        tools_layout = QVBoxLayout(tools_page)
        tools_layout.setContentsMargins(0, 18, 0, 0)
        tools_layout.setSpacing(14)
        tools_layout.addWidget(label("Помощник оператора", "cardTitle"))
        for widget in (self.model_note, self.allow_model_input, self.model_button):
            tools_layout.addWidget(widget)
        tools_layout.addStretch()
        tabs = QTabWidget()
        tabs.addTab(queue_page, "Заявки")
        tabs.addTab(chat_page, "Чат")
        tabs.addTab(plan_page, "План")
        tabs.addTab(tools_page, "Модель")
        sidebar, side_layout = card()
        side_layout.addWidget(label("РАБОЧЕЕ ПРОСТРАНСТВО", "eyebrow"))
        side_layout.addWidget(tabs, 1)

        desktop, desktop_layout = card()
        desktop_layout.addWidget(label("УДАЛЁННОЕ УСТРОЙСТВО", "eyebrow"))
        desktop_layout.addWidget(label("Экран клиента", "cardTitle"))
        viewport, viewport_layout = card("viewport", 12, 0)
        viewport_layout.addWidget(self.preview, 1)
        desktop_layout.addWidget(viewport, 1)
        desktop_layout.addWidget(control)
        text_row = QHBoxLayout()
        text_row.addWidget(self.text_input, 1)
        text_row.addWidget(typed)
        desktop_layout.addLayout(text_row)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(sidebar)
        split.addWidget(desktop)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(16)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        split.setSizes([400, 650])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 28)
        layout.setSpacing(16)
        layout.addLayout(header("Панель оператора", lambda: connection_settings(self)))
        layout.addWidget(self.status)
        layout.addWidget(split, 1)
        self.setStyleSheet(STYLE)
        self.resize(1200, 760)
        self.setMinimumSize(850, 580)
        self.restore_preferences(tabs, split)

    def reload_queue(self) -> None:
        if self._closing or self._editing_settings or self._queue_polling:
            return
        self._queue_polling = True
        def done(tickets):
            self._queue_polling = False
            self.service_recovered()
            snapshot = [(str(t.get("ticket_id", "")), str(t.get("description", ""))) for t in tickets]
            if snapshot != self._queue_snapshot:
                sync_rows(self.queue, [(ticket_id, f"{ticket_id} {description[:80]}") for ticket_id, description in snapshot])
                self._queue_snapshot = snapshot
            if not self.ticket_id:
                self.status.setText(f"Заявок в очереди: {len(tickets)}" if tickets else "Очередь пуста")
        def failed(error):
            self._queue_polling = False
            self.report_error(error)
        self.run_background(self.api.list_queued, done, failed)

    def claim(self) -> None:
        if self._claiming:
            return
        item = self.queue.currentItem()
        if item is None:
            self.status.setText("Выберите заявку")
            return
        if self.ticket_id:
            self.status.setText("Сначала завершите текущую заявку")
            return
        ticket_id = item.text().split()[0]
        self._claiming = True
        def done(claimed):
            self._claiming = False
            self.ticket_id = ticket_id
            self.status.setText(f"Заявка {ticket_id} захвачена")
            self._sync_player(claimed["account_id"], claimed["tunnel_id"], "assigned")
        def failed(error):
            self._claiming = False
            self.report_error(error)
        self.run_background(lambda: self.api.claim(ticket_id), done, failed)
    def _sync_player(self, account_id: str, tunnel_id: str, status: str) -> None:
        if status == "resolved" or not tunnel_id:
            self._stop_play()
            return
        running = self.player is not None and self.player.state() != QProcess.NotRunning
        if account_id and should_open_player(self.playing_tunnel, tunnel_id, running, status):
            self._play(account_id, tunnel_id)

    def _stop_play(self) -> None:
        self.play_gen += 1
        self.playing_tunnel = ""
        if self.player is not None:
            player, self.player = self.player, None
            player.finished.connect(player.deleteLater)
            player.kill()
        self._pending_frame = None
        self.frame = None
        self.buffer.clear()
        self.preview.clear()
        if self.bridge is not None:
            self.bridge.close()
            self.bridge = None

    def _play(self, account_id: str, tunnel_id: str) -> None:
        self._stop_play()
        gen = self.play_gen
        self.playing_tunnel = tunnel_id
        self.bridge = LocalRTSPBridge(self.relay_host, self.relay_port, "OPERATOR", account_id)
        url = self.bridge.url(f"/tunnels/{tunnel_id}/screen")
        try:
            args = play_args(url)
        except RuntimeError as exc:
            self._stop_play()
            self.status.setText(str(exc))
            return
        self.buffer.clear()
        self._player_stderr = ""
        self.preview.setText("Жду экран клиента")
        self.player = QProcess(self)
        self.player.readyReadStandardOutput.connect(lambda g=gen: self._read_frame() if g == self.play_gen else None)
        self.player.readyReadStandardError.connect(lambda g=gen: self._read_player_error(g))
        self.player.errorOccurred.connect(lambda _err, g=gen: self._player_finished(g))
        self.player.finished.connect(lambda *_args, g=gen: self._player_finished(g))
        self.player.start(args[0], args[1:])

    def _read_player_error(self, gen):
        if gen == self.play_gen and self.player is not None:
            self._player_stderr = (self._player_stderr + bytes(self.player.readAllStandardError()).decode("utf-8", errors="replace"))[-8192:]

    def _player_finished(self, gen: int) -> None:
        if gen != self.play_gen or self.player is None:
            return
        self._read_player_error(gen)
        err = self._player_stderr.strip()
        self.player.deleteLater()
        self.player = None
        self.play_gen += 1
        self.playing_tunnel = ""
        if self.bridge is not None:
            self.bridge.close()
            self.bridge = None
        if err:
            log.error("FFmpeg player stopped: %s", err)
        self.frame = None
        self._pending_frame = None
        self.preview.clear()
        self.preview.setText("Поток прерван. Повторное подключение...")

    def _read_frame(self) -> None:
        if self.player is None:
            return
        raw = latest_frame(self.buffer, bytes(self.player.readAllStandardOutput()), FRAME_WIDTH * FRAME_HEIGHT * 3)
        if raw is not None:
            self._pending_frame = raw

    def _present_frame(self):
        if self._pending_frame is None:
            return
        raw, self._pending_frame = self._pending_frame, None
        self.frame = QImage(raw, FRAME_WIDTH, FRAME_HEIGHT, QImage.Format_RGB888).copy()
        self._render_frame()

    def _render_frame(self):
        if self.frame is not None:
            pixmap = QPixmap.fromImage(self.frame).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_frame()

    def refresh(self) -> None:
        if self._closing or self._editing_settings or not self.ticket_id or self._polling:
            return
        ticket_id = self.ticket_id
        self._polling = True
        def done(session):
            self._polling = False
            if self.ticket_id == ticket_id:
                self.service_recovered()
                self.session = session
                self._show_session()
        def failed(error):
            self._polling = False
            self.report_error(error)
        self.run_background(lambda: self.api.session(ticket_id), done, failed)

    def _show_session(self) -> None:
        ticket = self.session.get("ticket") or {}
        self._sync_player(ticket.get("account_id") or "", ticket.get("tunnel_id") or "", ticket.get("status") or "")
        self.status.setText(f"{ticket.get('status', '')} · {self.ticket_id}")
        if ticket.get("status") == "resolved":
            self.ticket_id = ""
            self.session = {}
            self.frame = None
            self.preview.clear()
            self.preview.setText("Сеанс завершён")
            self.status.setText("Заявка завершена")
            self.reload_queue()
            return
        lines = [f"{item.get('author')}: {item.get('body')}" for item in self.session.get("messages") or []]
        self.chat.setPlainText("\n".join(lines))
        sync_rows(self.steps, [(str(step.get("id", "")), f"{step.get('status')} {step.get('id')} {step.get('body')}")
                               for step in self.session.get("steps") or []])
    def preview_click(self, event) -> None:
        if not self.ticket_id:
            return
        if self.frame is None or self.preview.pixmap() is None:
            return
        pixmap = self.preview.pixmap()
        left = (self.preview.width() - pixmap.width()) / 2
        top = (self.preview.height() - pixmap.height()) / 2
        x, y = event.pos().x() - left, event.pos().y() - top
        if x < 0 or y < 0 or x >= pixmap.width() or y >= pixmap.height():
            return
        self._send_input({
            "type": "pointer",
            "intent": "adjust_app_setting",
            "pointer": {"x": int(x * FRAME_WIDTH / pixmap.width()), "y": int(y * FRAME_HEIGHT / pixmap.height()), "button": "left", "action": "click"},
        })

    def send_text(self) -> None:
        self._send_input({"type": "text", "intent": "type_in_field", "text": self.text_input.text()})

    def _send_input(self, event: dict) -> None:
        control = self.session.get("control") or {}
        reason = validate_input(event, control, FRAME_WIDTH, FRAME_HEIGHT)
        if reason:
            self._blocked(event.get("intent", ""), reason)
            return
        event["grant_id"] = control.get("grant_id")
        ticket_id = self.ticket_id
        self.run_background(lambda: self.api.post_input(ticket_id, event))

    def _blocked(self, intent: str, detail: str) -> None:
        self.status.setText(detail)
        ticket_id = self.ticket_id
        if ticket_id:
            self.run_background(lambda: self.api.log_blocked(ticket_id, intent or "unknown", detail), failed=self.report_error)
    def send_chat(self) -> None:
        text = self.chat_line.text().strip()
        if not text or not self.ticket_id:
            return
        ticket_id, draft = self.ticket_id, self.chat_line.text()
        self._guarded(lambda captured_id: self.api.message(captured_id, "operator", text),
                      lambda _: self.chat_line.clear() if self.ticket_id == ticket_id and self.chat_line.text() == draft else None)

    def request_control(self) -> None:
        if not intent_allowed("request_control"):
            return
        self._guarded(lambda ticket_id: self.api.request_control(ticket_id, "request_control"))

    def publish_plan(self) -> None:
        steps = [line.strip() for line in self.plan_text.toPlainText().splitlines() if line.strip()]
        for step in steps:
            reason = validate_step(step)
            if reason:
                self._blocked("create_plan", reason)
                return
        self._guarded(lambda ticket_id: self.api.create_plan(ticket_id, steps))

    def complete_step(self) -> None:
        step_id = _selected_id(self.steps)
        if step_id:
            self._guarded(lambda ticket_id: self.api.complete_step(ticket_id, step_id, "отметка оператора"))

    def add_problem(self) -> None:
        step_id = _selected_id(self.steps)
        steps = [line.strip() for line in self.plan_text.toPlainText().splitlines() if line.strip()]
        if not step_id or not steps:
            self.status.setText("Нужны выбранный шаг и строки нового плана")
            return
        description = self.problem.text().strip()
        self._guarded(lambda ticket_id: self.api.add_problem(ticket_id, step_id, description, steps))

    def resolve(self, accept: bool) -> None:
        self._guarded(lambda ticket_id: self.api.resolve(ticket_id, "operator", accept, "Предлагаю закрыть заявку"))

    def prepare_models(self):
        self.model_note.setText("Подготовка моделей...")
        def ready(models):
            self.models = models
            self.model_button.setEnabled(True)
            self.allow_model_input.setEnabled(True)
            self.model_note.setText("Профиль {profile}: текст {text_repo}, кадр {vision_repo}, решение {decision_repo}. {reason}".format(**models))
        def failed(error):
            self.model_note.setText(str(error))
        ModelProcess(self, "prepare", {}, ready, failed)

    def model_step(self) -> None:
        if not self.ticket_id or self.models is None:
            return
        context = {
            "description": (self.session.get("ticket") or {}).get("description", ""),
            "status": (self.session.get("ticket") or {}).get("status", ""),
            "messages": self.session.get("messages") or [],
            "steps": self.session.get("steps") or [],
            "control": self.session.get("control") or {},
        }

        png = self._png()
        models = self.models
        ticket_id = self.ticket_id
        self.model_button.setEnabled(False)
        self.status.setText("Модель обрабатывает заявку...")
        def done(action):
            self.model_button.setEnabled(True)
            if self.ticket_id == ticket_id:
                self._apply_model(action)
        def failed(error):
            self.model_button.setEnabled(True)
            self.status.setText(f"Модель недоступна: {error}")
        ModelProcess(self, "action", {"models": models, "context": context,
                                      "frame": base64.b64encode(png).decode("ascii") if png else None}, done, failed)

    def _apply_model(self, action: dict) -> None:
        kind = action.get("action")
        intent = str(action.get("intent") or "")
        if not intent_allowed(intent):
            self._blocked(intent, "модель предложила запрещённое намерение")
            return
        if kind == "chat":
            self._guarded(lambda ticket_id: self.api.message(ticket_id, "operator", str(action.get("text") or "")))
        elif kind == "request_control":
            self._guarded(lambda ticket_id: self.api.request_control(ticket_id, intent))
        elif kind == "plan":
            steps = [str(step) for step in action.get("steps") or []]
            for step in steps:
                reason = validate_step(step)
                if reason:
                    self._blocked(intent, reason)
                    return
            self._guarded(lambda ticket_id: self.api.create_plan(ticket_id, steps))
        elif kind == "complete_step":
            step_id = str(action.get("step_id") or "")
            if step_id:
                self._guarded(lambda ticket_id: self.api.complete_step(ticket_id, step_id, "модель видит шаг на экране"))
        elif kind == "problem":
            self._guarded(lambda ticket_id: self.api.add_problem(
                ticket_id,
                str(action.get("parent_step_id") or ""),
                str(action.get("text") or ""),
                [str(step) for step in action.get("steps") or []],
            ))
        elif kind == "propose_close":
            self.resolve(True)
        elif kind == "input":
            if not self.allow_model_input.isChecked():
                self.status.setText("Модель предложила ввод. Переключатель выключен, событие не отправлено.")
                return
            event = action.get("input") or {}
            event["intent"] = intent
            self._send_input(event)
        else:
            self.status.setText("Модель ждёт")

    def _png(self) -> bytes | None:
        if self.frame is None:
            return None
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        self.frame.save(buffer, "PNG")
        return bytes(buffer.data())

    def _guarded(self, call, done=None) -> None:
        ticket_id = self.ticket_id
        if ticket_id:
            def execute():
                if self.ticket_id == ticket_id:
                    return call(ticket_id)
            self.run_background(execute, done)
    def closeEvent(self, event) -> None:
        self.close_background()
        for job in list(self._model_jobs):
            job.kill()
        self._model_jobs.clear()
        self.timer.stop()
        self.queue_timer.stop()
        self.render_timer.stop()
        self._stop_play()
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
