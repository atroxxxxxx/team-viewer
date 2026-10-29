"""Общие оформление и фоновые задачи настольных приложений."""
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

STYLE = """
QWidget { background: #0b101a; color: #e8edf5; font-family: "Segoe UI", "Arial"; font-size: 13px; }
QLabel { background: transparent; }
QLabel#brand { color: #f7f9fd; font-size: 17px; font-weight: 700; }
QLabel#eyebrow { color: #99abc5; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
QLabel#heading { color: #f4f7fc; font-size: 24px; font-weight: 700; }
QLabel#cardTitle { color: #edf2fa; font-size: 16px; font-weight: 700; }
QLabel#muted { color: #90a0b4; font-size: 12px; }
QLabel#identity { color: #e0eaff; font-size: 12px; font-weight: 600; }
QLabel#status { color: #a6c9ff; background: #172b43; border: 1px solid #274c70; border-radius: 9px; padding: 10px 13px; }
QLabel#status[state="error"] { color: #ffb9b9; background: #3b2027; border: 1px solid #773b45; }
QLabel#status[state="active"] { color: #9be1c7; background: #17362f; border: 1px solid #2d6a57; }
QWidget#tabPage, QStackedWidget { background: #161f2e; }
QFrame#card { background: #161f2e; border: 1px solid #2b3a50; border-radius: 16px; }
QFrame#inset { background: #101927; border: 1px solid #30435e; border-radius: 12px; }
QFrame#accent { background: #6599f7; border-radius: 2px; }
QFrame#divider { background: #283548; max-height: 1px; }
QFrame#viewport { background: #0a111d; border: 1px solid #30415a; border-radius: 12px; }
QLineEdit, QPlainTextEdit, QListWidget, QSpinBox { background: #0e1621; color: #e8eef8; border: 1px solid #344357; border-radius: 9px; padding: 10px 12px; selection-background-color: #365d9e; }
QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QSpinBox:focus { border: 1px solid #6599f7; }
QLineEdit:disabled, QPlainTextEdit:disabled { color: #68798d; background: #141b25; }
QListWidget::item { padding: 10px; border-radius: 6px; }
QListWidget::item:selected { background: #263f62; color: #ffffff; }
QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #497de5, stop:1 #648af0); color: #ffffff; border: 1px solid #588bec; border-radius: 10px; padding: 10px 16px; font-weight: 600; min-height: 17px; }
QPushButton:hover { background: #7299f6; border-color: #7ba8fa; }
QPushButton:pressed { background: #316acb; }
QPushButton:disabled { background: #293548; color: #78889b; border-color: #293548; }
QPushButton#secondary { background: #202c3c; color: #d7e2f1; border: 1px solid #34465c; }
QPushButton#secondary:hover { background: #2c3d53; border-color: #506986; }
QPushButton#secondary:pressed { background: #182332; }
QPushButton#secondary:disabled { color: #6d7b8b; background: #1c2633; border-color: #293548; }
QCheckBox { color: #c5d1e0; spacing: 9px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #52657c; border-radius: 4px; background: #101823; }
QCheckBox::indicator:checked { background: #477fe4; border-color: #80abff; }
QTabWidget::pane { border: 0; background: transparent; top: -1px; }
QTabBar::tab { color: #9baac0; background: #101927; padding: 10px 15px; margin-right: 5px; border: 1px solid #29394f; border-radius: 9px; }
QTabBar::tab:hover { color: #e3edfb; background: #202f45; }
QTabBar::tab:selected { color: #f2f6ff; background: #2c4268; border: 1px solid #4d70a9; }
QSplitter::handle { background: transparent; width: 12px; }
QScrollBar:vertical { background: #111a26; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #3c5068; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QDialog { background: #151d29; }
"""


from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QPainter, QPen, QLinearGradient
from PyQt5.QtWidgets import QLabel, QWidget, QCheckBox, QStyle, QStyleOptionButton


