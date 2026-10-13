import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from ticket_service import store
from ticket_service.auth import Checker, Principal
from ticket_service.grants import pick_controls
from ticket_service.log import error, info
from ticket_service.models import Ticket
from ticket_service.policy import intent_allowed, normalize_scope, scope_allows, text_allowed
from ticket_service.relay import Relay
from ticket_service.shard import shard_id
from ticket_service.token import hash_token, new_token

SPEC = Path(__file__).resolve().parents[1] / "openapi.json"


@contextmanager
def unit(session):
    try:
        yield
        session.commit()
    except Exception:
        session.rollback()
        raise
GRANT_TTL = timedelta(minutes=30)


def build_app(factory: sessionmaker, creds: Checker, relay: Relay, shard_count: int, swagger_url: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    spec = SPEC.read_bytes()

    @app.middleware("http")
    async def access(request: Request, call_next):
        if request.headers.get("content-length") and int(request.headers["content-length"]) > 1 << 20:
            return JSONResponse({"error": "некорректное тело"}, status_code=413)
        started = datetime.now(timezone.utc)
        response = await call_next(request)
        info("http", method=request.method, path=request.url.path, status=response.status_code, ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
        return response

    @app.get("/")
    def root(request: Request):
        host = request.headers.get("host", "").split(":")[0].strip("[]")
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse(status_code=444, content=None)
        return JSONResponse(status_code=302, content=None, headers={"Location": "/swagger/index.html"})

    @app.get("/health")
    def health():
        try:
            with factory() as session:
                session.execute(text("SELECT 1"))
            return {"status": "ok"}
        except Exception:
            return JSONResponse({"status": "postgres down"}, status_code=503)

    @app.get("/openapi.json")
    @app.get("/openapi/tickets.json")
    def openapi():
        return JSONResponse(json.loads(spec))

    @app.get("/swagger/index.html", response_class=HTMLResponse)
    def swagger_page():
        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Заявки</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"></head>
<body><div id="ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({{url: {json.dumps(swagger_url)}, dom_id: "#ui"}})</script>
</body></html>"""

    def principal(request: Request) -> Principal | JSONResponse:
        header = request.headers.get("authorization", "")
        user, password = "", ""
        if header.lower().startswith("basic "):
            import base64

            try:
                user, password = base64.b64decode(header.split(" ", 1)[1]).decode().split(":", 1)
            except (ValueError, UnicodeError):
                user, password = "", ""
        found = creds.authenticate(user, password)
        if found is None:
            return JSONResponse({"error": "нужна аутентификация"}, status_code=401, headers={"WWW-Authenticate": 'Basic realm="tickets"'})
        return found

    def fail(exc: Exception) -> JSONResponse:
        if isinstance(exc, store.NotFound):
            return JSONResponse({"error": "заявка не найдена"}, status_code=404)
        if isinstance(exc, store.Conflict):
            return JSONResponse({"error": "заявка в другом состоянии"}, status_code=409)
        error("store", err=str(exc))
        return JSONResponse({"error": "ошибка хранилища"}, status_code=500)

    def locate(session: Session, raw_id: str):
        try:
            ticket_id = uuid.UUID(raw_id)
        except ValueError:
            return None, JSONResponse({"error": "id заявки должен быть uuid"}, status_code=400)
        try:
            return (ticket_id, store.locate(session, ticket_id)), None
        except Exception as exc:
            return None, fail(exc)

    def require_token(session: Session, shard: int, ticket_id: uuid.UUID, request: Request) -> JSONResponse | None:
        if not store.token_matches(session, shard, ticket_id, hash_token(request.headers.get("x-ticket-token", ""))):
            return JSONResponse({"error": "токен заявки не подходит"}, status_code=403)
        return None

    def assignee_ticket(session: Session, who: Principal, ticket_id: uuid.UUID) -> tuple[int, Ticket] | JSONResponse:
        if who.role != "operator":
            return JSONResponse({"error": "это действие оператора"}, status_code=403)
        try:
            shard = store.locate(session, ticket_id)
            ticket = store.get_ticket(session, shard, ticket_id)
        except Exception as exc:
            return fail(exc)
        if ticket.assignee != who.operator_id:
            return JSONResponse({"error": "заявка закреплена за другим оператором"}, status_code=403)
        return shard, ticket

    @app.post("/api/v1/tickets")
    async def create_ticket(request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        if who.role != "client":
            return JSONResponse({"error": "недостаточно прав"}, status_code=403)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "некорректное тело"}, status_code=400)
        try:
            account_id = uuid.UUID(str(body.get("account_id", "")))
        except ValueError:
            return JSONResponse({"error": "account_id должен быть uuid"}, status_code=400)
        description = str(body.get("description") or "").strip()
        if not description or len(description) > 4000:
            return JSONResponse({"error": "описание пустое или длиннее 4000 символов"}, status_code=400)
        raw = new_token()
        ticket = Ticket(
            shard_id=shard_id(account_id, shard_count),
            id=uuid.uuid4(),
            account_id=account_id,
            description=description,
            status="queued",
            camera=bool(body.get("camera")),
            token_hash=hash_token(raw),
        )
        try:
            with factory() as session:
                with unit(session):
                    store.create_ticket(session, ticket)
        except Exception as exc:
            error("create ticket", err=str(exc))
            return JSONResponse({"error": "не удалось сохранить заявку"}, status_code=500)
        info("ticket created", ticket_id=str(ticket.id), shard_id=ticket.shard_id)
        return JSONResponse({"ticket_id": str(ticket.id), "shard_id": ticket.shard_id, "ticket_token": raw}, status_code=201)

    @app.get("/api/v1/tickets")
    def list_tickets(request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        if who.role != "operator":
            return JSONResponse({"error": "недостаточно прав"}, status_code=403)
        try:
            with factory() as session:
                items = store.list_queued(session)
        except Exception as exc:
            error("list tickets", err=str(exc))
            return JSONResponse({"error": "не удалось прочитать очередь"}, status_code=500)
        return {"tickets": [_ticket(item) for item in items]}

    @app.get("/api/v1/tickets/{ticket_id}")
    def get_one(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        with factory() as session:
            located, problem = locate(session, ticket_id)
            if problem:
                return problem
            found_id, shard = located
            if who.role != "operator":
                denied = require_token(session, shard, found_id, request)
                if denied:
                    return denied
            try:
                ticket = store.get_ticket(session, shard, found_id)
            except Exception as exc:
                return fail(exc)
            return _ticket(ticket)

    @app.post("/api/v1/tickets/{ticket_id}/claim")
    def claim(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        if who.role != "operator":
            return JSONResponse({"error": "недостаточно прав"}, status_code=403)
        with factory() as session:
            located, problem = locate(session, ticket_id)
            if problem:
                return problem
            found_id, shard = located
            try:
                ticket = store.get_ticket(session, shard, found_id)
            except Exception as exc:
                return fail(exc)
        try:
            tunnel = relay.open(ticket.account_id, found_id, ticket.camera)
        except Exception as exc:
            error("relay open", err=str(exc))
            return JSONResponse({"error": "ретранслятор не принял заявку"}, status_code=502)
        try:
            with factory() as session:
                with unit(session):
                    store.claim(session, shard, found_id, who.operator_id, tunnel)
        except Exception as exc:
            try:
                relay.close(found_id)
            except Exception as close_exc:
                error("relay close after claim", err=str(close_exc))
            return fail(exc)
        info("ticket claimed", ticket_id=str(found_id), tunnel_id=str(tunnel), shard_id=shard)
        return {
            "ticket_id": str(found_id),
            "shard_id": shard,
            "account_id": str(ticket.account_id),
            "tunnel_id": str(tunnel),
            "camera": ticket.camera,
            "description": ticket.description,
        }

    @app.get("/api/v1/tickets/{ticket_id}/session")
    def get_session(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        now = datetime.now(timezone.utc)
        with factory() as session:
            located, problem = locate(session, ticket_id)
            if problem:
                return problem
            found_id, shard = located
            if who.role != "operator":
                denied = require_token(session, shard, found_id, request)
                if denied:
                    return denied
            try:
                ticket = store.get_ticket(session, shard, found_id)
                control, pending = pick_controls(store.grants_for(session, shard, found_id), now)
            except Exception as exc:
                return fail(exc)
            payload = {
                "ticket": _ticket(ticket),
                "messages": [_message(item) for item in store.messages(session, shard, found_id)],
                "steps": [_step(item) for item in store.steps(session, shard, found_id)],
                "inputs": [_log(item) for item in store.pending_inputs(session, shard, found_id)],
                "confirmations": [_confirmation(item) for item in store.confirmations(session, shard, found_id)],
                "control": _grant(control),
            }
            if pending is not None and control is not pending:
                payload["control_request"] = _grant(pending)
            return payload

    @app.post("/api/v1/tickets/{ticket_id}/messages")
    async def post_message(ticket_id: str, request: Request):
        return await _participant_write(request, ticket_id, _message_body)

    @app.post("/api/v1/tickets/{ticket_id}/control-requests")
    async def request_control(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
            intent = str(body.get("intent") or "")
        except json.JSONDecodeError:
            intent = "request_control"
        if not intent_allowed(intent):
            return JSONResponse({"error": "намерение запрещено"}, status_code=403)
        with factory() as session:
            parsed = _uuid(ticket_id, "id заявки должен быть uuid")
            if isinstance(parsed, JSONResponse):
                return parsed
            gate = assignee_ticket(session, who, parsed)
            if isinstance(gate, JSONResponse):
                return gate
            shard, _ticket_row = gate
            try:
                with unit(session):
                    grant_id = store.request_control(session, shard, parsed, intent)
            except Exception as exc:
                return fail(exc)
        return JSONResponse({"grant_id": str(grant_id), "status": "pending"}, status_code=201)

    @app.post("/api/v1/tickets/{ticket_id}/control-requests/{grant_id}/decision")
    async def decide(ticket_id: str, grant_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        if who.role != "client":
            return JSONResponse({"error": "это действие клиента"}, status_code=403)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        grant = _uuid(grant_id, "grant должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        if isinstance(grant, JSONResponse):
            return grant
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "некорректное тело"}, status_code=400)
        allow = bool(body.get("allow"))
        scope = "pointer,key,text"
        if allow and str(body.get("scope") or "").strip():
            normalized = normalize_scope(str(body.get("scope")))
            if normalized is None:
                return JSONResponse({"error": "scope может содержать только pointer, key и text"}, status_code=403)
            scope = normalized
        with factory() as session:
            located, problem = locate(session, ticket_id)
            if problem:
                return problem
            found_id, shard = located
            denied = require_token(session, shard, found_id, request)
            if denied:
                return denied
            try:
                with unit(session):
                    store.decide_control(session, shard, found_id, grant, allow, scope, GRANT_TTL)
            except Exception as exc:
                return fail(exc)
        return {"allow": allow, "scope": scope}

    @app.post("/api/v1/tickets/{ticket_id}/input")
    async def post_input(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "некорректное тело"}, status_code=400)
        kind = body.get("type")
        text = str(body.get("text") or "")
        try:
            grant_id = uuid.UUID(str(body.get("grant_id")))
        except ValueError:
            grant_id = None
        if grant_id is None or kind not in {"pointer", "key", "text"} or not intent_allowed(str(body.get("intent") or "")) or not text_allowed(text) or len(text) > 200:
            return JSONResponse({"error": "событие не проходит проверку схемы или правил"}, status_code=403)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        now = datetime.now(timezone.utc)
        with factory() as session:
            gate = assignee_ticket(session, who, parsed)
            if isinstance(gate, JSONResponse):
                return gate
            shard, _ticket_row = gate
            try:
                grant = store.grant_for_input(session, shard, parsed, grant_id, now)
            except Exception as exc:
                return fail(exc)
            if not scope_allows(grant.scope or "", kind):
                return JSONResponse({"error": "тип события вне scope"}, status_code=403)
            try:
                with unit(session):
                    action_id = store.propose_input(session, shard, parsed, grant, str(body["intent"]), body)
            except Exception as exc:
                return fail(exc)
        return JSONResponse({"action_id": str(action_id), "phase": "proposed"}, status_code=201)

    @app.post("/api/v1/tickets/{ticket_id}/input/{action_id}/result")
    async def finish_input(ticket_id: str, action_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        if who.role != "client":
            return JSONResponse({"error": "это действие клиента"}, status_code=403)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        action = _uuid(action_id, "action должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        if isinstance(action, JSONResponse):
            return action
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        phase = body.get("phase")
        if phase not in {"applied", "rejected_by_client"}:
            return JSONResponse({"error": "phase должна быть applied или rejected_by_client"}, status_code=400)
        with factory() as session:
            located, problem = locate(session, ticket_id)
            if problem:
                return problem
            found_id, shard = located
            denied = require_token(session, shard, found_id, request)
            if denied:
                return denied
            try:
                with unit(session):
                    store.finish_input(session, shard, found_id, action, phase, str(body.get("observation") or "").strip())
            except Exception as exc:
                return fail(exc)
        return {"phase": phase}

    @app.post("/api/v1/tickets/{ticket_id}/actions")
    async def blocked(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        intent = str(body.get("intent") or "").strip()
        if not intent:
            return JSONResponse({"error": "нужно намерение"}, status_code=400)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        with factory() as session:
            gate = assignee_ticket(session, who, parsed)
            if isinstance(gate, JSONResponse):
                return gate
            shard, _ticket_row = gate
            try:
                with unit(session):
                    store.log_blocked(session, shard, parsed, who.role, intent, {"detail": str(body.get("detail") or "")})
            except Exception as exc:
                return fail(exc)
        return JSONResponse({"phase": "blocked_by_policy"}, status_code=201)

    @app.post("/api/v1/tickets/{ticket_id}/plans")
    async def post_plan(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
            raw_steps = body.get("steps") or []
        except json.JSONDecodeError:
            raw_steps = []
        if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > 20:
            return JSONResponse({"error": "нужен хотя бы один шаг и не больше 20"}, status_code=400)
        cleaned = []
        for step in raw_steps:
            text = str(step).strip()
            if not text or len(text) > 500 or not text_allowed(text):
                return JSONResponse({"error": "шаг пустой или запрещён правилами"}, status_code=403)
            cleaned.append(text)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        with factory() as session:
            gate = assignee_ticket(session, who, parsed)
            if isinstance(gate, JSONResponse):
                return gate
            shard, _ticket_row = gate
            try:
                with unit(session):
                    plan_id = store.create_plan(session, shard, parsed, None, cleaned)
            except Exception as exc:
                return fail(exc)
        return JSONResponse({"plan_id": str(plan_id)}, status_code=201)

    @app.post("/api/v1/tickets/{ticket_id}/steps/{step_id}/observations")
    async def observe(ticket_id: str, step_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        if who.role != "client":
            return JSONResponse({"error": "это действие клиента"}, status_code=403)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        text = str(body.get("body") or "").strip()
        if not text or not text_allowed(text):
            return JSONResponse({"error": "наблюдение пустое или запрещено"}, status_code=400)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        step = _uuid(step_id, "step должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        if isinstance(step, JSONResponse):
            return step
        with factory() as session:
            located, problem = locate(session, ticket_id)
            if problem:
                return problem
            found_id, shard = located
            denied = require_token(session, shard, found_id, request)
            if denied:
                return denied
            try:
                with unit(session):
                    store.add_observation(session, shard, found_id, step, text)
            except Exception as exc:
                return fail(exc)
        return JSONResponse({"status": "noted"}, status_code=201)

    @app.post("/api/v1/tickets/{ticket_id}/steps/{step_id}/complete")
    async def complete(ticket_id: str, step_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        step = _uuid(step_id, "step должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        if isinstance(step, JSONResponse):
            return step
        with factory() as session:
            gate = assignee_ticket(session, who, parsed)
            if isinstance(gate, JSONResponse):
                return gate
            shard, _ticket_row = gate
            try:
                with unit(session):
                    store.complete_step(session, shard, parsed, step, bool(body.get("seen_on_screen")), str(body.get("body") or "").strip())
            except Exception as exc:
                return fail(exc)
        return {"status": "done"}

    @app.post("/api/v1/tickets/{ticket_id}/steps/{step_id}/revise")
    async def revise(ticket_id: str, step_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        text = str(body.get("body") or "").strip()
        if not text_allowed(text):
            return JSONResponse({"error": "новый текст шага запрещён"}, status_code=403)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        step = _uuid(step_id, "step должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        if isinstance(step, JSONResponse):
            return step
        with factory() as session:
            gate = assignee_ticket(session, who, parsed)
            if isinstance(gate, JSONResponse):
                return gate
            shard, _ticket_row = gate
            try:
                with unit(session):
                    store.revise_step(session, shard, parsed, step, text)
            except Exception as exc:
                return fail(exc)
        return {"status": "revised"}

    @app.post("/api/v1/tickets/{ticket_id}/problems")
    async def problem(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "некорректное тело"}, status_code=400)
        steps = body.get("steps") or []
        description = str(body.get("description") or "").strip()
        try:
            parent = uuid.UUID(str(body.get("parent_step_id")))
        except ValueError:
            parent = None
        if parent is None or not description or not isinstance(steps, list) or not steps or len(steps) > 20:
            return JSONResponse({"error": "нужны родительский шаг, описание и шаги"}, status_code=400)
        if any(not text_allowed(str(step)) for step in steps):
            return JSONResponse({"error": "шаг запрещён правилами"}, status_code=403)
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        with factory() as session:
            gate = assignee_ticket(session, who, parsed)
            if isinstance(gate, JSONResponse):
                return gate
            shard, _ticket_row = gate
            try:
                with unit(session):
                    problem_id = store.add_problem(session, shard, parsed, parent, description, [str(step) for step in steps])
            except Exception as exc:
                return fail(exc)
        return JSONResponse({"problem_id": str(problem_id)}, status_code=201)

    @app.post("/api/v1/tickets/{ticket_id}/resolution")
    async def resolution(ticket_id: str, request: Request):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "некорректное тело"}, status_code=400)
        accept = bool(body.get("accept"))
        comment = str(body.get("comment") or "").strip()
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        now = datetime.now(timezone.utc)
        with factory() as session:
            if who.role == "client":
                located, problem = locate(session, ticket_id)
                if problem:
                    return problem
                found_id, shard = located
                denied = require_token(session, shard, found_id, request)
                if denied:
                    return denied
            else:
                gate = assignee_ticket(session, who, parsed)
                if isinstance(gate, JSONResponse):
                    return gate
                shard, _ticket_row = gate
                found_id = parsed
            try:
                with unit(session):
                    if comment:
                        store.add_message(session, shard, found_id, who.role, comment)
                    status = store.confirm(session, shard, found_id, who.role, accept, now)
            except Exception as exc:
                return fail(exc)
        if status == "resolved":
            try:
                relay.close(found_id)
            except Exception as exc:
                error("relay close", err=str(exc), ticket_id=str(found_id))
        return {"status": status}

    async def _participant_write(request: Request, ticket_id: str, writer):
        who = principal(request)
        if isinstance(who, JSONResponse):
            return who
        parsed = _uuid(ticket_id, "id заявки должен быть uuid")
        if isinstance(parsed, JSONResponse):
            return parsed
        with factory() as session:
            if who.role == "client":
                located, problem = locate(session, ticket_id)
                if problem:
                    return problem
                found_id, shard = located
                denied = require_token(session, shard, found_id, request)
                if denied:
                    return denied
            else:
                gate = assignee_ticket(session, who, parsed)
                if isinstance(gate, JSONResponse):
                    return gate
                shard, _ticket_row = gate
                found_id = parsed
            return await writer(session, shard, found_id, who, request)

    async def _message_body(session, shard, found_id, who, request: Request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "некорректное тело"}, status_code=400)
        text = str(body.get("body") or "").strip()
        if not text or len(text) > 4000 or not text_allowed(text):
            return JSONResponse({"error": "сообщение пустое, слишком длинное или запрещено правилами"}, status_code=400)
        try:
            with unit(session):
                store.add_message(session, shard, found_id, who.role, text)
        except Exception as exc:
            return fail(exc)
        return JSONResponse({"status": "sent"}, status_code=201)

    return app


def _uuid(value: str, message: str):
    try:
        return uuid.UUID(value)
    except ValueError:
        return JSONResponse({"error": message}, status_code=400)


def _ticket(ticket: Ticket) -> dict:
    out = {
        "ticket_id": str(ticket.id),
        "shard_id": ticket.shard_id,
        "account_id": str(ticket.account_id),
        "description": ticket.description,
        "status": ticket.status,
        "camera": ticket.camera,
        "created_at": _time(ticket.created_at),
    }
    if ticket.tunnel_id is not None:
        out["tunnel_id"] = str(ticket.tunnel_id)
    return out


def _message(item) -> dict:
    return {"id": str(item.id), "author": item.author, "body": item.body, "created_at": _time(item.created_at)}


def _step(item) -> dict:
    return {"id": str(item.id), "plan_id": str(item.plan_id), "body": item.body, "status": item.status}


def _log(item) -> dict:
    return {
        "id": str(item.id),
        "actor": item.actor,
        "intent": item.intent,
        "phase": item.phase,
        "detail": item.detail or {},
        "created_at": _time(item.created_at),
    }


def _confirmation(item) -> dict:
    return {"role": item.role, "accept": item.accept, "created_at": _time(item.created_at)}


def _grant(grant) -> dict:
    if grant is None:
        return {}
    return {"grant_id": str(grant.id), "status": grant.status, "scope": grant.scope or "", "expires_at": _time(grant.expires_at)}


def _time(value):
    if value is None:
        return None
    return value.isoformat()
