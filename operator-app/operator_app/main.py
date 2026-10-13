import argparse
import sys
from pathlib import Path

_protocol = Path(__file__).resolve().parents[2] / "support-protocol"
if _protocol.is_dir():
    sys.path.insert(0, str(_protocol))

from PySide6.QtWidgets import QApplication

from operator_app.window import OperatorWindow


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Оператор техподдержки")
    parser.add_argument(
        "--no-models",
        action="store_true",
        help="не скачивать модели и оставить оператора только человеком",
    )
    args = parser.parse_args(argv)
    models = None
    if not args.no_models:
        from operator_app.download_models import ensure_models

        models = ensure_models()
    app = QApplication(sys.argv)
    window = OperatorWindow(models)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
