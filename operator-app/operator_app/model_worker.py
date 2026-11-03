"""Изолированный процесс для скачивания и выполнения моделей."""
import base64
import contextlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    payload = json.load(sys.stdin)
    with contextlib.redirect_stdout(sys.stderr):
        if sys.argv[1] == "prepare":
            from operator_app.download_models import ensure_models
            result = ensure_models()
        else:
            from operator_app.agent import next_action
            frame = base64.b64decode(payload["frame"]) if payload.get("frame") else None
            result = next_action(payload["models"], payload["context"], frame)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
