import argparse
import sys
from pathlib import Path

_protocol = Path(__file__).resolve().parents[2] / "support-protocol"
if _protocol.is_dir():
    sys.path.insert(0, str(_protocol))

from PyQt5.QtWidgets import QApplication

from support_protocol.log import configure

from operator_app.window import OperatorWindow


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Оператор техподдержки")
    parser.add_argument(
        "--no-models",
        action="store_true",
        help="не скачивать модели и оставить оператора только человеком",
    )
    args = parser.parse_args(argv)
    configure("operator")
    app = QApplication(sys.argv)
    window = OperatorWindow(None)
    window.show()
    if not args.no_models:
        window.prepare_models()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
