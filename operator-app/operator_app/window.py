import time
from collections import deque
import base64
import html
import logging
from pathlib import Path
from dataclasses import dataclass

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
    QWidget, QTabWidget, QSplitter, QScrollArea,
)

from operator_app.model_process import ModelProcess
from support_protocol.ui import Background, STYLE, connection_settings, card, header, label, secondary, StatusLabel, CheckBox, DesktopPreview, sync_chat, sync_rows
from support_protocol.settings import UserSettings
from support_protocol.api import TicketAPI
from support_protocol.endpoints import config_file, load_endpoints
from support_protocol.labels import step_line, ticket_status
from support_protocol.media import FRAME_HEIGHT, FRAME_WIDTH, latest_frame, play_args, session_ended, should_open_player
from support_protocol.policy import intent_allowed, validate_input, validate_step
from support_protocol.tunnel import LocalRTSPBridge

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Assignment:
    ticket_id: str
    api: TicketAPI



class OperatorWindow(QWidget, Background):
    def __init__(self, models: dict | None = None):
        super().__init__()
        self.models = models
        self._model_jobs = set()
        self.setWindowTitle("Оператор техподдержки")
        self.settings = UserSettings("operator")
        api_base, relay_host, relay_port, username, password, tls_verify = self.settings.endpoints(load_endpoints(
            config_file(ROOT / "config.yaml", "SUPPORT_OPERATOR_CONFIG"), password_env="OPERATOR_PASSWORD"
        ))
        self.api = TicketAPI(api_base, username, password, verify=tls_verify)
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.assignment: Assignment | None = None
        self.ticket_id = ""
        self.playing_tunnel = ""
        self.play_gen = 0
        self.bridge: LocalRTSPBridge | None = None
        self.media_secret = ""
        self.session: dict = {}
        self.player: QProcess | None = None
        self.buffer = bytearray()
        self.frame: QImage | None = None
        self._pending_frame = None
        self._frame_times = deque(maxlen=120)
        self._player_stderr = ""
        self.init_background()
        self._polling = False
        self._claiming = False
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.timeout.connect(self._update_video_rate)
        self.timer.start(1000)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(0)
        self.render_timer.setTimerType(Qt.PreciseTimer)
        self.render_timer.timeout.connect(self._present_frame)
        self.queue_timer = QTimer(self)
        self.queue_timer.setInterval(3000)
        self.queue_timer.timeout.connect(self.reload_queue)
        self.queue_timer.start()
        self._queue_polling = False
        self._queue_snapshot = None
        self._status_until = 0.0
        self._releasing = False
        self.reload_queue()

    def _build(self) -> None:
        self.queue = QListWidget()
        self.queue.setWordWrap(True)
        self.queue.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.status = StatusLabel("Очередь пуста")
        self.preview = DesktopPreview("Экран появится после захвата")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.mousePressEvent = self.preview_click
        self.preview.framePainted.connect(self._record_video_frame)
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
        self.text_input.setPlaceholderText("Текст для ввода")
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
        self.steps.setMinimumHeight(100)
        self.plan_text.setMinimumHeight(100)
        plan_layout.addWidget(self.steps, 1)
        plan_layout.addWidget(self.plan_text, 1)
        plan_layout.addWidget(plan)
        plan_layout.addWidget(done)
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
        plan_scroll = QScrollArea()
        plan_scroll.setWidgetResizable(True)
        plan_scroll.setFrameShape(QScrollArea.NoFrame)
        plan_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        plan_scroll.setWidget(plan_page)
        tabs.addTab(plan_scroll, "План")
        tabs.addTab(tools_page, "Модель")
        sidebar, side_layout = card()
        side_layout.addWidget(label("РАБОЧЕЕ ПРОСТРАНСТВО", "eyebrow"))
        side_layout.addWidget(tabs, 1)

        desktop, desktop_layout = card()
        desktop_layout.addWidget(label("УДАЛЁННОЕ УСТРОЙСТВО", "eyebrow"))
        video_heading = QHBoxLayout()
        video_heading.addWidget(label("Экран клиента", "cardTitle"), 1)
        self.video_rate = label("— кадр/с", "muted")
        video_heading.addWidget(self.video_rate)
        desktop_layout.addLayout(video_heading)
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
        self.claim_button, self.chat_button, self.control_button, self.type_button = claim, send, control, typed
        self.plan_actions = (plan, done, nested, close)
        for field in (self.chat_line, self.text_input, self.problem, self.plan_text):
            field.textChanged.connect(self._update_actions)
        self.queue.currentItemChanged.connect(self._update_actions)
        self.steps.currentItemChanged.connect(self._update_actions)
        self._update_actions()

    def _update_actions(self):
        active = bool(self.ticket_id)
        grant = self.session.get("control") or {}
        can_type = grant.get("status") == "active" and "text" in (grant.get("scope") or "").split(",")
        pending = self.session.get("control_request") or grant
        self.claim_button.setEnabled(not active and not self._claiming and self.queue.currentItem() is not None)
        self.chat_button.setEnabled(active and bool(self.chat_line.text().strip()))
        self.control_button.setEnabled(active and pending.get("status") not in {"pending", "active"})
        self.type_button.setEnabled(active and can_type and bool(self.text_input.text().strip()))
        plan, done, nested, close = self.plan_actions
        has_draft = bool(self.plan_text.toPlainText().strip())
        has_step = self.steps.currentItem() is not None
        plan.setEnabled(active and has_draft)
        done.setEnabled(active and has_step)
        nested.setEnabled(active and has_step and has_draft and bool(self.problem.text().strip()))
        close.setEnabled(active)
        self.model_button.setEnabled(active and self.models is not None and not self._model_jobs)

    def reload_queue(self) -> None:
        if self._closing or self._editing_settings or self._queue_polling:
            return
        self._queue_polling = True
        def done(tickets):
            self._queue_polling = False
            self.service_recovered()
            snapshot = [
                (str(t.get("ticket_id", "")), str(t.get("description", "")), str(t.get("handoff") or ""))
                for t in tickets
            ]
            if snapshot != self._queue_snapshot:
                rows = []
                for ticket_id, description, handoff in snapshot:
                    summary = " ".join(description.split()) or "Без описания"
                    if len(summary) > 100:
                        summary = summary[:99].rstrip() + "…"
                    rows.append((ticket_id, f"{summary}\n{handoff}" if handoff else summary))
                sync_rows(self.queue, rows)
                for index, (ticket_id, description, handoff) in enumerate(snapshot):
                    detail = (html.escape(value) for value in (description, handoff, ticket_id) if value)
                    self.queue.item(index).setToolTip("<qt>" + "<br>".join(detail) + "</qt>")
                self._queue_snapshot = snapshot
            if not self.ticket_id and time.monotonic() >= self._status_until:
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
            self._note("Выберите заявку")
            return
        if self.ticket_id:
            self._note("Сначала завершите текущую заявку")
            return
        ticket_id = str(item.data(Qt.UserRole) or "")
        if not ticket_id:
            return
        self._claiming = True
        self._update_actions()
        def done(claimed):
            self._claiming = False
            self.ticket_id = ticket_id
            self.assignment = Assignment(ticket_id, self.api.for_assignment(claimed["tunnel_id"]))
            self._note(f"Заявка {ticket_id} захвачена")
            self.media_secret = str(claimed.get("media_secret") or "")
            self._update_actions()
            self._sync_player(claimed["account_id"], claimed["tunnel_id"], "assigned")
        def failed(error):
            self._claiming = False
            self._update_actions()
            self.report_error(error)
        self.run_background(lambda: self.api.claim(ticket_id), done, failed)
    def _sync_player(self, account_id: str, tunnel_id: str, status: str) -> None:
        if session_ended(status) or not tunnel_id:
            self._stop_play()
            return
        running = self.player is not None and self.player.state() != QProcess.NotRunning
        if account_id and len(self.media_secret) == 64 and should_open_player(self.playing_tunnel, tunnel_id, running, status):
            self._play(account_id, tunnel_id)

    def _stop_play(self) -> None:
        self.play_gen += 1
        self.playing_tunnel = ""
        if self.player is not None:
            player, self.player = self.player, None
            player.finished.connect(player.deleteLater)
            player.kill()
        self._pending_frame = None
        self.render_timer.stop()
        self._frame_times.clear()
        self.video_rate.setText("— кадр/с")
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
        self.bridge = LocalRTSPBridge(self.relay_host, self.relay_port, "OPERATOR", tunnel_id, self.media_secret)
        url = self.bridge.url(f"/tunnels/{tunnel_id}/screen")
        try:
            args = play_args(url)
        except RuntimeError as exc:
            self._stop_play()
            self._note(str(exc))
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
        self.render_timer.stop()
        self._frame_times.clear()
        self.video_rate.setText("— кадр/с")
        self.preview.clear()
        self.preview.setText("Поток прерван. Повторное подключение...")

    def _read_frame(self) -> None:
        if self.player is None:
            return
        raw = latest_frame(self.buffer, bytes(self.player.readAllStandardOutput()), FRAME_WIDTH * FRAME_HEIGHT * 3)
        if raw is not None:
            self._pending_frame = raw
            # Один отложенный показ на пакет кадров, без второго FPS-ограничителя.
            if not self.render_timer.isActive():
                self.render_timer.start()

    def _present_frame(self):
        self.render_timer.stop()
        if self._pending_frame is None:
            return
        raw, self._pending_frame = self._pending_frame, None
        self.frame = QImage(raw, FRAME_WIDTH, FRAME_HEIGHT, QImage.Format_RGB888).copy()
        self._render_frame()

    def _render_frame(self):
        if self.frame is not None:
            pixmap = QPixmap.fromImage(self.frame).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview.frame_id = self.frame.cacheKey()
            self.preview.setPixmap(pixmap)

    def _record_video_frame(self):
        self._frame_times.append(time.monotonic())

    def _update_video_rate(self):
        if len(self._frame_times) < 2 or time.monotonic() - self._frame_times[-1] > 2:
            self.video_rate.setText("— кадр/с")
            return
        elapsed = self._frame_times[-1] - self._frame_times[0]
        fps = (len(self._frame_times) - 1) / elapsed if elapsed > 0 else 0
        self.video_rate.setText(f"{fps:.0f} кадр/с")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_frame()

    def refresh(self) -> None:
        if self._closing or self._editing_settings or not self.ticket_id or self._polling:
            return
        assignment = self._capture_assignment()
        self._polling = True
        def done(session):
            self._polling = False
            if self._is_current(assignment):
                self.service_recovered()
                self.session = session
                self._show_session()
        def failed(error):
            self._polling = False
            if self._is_current(assignment):
                self.report_error(error)
        self.run_background(lambda: assignment.api.session(assignment.ticket_id), done, failed)

    def _show_session(self) -> None:
        ticket = self.session.get("ticket") or {}
        status = ticket.get("status") or ""
        if (status == "queued" and self.ticket_id) or self.session.get("owned") is False:
            self._finish_ticket(returned=True)
            return
        if session_ended(status):
            self._finish_ticket()
            return
        self.media_secret = str(self.session.get("media_secret") or "")
        self._sync_player(ticket.get("account_id") or "", ticket.get("tunnel_id") or "", status)
        if time.monotonic() >= self._status_until:
            notice = str((self.session.get("handoff") or {}).get("operator") or "")
            self.status.setText(notice or ticket_status(status, self.ticket_id))
        sync_chat(self.chat, self.session.get("messages") or [])
        sync_rows(self.steps, [(str(step.get("id", "")), step_line(step)) for step in self.session.get("steps") or []])
        self._update_actions()

    def _finish_ticket(self, *, returned=False):
        self._stop_play()
        self.ticket_id = ""
        self.assignment = None
        self.media_secret = ""
        self.session = {}
        if returned:
            self.preview.setText("Экран появится после захвата")
            self._note("Заявка вернулась в очередь")
        else:
            self.preview.setText("Сеанс завершён")
            self._status_until = 0.0
            self.status.setText("Заявка завершена")
        self._update_actions()
        self.reload_queue()

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
        self._guarded(lambda api, ticket_id: api.post_input(ticket_id, event))

    def _blocked(self, intent: str, detail: str) -> None:
        self._note(detail)
        self._guarded(lambda api, ticket_id: api.log_blocked(ticket_id, intent or "unknown", detail))
    def send_chat(self) -> None:
        text = self.chat_line.text().strip()
        if not text or not self.ticket_id:
            return
        ticket_id, draft = self.ticket_id, self.chat_line.text()
        self._guarded(lambda api, captured_id: api.message(captured_id, "operator", text),
                      lambda _: self.chat_line.clear() if self.ticket_id == ticket_id and self.chat_line.text() == draft else None)

    def request_control(self) -> None:
        if not intent_allowed("request_control"):
            return
        self._guarded(lambda api, ticket_id: api.request_control(ticket_id, "request_control"))

    def publish_plan(self) -> None:
        steps = [line.strip() for line in self.plan_text.toPlainText().splitlines() if line.strip()]
        for step in steps:
            reason = validate_step(step)
            if reason:
                self._blocked("create_plan", reason)
                return
        self._guarded(lambda api, ticket_id: api.create_plan(ticket_id, steps))

    def complete_step(self) -> None:
        step_id = _selected_id(self.steps)
        if step_id:
            self._guarded(lambda api, ticket_id: api.complete_step(ticket_id, step_id, "отметка оператора"))

    def add_problem(self) -> None:
        step_id = _selected_id(self.steps)
        steps = [line.strip() for line in self.plan_text.toPlainText().splitlines() if line.strip()]
        if not step_id or not steps:
            self._note("Нужны выбранный шаг и строки нового плана")
            return
        description = self.problem.text().strip()
        self._guarded(lambda api, ticket_id: api.add_problem(ticket_id, step_id, description, steps))

    def resolve(self, accept: bool) -> None:
        self._guarded(lambda api, ticket_id: api.resolve(ticket_id, "operator", accept, "Предлагаю закрыть заявку"))

    def prepare_models(self):
        self.model_note.setText("Подготовка моделей...")
        def ready(models):
            self.models = models
            self._update_actions()
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
        assignment = self._capture_assignment()
        self.model_button.setEnabled(False)
        self._note("Модель обрабатывает заявку...")
        def done(action):
            self._update_actions()
            if self._is_current(assignment):
                self._apply_model(action)
        def failed(error):
            self._update_actions()
            self._note(f"Модель недоступна: {error}")
        ModelProcess(self, "action", {"models": models, "context": context,
                                      "frame": base64.b64encode(png).decode("ascii") if png else None}, done, failed)

    def _apply_model(self, action: dict) -> None:
        kind = action.get("action")
        intent = str(action.get("intent") or "")
        if not intent_allowed(intent):
            self._blocked(intent, "модель предложила запрещённое намерение")
            return
        if kind == "chat":
            self._guarded(lambda api, ticket_id: api.message(ticket_id, "operator", str(action.get("text") or "")))
        elif kind == "request_control":
            self._guarded(lambda api, ticket_id: api.request_control(ticket_id, intent))
        elif kind == "plan":
            steps = [str(step) for step in action.get("steps") or []]
            for step in steps:
                reason = validate_step(step)
                if reason:
                    self._blocked(intent, reason)
                    return
            self._guarded(lambda api, ticket_id: api.create_plan(ticket_id, steps))
        elif kind == "complete_step":
            step_id = str(action.get("step_id") or "")
            if step_id:
                self._guarded(lambda api, ticket_id: api.complete_step(ticket_id, step_id, "модель видит шаг на экране"))
        elif kind == "problem":
            self._guarded(lambda api, ticket_id: api.add_problem(
                ticket_id,
                str(action.get("parent_step_id") or ""),
                str(action.get("text") or ""),
                [str(step) for step in action.get("steps") or []],
            ))
        elif kind == "propose_close":
            self.resolve(True)
        elif kind == "input":
            if not self.allow_model_input.isChecked():
                self._note("Модель предложила ввод. Переключатель выключен, событие не отправлено.")
                return
            event = action.get("input") or {}
            event["intent"] = intent
            self._send_input(event)
        else:
            self._note("Модель ждёт")

    def _png(self) -> bytes | None:
        if self.frame is None:
            return None
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        self.frame.save(buffer, "PNG")
        return bytes(buffer.data())

    def _capture_assignment(self):
        return self.assignment or Assignment(self.ticket_id, self.api)

    def _is_current(self, assignment):
        return bool(self.ticket_id) and self._capture_assignment() == assignment

    def _guarded(self, call, done=None) -> None:
        if not self.ticket_id:
            return
        assignment = self._capture_assignment()

        def completed(result):
            if done is not None and self._is_current(assignment):
                done(result)

        def failed(error):
            if self._is_current(assignment):
                self.report_error(error)

        def execute():
            if self._is_current(assignment):
                return call(assignment.api, assignment.ticket_id)

        self.run_background(execute, completed, failed)

    def _note(self, text: str) -> None:
        self._status_until = time.monotonic() + 5
        self.status.setText(text)

    def report_error(self, error) -> None:
        super().report_error(error)
        self._status_until = time.monotonic() + 5

    def service_recovered(self) -> None:
        failed = self._service_failed
        super().service_recovered()
        if failed:
            self._status_until = time.monotonic() + 5

    def closeEvent(self, event) -> None:
        if self._releasing:
            event.ignore()
            return
        assignment = self._capture_assignment()
        self.ticket_id = ""
        self.assignment = None
        for job in list(self._model_jobs):
            job.kill()
        self._model_jobs.clear()
        self.timer.stop()
        self.queue_timer.stop()
        self._stop_play()
        if not assignment.ticket_id:
            self.close_background()
            super().closeEvent(event)
            return
        event.ignore()
        self._releasing = True
        self.setEnabled(False)
        self._note("Возвращаю заявку в очередь…")

        def finish(_):
            self._releasing = False
            self.close()

        def failed(error):
            log.warning("Could not release ticket on close", exc_info=(type(error), error, error.__traceback__))
            finish(None)

        self.run_background(lambda: assignment.api.release(assignment.ticket_id), finish, failed)


def _selected_id(widget: QListWidget) -> str:
    item = widget.currentItem()
    if item is None:
        return ""
    return str(item.data(Qt.UserRole) or "")
