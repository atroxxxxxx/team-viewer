import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from ticket_service.app import build_app
from ticket_service.auth import Checker
from ticket_service.models import ActionLog, Confirmation, ControlGrant, Observation, Ticket

pytestmark = pytest.mark.integration
CLIENT = ("client", "test-client")
OPERATOR = ("operator", "test-operator")
INVALID_BOOLS = ["false", "true", 0, 1, None, [], {}]


class RelayStub:
    def __init__(self):
        self.opens = []
        self.closes = []
        self.tunnels = {}
        self.started = threading.Event()
        self.proceed = threading.Event()
        self.proceed.set()
        self.fail_open = False

    def open(self, client_id, ticket_id, camera):
        self.opens.append(ticket_id)
        self.started.set()
        if not self.proceed.wait(5):
            raise TimeoutError("test relay timed out")
        if self.fail_open:
            raise ConnectionError("test relay unavailable")
        tunnel = uuid.uuid4()
        self.tunnels[ticket_id] = tunnel
        return tunnel

    def close(self, ticket_id):
        self.closes.append(ticket_id)
        self.tunnels.pop(ticket_id, None)


class API:
    def __init__(self, http, relay):
        self.http = http
        self.relay = relay
        created = http.post("/api/v1/tickets", auth=CLIENT, json={
            "account_id": str(uuid.uuid4()), "description": "Экран не открывается",
        })
        assert created.status_code == 201, created.text
        self.id = created.json()["ticket_id"]
        self.path = "/api/v1/tickets/" + self.id
        self.headers = {"X-Ticket-Token": created.json()["ticket_token"]}

    def operator(self, suffix, body=None):
        return self.http.post(self.path + suffix, auth=OPERATOR, json=body)

    def client(self, suffix, body=None):
        return self.http.post(self.path + suffix, auth=CLIENT, headers=self.headers, json=body)

    def session(self):
        response = self.http.get(self.path + "/session", auth=CLIENT, headers=self.headers)
        assert response.status_code == 200, response.text
        return response.json()

    def claim(self):
        response = self.operator("/claim")
        assert response.status_code == 200, response.text
        return response.json()

    def request_control(self):
        response = self.operator("/control-requests", {"intent": "request_control"})
        assert response.status_code == 201, response.text
        return response.json()["grant_id"]

    def allow_control(self):
        grant_id = self.request_control()
        response = self.client(f"/control-requests/{grant_id}/decision", {
            "allow": True, "scope": "pointer,key,text",
        })
        assert response.status_code == 200, response.text
        return grant_id

    def input(self, grant_id):
        return {"grant_id": grant_id, "type": "text", "intent": "type_in_field", "text": "hello"}


@pytest.fixture
def api(factory):
    relay = RelayStub()
    app = build_app(factory, Checker(*CLIENT, *OPERATOR), relay, 4, "/openapi/tickets.json")
    with TestClient(app) as http:
        yield API(http, relay)


def test_repeat_claim_preserves_existing_tunnel(api):
    claimed = api.claim()
    assert api.operator("/claim").status_code == 409
    assert len(api.relay.opens) == 1
    assert api.relay.closes == []
    assert str(api.relay.tunnels[uuid.UUID(api.id)]) == claimed["tunnel_id"]
    assert api.session()["ticket"]["tunnel_id"] == claimed["tunnel_id"]


def test_concurrent_claim_has_one_relay_open(api, factory):
    api.relay.proceed.clear()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(api.operator, "/claim")
        try:
            assert api.relay.started.wait(3)
            second = executor.submit(api.operator, "/claim")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with factory() as session:
                    waiting = session.scalar(text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                        "AND query LIKE '%FOR UPDATE%'"
                    ))
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting, "second claim must overlap the first transaction"
        finally:
            api.relay.proceed.set()
        responses = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(api.relay.opens) == 1
    assert api.relay.closes == []
    assert len(api.relay.tunnels) == 1


def test_failed_relay_leaves_ticket_available(api):
    api.relay.fail_open = True
    assert api.operator("/claim").status_code == 502
    assert api.session()["ticket"]["status"] == "queued"
    assert api.relay.closes == []
    api.relay.fail_open = False
    api.claim()


