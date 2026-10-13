from datetime import datetime


def usable(grant, now: datetime) -> bool:
    if grant.status != "active":
        return False
    return grant.expires_at is None or grant.expires_at > now


def pick_controls(grants, now: datetime):
    """Ввод опирается на действующий грант. Новый запрос pending не подменяет его."""
    active = next((grant for grant in grants if usable(grant, now)), None)
    pending = next((grant for grant in grants if grant.status == "pending"), None)
    if active is not None:
        return active, pending
    return pending, None
