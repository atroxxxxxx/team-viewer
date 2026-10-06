import uuid
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QProcess, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
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
from support_protocol.api import TicketAPI, media_url
from support_protocol.endpoints import load_endpoints
from support_protocol.media import FRAME_HEIGHT, FRAME_WIDTH, play_args
from support_protocol.policy import intent_allowed, validate_input, validate_step

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state" / "operator_id"


class OperatorWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Оператор техподдержки")
        api_base, rtsp_base, username, password, tls_verify = load_endpoints(ROOT / "config.yaml")
        self.api = TicketAPI(api_base, username, password, verify=tls_verify)
        self.operator_id = _load_id(STATE)
        self.ticket_id = ""
        self.reader_token = ""
        self.rtsp_base = rtsp_base
        self.screen_path = ""
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
        self.endpoint = QLineEdit("http://127.0.0.1:11434")
        self.planner = QLineEdit("qwen2.5:7b")
        self.vision = QLineEdit("qwen2.5vl:7b")
        self.allow_model_input = QCheckBox("Модель может предлагать ввод")
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
        model = QPushButton("Шаг модели")
        model.clicked.connect(self.model_step)
        typed = QPushButton("Отправить текст")
        typed.clicked.connect(self.send_text)
        form = QFormLayout()
        form.addRow("Ollama", self.endpoint)
        form.addRow("План", self.planner)
        form.addRow("Кадр", self.vision)
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
        layout.addLayout(form)
        layout.addWidget(self.allow_model_input)
        layout.addWidget(model)
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
            claimed = self.api.claim(ticket_id, self.operator_id)
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        self.ticket_id = ticket_id
        self.reader_token = claimed["reader_token"]
        paths = claimed.get("paths") or []
        self.screen_path = paths[0] if paths else ""
        self.status.setText(f"Заявка {ticket_id} захвачена")
        self._play()

    def _play(self) -> None:
        if self.player is not None:
            self.player.terminate()
        url = media_url(self.rtsp_base, self.screen_path, self.reader_token)
        try:
            args = play_args(url)
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        self.buffer.clear()
        self.player = QProcess(self)
        self.player.readyReadStandardOutput.connect(self._read_frame)
        self.player.start(args[0], args[1:])

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
        self.status.setText(f"{ticket.get('status', '')} · {self.ticket_id}")
        lines = [f"{item.get('author')}: {item.get('body')}" for item in self.session.get("messages") or []]
        self.chat.setPlainText("\n".join(lines))
        self.steps.clear()
        for step in self.session.get("steps") or []:
            self.steps.addItem(f"{step.get('status')} {step.get('id')} {step.get('body')}")
        if ticket.get("status") == "resolved" and self.player is not None:
            self.player.terminate()
            self.player = None

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
        if not self.ticket_id:
            return
        context = {
            "description": (self.session.get("ticket") or {}).get("description", ""),
            "status": (self.session.get("ticket") or {}).get("status", ""),
            "messages": self.session.get("messages") or [],
            "steps": self.session.get("steps") or [],
            "control": self.session.get("control") or {},
        }
        try:
            action = next_action(self.endpoint.text(), self.planner.text(), self.vision.text(), context, self._png())
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
        if self.player is not None:
            self.player.terminate()
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
