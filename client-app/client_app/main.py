import sys
from pathlib import Path

_protocol = Path(__file__).resolve().parents[2] / "support-protocol"
if _protocol.is_dir():
    sys.path.insert(0, str(_protocol))

from PySide6.QtWidgets import QApplication

from client_app.window import ClientWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = ClientWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
