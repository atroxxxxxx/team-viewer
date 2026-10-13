import uuid
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QProcess, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from operator_app.agent import next_action
from support_protocol.api import TicketAPI
from support_protocol.endpoints import config_file, load_endpoints
from support_protocol.media import FRAME_HEIGHT, FRAME_WIDTH, play_args, should_open_player
from support_protocol.policy import intent_allowed, validate_input, validate_step
from support_protocol.tunnel import LocalRTSPBridge

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state" / "operator_id"


class OperatorWindow(QWidget):
    def __init__(self, models: dict | None = None):
        super().__init__()
        self.models = models
        self.setWindowTitle("Оператор техподдержки")
        api_base, relay_host, relay_port, username, password, tls_verify = load_endpoints(
            config_file(ROOT / "config.yaml", "SUPPORT_OPERATOR_CONFIG")
        )
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
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.reload_queue()

    def _build(self) -> None:
        self.queue = QListWidget()
        self.status = QLabel("Очередь пуста")
        self.preview = QLabel("Экран появится после захвата")
        self.preview.setFixedSize(FRAME_WIDTH, FRAME_HEIGHT)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.mousePressEvent = self.preview_click
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat_line = QLineEdit()
        self.steps = QListWidget()
        self.plan_text = QPlainTextEdit()
        self.plan_text.setPlaceholderText("По одному шагу на строку")
        self.problem = QLineEdit()
        self.problem.setPlaceholderText("Что сломал этот шаг")
        self.allow_model_input = QCheckBox("Модель может предлагать ввод")
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
        layout = QVBoxLayout(self)
        layout.addWidget(refresh)
        layout.addWidget(self.queue)
        layout.addWidget(claim)
        layout.addWidget(self.status)
        layout.addWidget(self.preview)
        layout.addWidget(self.chat)
        layout.addWidget(self.chat_line)
        row = QHBoxLayout()
        row.addWidget(send)
        row.addWidget(control)
        layout.addLayout(row)
        layout.addWidget(self.steps)
        layout.addWidget(self.plan_text)
        layout.addWidget(plan)
        layout.addWidget(done)
        layout.addWidget(self.problem)
        layout.addWidget(nested)
        layout.addWidget(self.text_input)
        layout.addWidget(typed)
        layout.addWidget(self.model_note)
        layout.addWidget(self.allow_model_input)
        layout.addWidget(self.model_button)
        layout.addWidget(close)
        self.resize(720, 980)

    def reload_queue(self) -> None:
        self.queue.clear()
        try:
            tickets = self.api.list_queued()
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        for ticket in tickets:
            self.queue.addItem(f"{ticket.get('ticket_id')} {ticket.get('description', '')[:80]}")
        if not tickets:
            self.status.setText("Очередь пуста")

    def claim(self) -> None:
        item = self.queue.currentItem()
        if item is None:
            return
        ticket_id = item.text().split()[0]
        try:
            claimed = self.api.claim(ticket_id)
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        self.ticket_id = ticket_id
        self.status.setText(f"Заявка {ticket_id} захвачена")
        self._sync_player(claimed["account_id"], claimed["tunnel_id"], "assigned")

    def _sync_player(self, account_id: str, tunnel_id: str, status: str) -> None:
        if status == "resolved" or not tunnel_id:
            self._stop_play()
            return
        running = self.player is not None and self.player.state() != QProcess.ProcessState.NotRunning
        if account_id and should_open_player(self.playing_tunnel, tunnel_id, running, status):
            self._play(account_id, tunnel_id)

    def _stop_play(self) -> None:
        if self.player is None and self.bridge is None and not self.playing_tunnel:
            return
        self.play_gen += 1
        self.playing_tunnel = ""
        if self.player is not None:
            self.player.kill()
            self.player = None
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
        self.preview.setText("Жду экран клиента")
        self.player = QProcess(self)
        self.player.readyReadStandardOutput.connect(self._read_frame)
        self.player.finished.connect(lambda *_args, g=gen: self._player_finished(g))
        self.player.start(args[0], args[1:])

    def _player_finished(self, gen: int) -> None:
        if gen != self.play_gen or self.player is None:
            return
        err = bytes(self.player.readAllStandardError()).decode("utf-8", errors="replace").strip()
        self.player = None
        self.playing_tunnel = ""
        if self.bridge is not None:
            self.bridge.close()
            self.bridge = None
        if err:
            self.preview.setText(err.splitlines()[-1][:180])

    def _read_frame(self) -> None:
        if self.player is None:
            return
        self.buffer += bytes(self.player.readAllStandardOutput())
        frame_size = FRAME_WIDTH * FRAME_HEIGHT * 3
        while len(self.buffer) >= frame_size:
            raw = bytes(self.buffer[:frame_size])
            del self.buffer[:frame_size]
            image = QImage(raw, FRAME_WIDTH, FRAME_HEIGHT, QImage.Format.Format_RGB888).copy()
            self.frame = image
            self.preview.setPixmap(QPixmap.fromImage(image))

    def refresh(self) -> None:
        if not self.ticket_id:
            return
        try:
            self.session = self.api.session(self.ticket_id)
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        ticket = self.session.get("ticket") or {}
        self._sync_player(ticket.get("account_id") or "", ticket.get("tunnel_id") or "", ticket.get("status") or "")
        self.status.setText(f"{ticket.get('status', '')} · {self.ticket_id}")
        lines = [f"{item.get('author')}: {item.get('body')}" for item in self.session.get("messages") or []]
        self.chat.setPlainText("\n".join(lines))
        self.steps.clear()
        for step in self.session.get("steps") or []:
            self.steps.addItem(f"{step.get('status')} {step.get('id')} {step.get('body')}")

    def preview_click(self, event) -> None:
        if not self.ticket_id:
            return
        self._send_input({
            "type": "pointer",
            "intent": "adjust_app_setting",
            "pointer": {"x": int(event.position().x()), "y": int(event.position().y()), "button": "left", "action": "click"},
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
        try:
            self.api.post_input(self.ticket_id, event)
        except RuntimeError as exc:
            self.status.setText(str(exc))

    def _blocked(self, intent: str, detail: str) -> None:
        self.status.setText(detail)
        if self.ticket_id:
            try:
                self.api.log_blocked(self.ticket_id, intent or "unknown", detail)
            except RuntimeError:
                pass

    def send_chat(self) -> None:
        text = self.chat_line.text().strip()
        if not text or not self.ticket_id:
            return
        self._guarded(lambda: self.api.message(self.ticket_id, "operator", text))
        self.chat_line.clear()

    def request_control(self) -> None:
        if not intent_allowed("request_control"):
            return
        self._guarded(lambda: self.api.request_control(self.ticket_id, "request_control"))

    def publish_plan(self) -> None:
        steps = [line.strip() for line in self.plan_text.toPlainText().splitlines() if line.strip()]
        for step in steps:
            reason = validate_step(step)
            if reason:
                self._blocked("create_plan", reason)
                return
        self._guarded(lambda: self.api.create_plan(self.ticket_id, steps))

    def complete_step(self) -> None:
        step_id = _selected_id(self.steps)
        if step_id:
            self._guarded(lambda: self.api.complete_step(self.ticket_id, step_id, "отметка оператора"))

    def add_problem(self) -> None:
        step_id = _selected_id(self.steps)
        steps = [line.strip() for line in self.plan_text.toPlainText().splitlines() if line.strip()]
        if not step_id or not steps:
            self.status.setText("Нужны выбранный шаг и строки нового плана")
            return
        description = self.problem.text().strip()
        self._guarded(lambda: self.api.add_problem(self.ticket_id, step_id, description, steps))

    def resolve(self, accept: bool) -> None:
        self._guarded(lambda: self.api.resolve(self.ticket_id, "operator", accept, "Предлагаю закрыть заявку"))

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

        def progress(text: str) -> None:
            self.status.setText(text)
            QApplication.processEvents()

        try:
            action = next_action(self.models, context, self._png(), progress)
        except Exception as exc:
            self.status.setText(f"Модель недоступна: {exc}")
            return
        self._apply_model(action)

    def _apply_model(self, action: dict) -> None:
        kind = action.get("action")
        intent = str(action.get("intent") or "")
        if not intent_allowed(intent):
            self._blocked(intent, "модель предложила запрещённое намерение")
            return
        if kind == "chat":
            self._guarded(lambda: self.api.message(self.ticket_id, "operator", str(action.get("text") or "")))
        elif kind == "request_control":
            self._guarded(lambda: self.api.request_control(self.ticket_id, intent))
        elif kind == "plan":
            steps = [str(step) for step in action.get("steps") or []]
            for step in steps:
                reason = validate_step(step)
                if reason:
                    self._blocked(intent, reason)
                    return
            self._guarded(lambda: self.api.create_plan(self.ticket_id, steps))
        elif kind == "complete_step":
            step_id = str(action.get("step_id") or "")
            if step_id:
                self._guarded(lambda: self.api.complete_step(self.ticket_id, step_id, "модель видит шаг на экране"))
        elif kind == "problem":
            self._guarded(lambda: self.api.add_problem(
                self.ticket_id,
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
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.frame.save(buffer, "PNG")
        return bytes(buffer.data())

    def _guarded(self, call) -> None:
        if not self.ticket_id:
            return
        try:
            call()
        except RuntimeError as exc:
            self.status.setText(str(exc))

    def closeEvent(self, event) -> None:
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
