import argparse
import sys
from pathlib import Path

_protocol = Path(__file__).resolve().parents[2] / "support-protocol"
if _protocol.is_dir():
    sys.path.insert(0, str(_protocol))

from PyQt5.QtWidgets import QApplication

from operator_app.window import OperatorWindow


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Оператор техподдержки")
    parser.add_argument(
        "--no-models",
        action="store_true",
        help="не скачивать модели и оставить оператора только человеком",
    )
    args = parser.parse_args(argv)
    app = QApplication(sys.argv)
    window = OperatorWindow(None)
    window.show()
    if not args.no_models:
        from operator_app.download_models import ensure_models

        window.model_note.setText("Подготовка моделей в фоновом режиме...")
        def ready(models):
            window.models = models
            window.model_button.setEnabled(True)
            window.allow_model_input.setEnabled(True)
            window.model_note.setText("Профиль {profile}: текст {text_repo}, кадр {vision_repo}, решение {decision_repo}. {reason}".format(**models))
        def failed(error):
            window.model_note.setText(f"Модели недоступны: {error}")
        window.run_background(ensure_models, ready, failed)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
