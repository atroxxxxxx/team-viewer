"""Управляемый Qt-процесс: долгие модели не удерживают пул и приложение при выходе."""
import json
import logging
import sys
from pathlib import Path

from PyQt5.QtCore import QProcess

log = logging.getLogger(__name__)
WORKER = Path(__file__).with_name("model_worker.py")


class ModelProcess(QProcess):
    def __init__(self, owner, kind, payload, done, failed):
        super().__init__(owner)
        self.owner, self.done, self.failed = owner, done, failed
        self.payload = json.dumps(payload, ensure_ascii=False).encode()
        self.output = bytearray()
        self.errors = bytearray()
        self.completed = False
        owner._model_jobs.add(self)
        self.started.connect(self._send)
        self.readyReadStandardOutput.connect(self._read_output)
        self.readyReadStandardError.connect(self._read_errors)
        self.finished.connect(self._finish)
        self.errorOccurred.connect(self._error)
        self.start(sys.executable, [str(WORKER), kind])

    def _send(self):
        self.write(self.payload)
        self.closeWriteChannel()
        self.payload = None

    def _read_output(self):
        self.output.extend(bytes(self.readAllStandardOutput()))
        if len(self.output) > 2_000_000:
            self.kill()

    def _read_errors(self):
        self.errors = (self.errors + bytes(self.readAllStandardError()))[-16_384:]

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self._finish(-1, QProcess.CrashExit)

    def _finish(self, code, status):
        if self.completed:
            return
        self.completed = True
        self._read_output()
        self._read_errors()
        self.owner._model_jobs.discard(self)
        try:
            if self.owner._closing:
                return
            if code != 0 or status != QProcess.NormalExit:
                raise RuntimeError(self.errorString())
            result = json.loads(bytes(self.output))
            if not isinstance(result, dict):
                raise ValueError("model worker must return an object")
            self.done(result)
        except Exception:
            log.exception("Model process failed; stderr: %s", self.errors.decode("utf-8", errors="replace"))
            if not self.owner._closing:
                self.failed(RuntimeError("Не удалось выполнить задачу модели. Подробности записаны в журнал."))
        finally:
            self.done = self.failed = None
            self.deleteLater()
