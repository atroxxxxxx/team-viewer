import base64
import json
from urllib.parse import urlparse

import httpx

SYSTEM = """Ты оператор техподдержки внутри приложения. Отвечай одним JSON-объектом без пояснений.
Допустимые action: wait, chat, request_control, plan, complete_step, problem, propose_close, input.
Поле intent обязательно. Нельзя выбирать намерения delete_file, empty_trash, format_disk, wipe_account, disable_protection, open_inbound_port, create_persistence, exfiltrate, read_secrets, run_shell.
Не предлагай удаление файлов, оболочку, смену пароля, открытие порта и выгрузку секретов.
input допустим только как pointer, key или text в полях программы из заявки.
Форма: {"action":"plan","intent":"guide_user","text":"","steps":["шаг"],"step_id":"","parent_step_id":"","input":{"type":"text","text":"","pointer":{"x":0,"y":0,"button":"left","action":"click"},"key":{"code":"","action":"press","modifiers":[]}}}
Если данных мало, action=chat и один уточняющий вопрос.
"""


def next_action(endpoint: str, planner: str, vision: str, context: dict, frame_png: bytes | None) -> dict:
    endpoint = local_endpoint(endpoint)
    screen = ""
    if frame_png and vision.strip():
        screen = _describe(endpoint, vision.strip(), frame_png)
    payload = dict(context)
    payload["screen"] = screen
    response = httpx.post(
        endpoint.rstrip("/") + "/api/chat",
        json={
            "model": planner,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        },
        timeout=180,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]
    action = json.loads(content)
    if not isinstance(action, dict) or "action" not in action:
        raise RuntimeError("модель вернула не действие")
    action.setdefault("intent", "guide_user")
    return action


def local_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint.strip())
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("адрес модели должен быть на localhost")
    return endpoint.rstrip("/")


def _describe(endpoint: str, model: str, frame_png: bytes) -> str:
    response = httpx.post(
        endpoint.rstrip("/") + "/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": "Кратко опиши окна и текст ошибки на кадре. Пароли и ключи не переписывай, напиши «секрет на экране».",
                    "images": [base64.b64encode(frame_png).decode("ascii")],
                }
            ],
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