@pytest.mark.parametrize("value", INVALID_BOOLS)
def test_camera_requires_json_boolean(api, factory, value):
    response = api.http.post("/api/v1/tickets", auth=CLIENT, json={
        "account_id": str(uuid.uuid4()), "description": "test", "camera": value,
    })
    assert response.status_code == 400
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Ticket)) == 1


@pytest.mark.parametrize("value", INVALID_BOOLS)
def test_control_consent_requires_json_boolean(api, value):
    api.claim()
    grant = api.request_control()
    response = api.client(f"/control-requests/{grant}/decision", {"allow": value})
    assert response.status_code == 400
    assert api.session()["control"]["status"] == "pending"


@pytest.mark.parametrize("value", INVALID_BOOLS)
def test_resolution_requires_json_boolean(api, factory, value):
    api.claim()
    response = api.client("/resolution", {"accept": value})
    assert response.status_code == 400
    assert api.session()["ticket"]["status"] == "assigned"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Confirmation)) == 0


@pytest.mark.parametrize("value", INVALID_BOOLS)
def test_seen_on_screen_requires_json_boolean(api, factory, value):
    api.claim()
    assert api.operator("/plans", {"steps": ["Открыть меню"]}).status_code == 201
    step = api.session()["steps"][0]["id"]
    response = api.operator(f"/steps/{step}/complete", {"seen_on_screen": value, "body": "test"})
    assert response.status_code == 400
    assert api.session()["steps"][0]["status"] == "todo"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Observation)) == 0


