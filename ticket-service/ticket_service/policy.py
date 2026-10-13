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
    "rm\t-",
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


def intent_allowed(intent: str) -> bool:
    value = intent.strip()
    return bool(value) and value not in FORBIDDEN_INTENTS


def scope_allows(scope: str, kind: str) -> bool:
    return kind in {part.strip() for part in scope.split(",")}


def normalize_scope(scope: str) -> str | None:
    allowed = {"pointer", "key", "text"}
    out = []
    seen = set()
    for part in scope.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in allowed or part in seen:
            if part not in allowed:
                return None
            continue
        seen.add(part)
        out.append(part)
    if not out:
        return None
    return ",".join(out)


def text_allowed(text: str) -> bool:
    folded = text.lower()
    return not any(item in folded for item in FORBIDDEN_TEXT)
