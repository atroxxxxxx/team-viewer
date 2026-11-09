"""Отменяемый процесс ввода для одной клиентской сессии."""
import subprocess
import threading


class InputSession:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process = None

    def cancel(self):
        with self._lock:
            self.cancelled.set()
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()

    def execute(self, args):
        # Проверка и запуск под одним замком: после cancel новый OS-ввод не запускается.
        with self._lock:
            if self.cancelled.is_set():
                return False
            process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self._process = process
        try:
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                return False
            return process.returncode == 0 and not self.cancelled.is_set()
        finally:
            with self._lock:
                self._process = None
