"""Общие оформление и фоновые задачи настольных приложений."""
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

STYLE = """
QWidget { background: #10151f; color: #e7edf7; font-size: 13px; }
QLabel { background: transparent; }
QLabel#heading { font-size: 23px; font-weight: 700; }
QLabel#status { color: #9fc3ff; padding: 9px; background: #19283c; border-radius: 6px; }
QLineEdit, QPlainTextEdit, QListWidget { background: #1a2230; border: 1px solid #303d50; border-radius: 6px; padding: 8px; selection-background-color: #345c9c; }
QPushButton { background: #315da8; border: 0; border-radius: 6px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #3c70c3; }
QPushButton:disabled { background: #303946; color: #8692a3; }
QCheckBox { spacing: 7px; }
QTabWidget::pane { border: 1px solid #303d50; border-radius: 6px; }
QTabBar::tab { padding: 10px 16px; background: #1a2230; color: #afbdd0; }
QTabBar::tab:selected { background: #2b4265; color: white; }
"""


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
