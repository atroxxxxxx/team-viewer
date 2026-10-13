"""Три локальные модели: текст заявки, кадр, решение с командой или советом."""

from __future__ import annotations

import base64
import gc
import json
from pathlib import Path

TEXT_SYSTEM = """Ты читаешь заявку техподдержки. Ответь коротко по-русски: в чём проблема и что уже написано в чате.
Не предлагай команд, клавиш и удаления. Пароли и ключи замени словами «секрет в тексте».
"""

VISION_PROMPT = """Кратко опиши окна и текст ошибки на кадре. Пароли и ключи не переписывай, напиши «секрет на экране».
"""

DECISION_SYSTEM = """Ты выбираешь одно следующее действие оператора техподдержки. Ответь одним JSON-объектом без пояснений.
Допустимые action: wait, chat, request_control, plan, complete_step, problem, propose_close, input.
chat — совет клиенту текстом. input — только pointer, key или text в полях программы из заявки.
Поле intent обязательно. Нельзя выбирать намерения delete_file, empty_trash, format_disk, wipe_account, disable_protection, open_inbound_port, create_persistence, exfiltrate, read_secrets, run_shell.
Не предлагай удаление файлов, оболочку, смену пароля, открытие порта и выгрузку секретов.
Форма: {"action":"chat","intent":"guide_user","text":"","steps":["шаг"],"step_id":"","parent_step_id":"","input":{"type":"text","text":"","pointer":{"x":0,"y":0,"button":"left","action":"click"},"key":{"code":"","action":"press","modifiers":[]}}}
Если данных мало, action=chat и один уточняющий вопрос.
"""


def next_action(manifest: dict, context: dict, frame_png: bytes | None, progress=None) -> dict:
    root = Path(manifest["root"])
    gpu_layers = int(manifest.get("gpu_layers") or 0)
    _progress(progress, "Текстовая модель читает заявку")
    brief = _complete(
        root / manifest["text"],
        [
            {"role": "system", "content": TEXT_SYSTEM},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        gpu_layers,
        max_tokens=400,
    )
    if frame_png:
        _progress(progress, "Модель кадра смотрит экран")
        screen = _describe(root / manifest["vision"], root / manifest["vision_mmproj"], frame_png, gpu_layers)
    else:
        screen = "кадра нет"
    _progress(progress, "Модель решения выбирает действие")
    payload = {
        "brief": brief,
        "screen": screen,
        "status": context.get("status", ""),
        "control": context.get("control") or {},
        "steps": context.get("steps") or [],
    }
    raw = _complete(
        root / manifest["decision"],
        [
            {"role": "system", "content": DECISION_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        gpu_layers,
        max_tokens=500,
    )
    return parse_action(raw)


def parse_action(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        fence = text.rfind("```")
        if fence >= 0:
            text = text[:fence]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("модель решения не вернула JSON")
    action = json.loads(text[start : end + 1])
    if not isinstance(action, dict) or "action" not in action:
        raise RuntimeError("модель вернула не действие")
    action.setdefault("intent", "guide_user")
    return action


def _describe(model: Path, mmproj: Path, frame_png: bytes, gpu_layers: int) -> str:
    encoded = base64.b64encode(frame_png).decode("ascii")
    return _complete(
        model,
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }
        ],
        gpu_layers,
        mmproj=mmproj,
        max_tokens=300,
    )


def _complete(model: Path, messages: list, gpu_layers: int, mmproj: Path | None = None, max_tokens: int = 400) -> str:
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise RuntimeError(
            "Для запуска моделей нужен llama-cpp-python. Установите зависимости из requirements-models.txt"
        ) from exc
    handler = _vision_handler(mmproj) if mmproj is not None else None
    llm = Llama(
        model_path=str(model),
        chat_handler=handler,
        n_ctx=4096,
        n_gpu_layers=gpu_layers,
        verbose=False,
    )
    try:
        result = llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=0.2)
        content = result["choices"][0]["message"]["content"]
    finally:
        del llm
        if handler is not None:
            del handler
        gc.collect()
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"пустой ответ модели {model.name}")
    return content


def _vision_handler(mmproj: Path):
    from llama_cpp import llama_chat_format

    handler_cls = getattr(llama_chat_format, "MTMDChatHandler", None)
    if handler_cls is None:
        raise RuntimeError("Нужен llama-cpp-python со зрительным обработчиком MTMDChatHandler")
    return handler_cls(clip_model_path=str(mmproj), verbose=False)


def _progress(progress, text: str) -> None:
    if progress is not None:
        progress(text)