@pytest.mark.parametrize("body", [[], [1], None, "text", 42, True])
def test_non_object_json_is_rejected(api, body):
    api.claim()
    response = api.http.post(api.path + "/plans", auth=OPERATOR,
                             content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert api.session()["steps"] == []


def test_missing_consent_and_malformed_json_do_not_decide_grant(api):
    api.claim()
    grant = api.request_control()
    suffix = f"/control-requests/{grant}/decision"
    assert api.client(suffix, {}).status_code == 400
    response = api.http.post(api.path + suffix, auth=CLIENT, headers=api.headers, content=b"{")
    assert response.status_code == 400
    assert api.session()["control"]["status"] == "pending"
    assert api.client(suffix, {"allow": False}).status_code == 200
    assert api.session()["ticket"]["status"] == "guided"


def test_pending_grant_is_not_permission_when_rejecting_resolution(api):
    api.claim()
    api.request_control()
    assert api.client("/resolution", {"accept": False}).json()["status"] == "guided"


def test_input_result_and_observation_work_before_resolution(api):
    api.claim()
    grant = api.allow_control()
    proposed = api.operator("/input", api.input(grant))
    assert proposed.status_code == 201
    action = proposed.json()["action_id"]
    assert api.client(f"/input/{action}/result", {"phase": "applied"}).status_code == 200
    assert api.session()["inputs"] == []
    assert api.operator("/plans", {"steps": ["Проверить меню"]}).status_code == 201
    step = api.session()["steps"][0]["id"]
    assert api.client(f"/steps/{step}/observations", {"body": "Меню открыто"}).status_code == 201
    notes = api.session()["steps"][0]["observations"]
    assert [item["body"] for item in notes] == ["Меню открыто"]
    assert api.operator(f"/steps/{step}/complete", {}).status_code == 200
    assert api.session()["steps"][0]["status"] == "done"


def test_client_review_closes_ticket_without_operator(api):
    api.claim()
    assert api.operator("/plans", {"steps": ["Открыть меню"]}).status_code == 201
    review = "Что не получилось: меню не открылось\nПочему: пункта нет"
    response = api.client("/resolution", {"accept": False, "close": True, "comment": review})
    assert response.status_code == 200
    assert response.json()["status"] == "closed"
    session = api.session()
    assert session["ticket"]["status"] == "closed"
    assert session["messages"][0]["author"] == "client"
    assert session["messages"][0]["body"] == review
    assert session["confirmations"][0]["accept"] is False
    assert api.relay.closes == [uuid.UUID(api.id)]
    assert api.client("/messages", {"body": "ещё"}).status_code == 409


def test_unresolved_review_is_required_and_client_only(api):
    api.claim()
    assert api.client("/resolution", {"accept": False, "close": True}).status_code == 400
    assert api.session()["ticket"]["status"] == "assigned"
    assert api.operator("/resolution", {"accept": False, "close": True, "comment": "отзыв"}).status_code == 403
    assert api.client("/resolution", {"accept": False}).json()["status"] == "guided"
    assert api.relay.closes == []


def test_expired_grant_rejects_new_input(api, factory):
    api.claim()
    grant_id = api.allow_control()
    with factory.begin() as session:
        grant = session.scalar(select(ControlGrant).where(ControlGrant.id == uuid.UUID(grant_id)))
        grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert api.operator("/input", api.input(grant_id)).status_code == 409
    assert api.session()["inputs"] == []


@pytest.fixture
def resolved(api):
    api.claim()
    api.operator("/plans", {"steps": ["Проверить меню"]})
    step = api.session()["steps"][0]["id"]
    assert api.operator(f"/steps/{step}/complete", {"seen_on_screen": True, "body": "вижу"}).status_code == 200
    grant = api.allow_control()
    action = api.operator("/input", api.input(grant)).json()["action_id"]
    pending = api.request_control()
    assert api.operator("/resolution", {"accept": True}).json()["status"] == "awaiting_client"
    assert api.client("/resolution", {"accept": True}).json()["status"] == "resolved"
    return {"api": api, "step": step, "grant": grant, "pending": pending, "action": action}


def test_resolution_revokes_all_grants_and_cancels_input(resolved, factory):
    api = resolved["api"]
    snapshot = api.session()
    assert snapshot["control"] == {}
    assert "control_request" not in snapshot
    assert snapshot["inputs"] == []
    assert api.relay.closes == [uuid.UUID(api.id)]
    with factory() as session:
        grants = list(session.scalars(select(ControlGrant)))
        assert len(grants) == 2
        assert all(grant.status == "revoked" and grant.scope == "" for grant in grants)
        action = session.scalar(select(ActionLog).where(ActionLog.id == uuid.UUID(resolved["action"])))
        assert action.phase == "cancelled"


@pytest.mark.parametrize("operation", [
    "input", "plan", "claim", "request", "decision", "observe", "complete", "revise",
    "problem", "message", "operator_message", "result", "blocked", "reject", "accept",
])
def test_resolved_ticket_rejects_further_changes(resolved, operation):
    api = resolved["api"]
    step = resolved["step"]
    calls = {
        "input": lambda: api.operator("/input", api.input(resolved["grant"])),
        "plan": lambda: api.operator("/plans", {"steps": ["Новый шаг"]}),
        "claim": lambda: api.operator("/claim"),
        "request": lambda: api.operator("/control-requests", {"intent": "request_control"}),
        "decision": lambda: api.client(f"/control-requests/{resolved['pending']}/decision", {"allow": True}),
        "observe": lambda: api.client(f"/steps/{step}/observations", {"body": "Новое наблюдение"}),
        "complete": lambda: api.operator(f"/steps/{step}/complete", {"seen_on_screen": True}),
        "revise": lambda: api.operator(f"/steps/{step}/revise", {"body": "Другой шаг"}),
        "problem": lambda: api.operator("/problems", {"parent_step_id": step, "description": "Новая проблема", "steps": ["Проверить"]}),
        "message": lambda: api.client("/messages", {"body": "test"}),
        "operator_message": lambda: api.operator("/messages", {"body": "test"}),
        "result": lambda: api.client(f"/input/{resolved['action']}/result", {"phase": "applied"}),
        "blocked": lambda: api.operator("/actions", {"intent": "run_shell"}),
        "reject": lambda: api.client("/resolution", {"accept": False, "comment": "test"}),
        "accept": lambda: api.operator("/resolution", {"accept": True}),
    }
    assert calls[operation]().status_code == 409
    snapshot = api.session()
    assert snapshot["ticket"]["status"] == "resolved"
    assert snapshot["messages"] == []
    assert snapshot["inputs"] == []
    assert len(snapshot["confirmations"]) == 2
    assert len(api.relay.opens) == 1
    assert len(api.relay.closes) == 1


def test_concurrent_confirmations_close_once(api):
    api.claim()
    api.allow_control()
    start = threading.Barrier(2)

    def confirm(call):
        start.wait(timeout=5)
        return call("/resolution", {"accept": True})

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(confirm, api.operator)
        second = executor.submit(confirm, api.client)
        assert first.result(timeout=10).status_code == 200
        assert second.result(timeout=10).status_code == 200
    assert api.session()["ticket"]["status"] == "resolved"
    assert len(api.relay.closes) == 1
