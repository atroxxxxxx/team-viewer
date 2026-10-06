"""Один контракт проверки для оператора и клиента."""

FORBIDDEN_INTENTS = {
    "delete_file",
    "empty_trash",
    "format_disk",
    "wipe_account",
    "disable_protection",
    "open_inbound_port",
    "create_persistence",
    "exfiltrate",
    "read_secrets",
    "run_shell",
}

FORBIDDEN_TEXT = (
    "rm -",
    "shred ",
    "mkfs",
    "remove-item",
    "del /",
    "format c:",
    "diskpart",
    "dd if=",
    "useradd",
    "net user",
    "schtasks",
    "iptables",
    "ufw ",
    "passwd",
    "authorized_keys",
)

INPUT_TYPES = {"pointer", "key", "text"}


def intent_allowed(intent: str) -> bool:
    cleaned = (intent or "").strip()
    return bool(cleaned) and cleaned not in FORBIDDEN_INTENTS


def text_allowed(text: str) -> bool:
    folded = (text or "").lower()
    return not any(item in folded for item in FORBIDDEN_TEXT)


def key_allowed(key: dict | None) -> bool:
    key = key or {}
    code = str(key.get("code", "")).lower()
    modifiers = {str(item).lower() for item in key.get("modifiers") or []}
    if code in {"delete", "kp_delete"} and "shift" in modifiers:
        return False
    if code == "f2" and "alt" in modifiers:
        return False
    if code == "t" and {"ctrl", "alt"} <= modifiers:
        return False
    if code == "delete" and {"ctrl", "alt"} <= modifiers:
        return False
    if code == "r" and ("super" in modifiers or "meta" in modifiers):
        return False
    return True


def validate_input(event: dict, grant: dict, frame_width: int, frame_height: int) -> str | None:
    kind = event.get("type")
    if kind not in INPUT_TYPES:
        return "неизвестный тип события"
    if not intent_allowed(str(event.get("intent", ""))):
        return "намерение запрещено или пустое"
    if grant.get("status") != "active":
        return "грант не активен"
    scope = {item.strip() for item in (grant.get("scope") or "").split(",") if item.strip()}
    if kind not in scope:
        return "тип вне scope"
    if kind == "text":
        text = str(event.get("text") or "")
        if not text or len(text) > 200 or not text_allowed(text):
            return "текст пустой, длинный или запрещён"
    if kind == "key" and not key_allowed(event.get("key")):
        return "сочетание клавиш запрещено"
    if kind == "pointer":
        pointer = event.get("pointer") or {}
        try:
            x = int(pointer.get("x"))
            y = int(pointer.get("y"))
        except (TypeError, ValueError):
            return "координаты указателя не заданы"
        if x < 0 or y < 0 or x >= frame_width or y >= frame_height:
            return "указатель вне кадра"
    return None


def validate_step(body: str) -> str | None:
    text = (body or "").strip()
    if not text or len(text) > 500:
        return "шаг пустой или длиннее 500 символов"
    if not text_allowed(text):
        return "шаг запрещён правилами"
    return None