class CheckBox(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(rect.left() + 4, rect.center().y(), rect.left() + 7, rect.bottom() - 4)
        painter.drawLine(rect.left() + 7, rect.bottom() - 4, rect.right() - 3, rect.top() + 4)


class IconBadge(QWidget):
    def __init__(self, size=42):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#4c82ec"))
        gradient.setColorAt(1, QColor("#745ee8"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        painter.setPen(QPen(QColor("#ffffff"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        x, y = self.width() / 2, self.height() / 2
        painter.drawRoundedRect(QRectF(x - 10, y - 8, 20, 14), 2, 2)
        painter.drawLine(int(x), int(y + 6), int(x), int(y + 10))
        painter.drawLine(int(x - 5), int(y + 10), int(x + 5), int(y + 10))


class StatusLabel(QLabel):
    def setText(self, text):
        super().setText(text)
        state = "active" if any(word in text.lower() for word in ("assigned", "захвачена", "завершена")) else "neutral"
        self.set_state(state)

    def set_state(self, state):
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class DesktopPreview(QLabel):
    def paintEvent(self, event):
        if self.pixmap() is not None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        x, y = center.x(), center.y() - 20
        painter.setPen(QPen(QColor("#6584a8"), 2))
        painter.setBrush(QColor("#19283a"))
        painter.drawRoundedRect(QRectF(x - 25, y - 22, 50, 33), 5, 5)
        painter.drawLine(x, y + 11, x, y + 18)
        painter.drawLine(x - 11, y + 18, x + 11, y + 18)
        painter.setPen(QColor("#a3b5ca"))
        painter.drawText(self.rect().adjusted(12, 50, -12, 0), Qt.AlignCenter | Qt.TextWordWrap, self.text())


def label(text, style=None, wrap=False):
    from PyQt5.QtWidgets import QLabel
    item = QLabel(text)
    if style:
        item.setObjectName(style)
    item.setWordWrap(wrap)
    return item


def card(name="card", margins=20, spacing=12):
    from PyQt5.QtWidgets import QFrame, QVBoxLayout
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return frame, layout


def secondary(button):
    button.setObjectName("secondary")
    return button


def header(title, on_settings):
    from PyQt5.QtWidgets import QHBoxLayout, QPushButton
    row = QHBoxLayout()
    row.setSpacing(14)
    row.addWidget(IconBadge())
    row.addWidget(label(title, "heading"), 1)
    settings = secondary(QPushButton("Настройки"))
    settings.clicked.connect(on_settings)
    row.addWidget(settings, 0)
    return row


class _Signals(QObject):
    done = pyqtSignal(object, object)


class _Task(QRunnable):
    def __init__(self, call):
        super().__init__()
        self.call = call
        self.signals = _Signals()

    def run(self):
        try:
            result, error = self.call(), None
        except Exception as exc:
            result, error = None, exc
        try:
            self.signals.done.emit(result, error)
        except RuntimeError:
            pass  # Qt может уже завершиться, пока сетевой запрос выполняется.


class Background:
    def run_background(self, call, done=None, failed=None):
        task = _Task(call)
        def completed(result, error):
            self._tasks.discard(task)
            if self._closing:
                return
            if error is not None:
                if failed:
                    failed(error)
                else:
                    self.status.setText(str(error))
                    self.status.set_state("error")
            elif done:
                done(result)
        task.signals.done.connect(completed)
        self._tasks.add(task)
        QThreadPool.globalInstance().start(task)

    def init_background(self):
        self._tasks = set()
        self._closing = False


def connection_settings(parent):
    """Настройки действуют до закрытия приложения; учётные данные не меняются."""
    from urllib.parse import urlsplit
    from PyQt5.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout
    dialog = QDialog(parent)
    dialog.setWindowTitle("Настройки подключения")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    address = QLineEdit(parent.api.base)
    host = QLineEdit(parent.relay_host)
    port = QSpinBox()
    port.setRange(1, 65535)
    port.setValue(parent.relay_port)
    verify = QCheckBox("Проверять сертификат HTTPS")
    verify.setChecked(parent.api.verify)
    form.addRow("Адрес сервиса заявок", address)
    form.addRow("Адрес ретранслятора", host)
    form.addRow("Порт ретранслятора", port)
    form.addRow(verify)
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec_() != QDialog.Accepted:
        return
    parsed = urlsplit(address.text().strip())
    try:
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        valid_port = False
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment or not host.text().strip() or not valid_port:
        QMessageBox.warning(parent, "Настройки", "Укажите корректный адрес сервиса и ретранслятора.")
        return
    if parent.ticket_id or parent._tasks:
        QMessageBox.warning(parent, "Настройки", "Настройки можно изменить только без активной заявки и ожидающих запросов.")
        return
    parent.api.base = address.text().strip().rstrip("/")
    parent.api.verify = verify.isChecked()
    parent.relay_host = host.text().strip()
    parent.relay_port = port.value()
    parent.status.setText("Настройки применены до закрытия приложения")
