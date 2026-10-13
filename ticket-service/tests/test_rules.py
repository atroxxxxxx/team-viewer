import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.orm import Session

from ticket_service import store
from ticket_service.grants import pick_controls, usable
from ticket_service.models import Ticket
from ticket_service.policy import intent_allowed, text_allowed
from ticket_service.shard import shard_id


class Grant:
    def __init__(self, status, expires_at=None):
        self.status = status
        self.expires_at = expires_at


class PolicyTests(unittest.TestCase):
    def test_intent(self):
        self.assertFalse(intent_allowed("delete_file"))
        self.assertFalse(intent_allowed(""))
        self.assertTrue(intent_allowed("adjust_app_setting"))

    def test_text(self):
        self.assertFalse(text_allowed("выполните rm -rf ~"))
        self.assertTrue(text_allowed("откройте меню Вид и включите линейку"))


class ShardTests(unittest.TestCase):
    def test_stable(self):
        account = uuid.UUID("6f1b1c3e-8a0d-4f2e-9c11-2b7e5a4d6c80")
        self.assertEqual(shard_id(account, 4), shard_id(account, 4))
        self.assertIn(shard_id(account, 4), range(4))


class GrantTests(unittest.TestCase):
    def test_active_wins_over_pending(self):
        now = datetime.now(timezone.utc)
        active = Grant("active", now + timedelta(minutes=10))
        pending = Grant("pending")
        chosen, request = pick_controls([pending, active], now)
        self.assertIs(chosen, active)
        self.assertIs(request, pending)
        self.assertTrue(usable(active, now))

    def test_pending_when_no_active(self):
        now = datetime.now(timezone.utc)
        pending = Grant("pending")
        chosen, request = pick_controls([pending], now)
        self.assertIs(chosen, pending)
        self.assertIsNone(request)


class InsertOrderTests(unittest.TestCase):
    def test_ticket_is_flushed_before_log(self):
        account = uuid.uuid4()
        ticket = Ticket(
            shard_id=2,
            id=uuid.uuid4(),
            account_id=account,
            description="экран не открывается",
            status="queued",
            camera=False,
            token_hash="hash",
        )
        flushed = []

        def remember(session, objects=None):
            flushed.append(tuple(sorted(type(obj).__name__ for obj in session.new)))
            for obj in list(session.new):
                session.expunge(obj)

        with Session() as session, patch.object(Session, "flush", remember):
            store.create_ticket(session, ticket)
            pending = sorted(type(obj).__name__ for obj in session.new)
        self.assertEqual(flushed, [("Ticket", "TicketIndex")])
        self.assertEqual(pending, ["ActionLog", "ChatMember"])


if __name__ == "__main__":
    unittest.main()
