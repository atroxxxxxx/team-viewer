TICKET_STATUS = {
    "queued": "в очереди",
    "assigned": "оператор подключился",
    "awaiting_permission": "жду разрешение",
    "remote_control": "удалённое управление",
    "guided": "работа по плану",
    "awaiting_client": "жду ответ клиента",
    "resolved": "решено",
    "closed": "закрыта",
}

STEP_STATUS = {
    "todo": "Нужно сделать",
    "done": "Сделано",
    "blocked": "Заблокирован",
    "cancelled": "Отменён",
    "pending": "Нужно сделать",
}


def ticket_status(status: str, ticket_id: str = "") -> str:
    name = TICKET_STATUS.get(status, status)
    if ticket_id:
        return f"{name} · {ticket_id}"
    return name


def step_line(step: dict) -> str:
    title = STEP_STATUS.get(str(step.get("status") or ""), "Шаг")
    line = f"{title}: {step.get('body')}"
    notes = [str(item.get("body") or "").strip() for item in step.get("observations") or []]
    notes = [note for note in notes if note]
    if notes:
        line += " — ответ клиента: " + " · ".join(notes)
    return line
