import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ticket_service.grants import pick_controls, usable
from ticket_service.models import (
    ActionLog,
    ChatMember,
    ChatMessage,
    Confirmation,
    ControlGrant,
    Observation,
    Plan,
    PlanStep,
    Problem,
    Ticket,
    TicketIndex,
)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def create_ticket(session: Session, ticket: Ticket) -> None:
    session.add(TicketIndex(ticket_id=ticket.id, shard_id=ticket.shard_id, account_id=ticket.account_id))
    session.add(ticket)
    # Журнал и участник ссылаются на заявку. Без связи relationship()
    # SQLAlchemy пишет таблицы по имени класса, и action_log уходит раньше tickets.
    session.flush()
    session.add(ChatMember(shard_id=ticket.shard_id, ticket_id=ticket.id, member_id=str(ticket.account_id), role="client"))
    _action(session, ticket.shard_id, ticket.id, str(ticket.account_id), "create_ticket", "applied")


def list_queued(session: Session, limit: int = 50) -> list[Ticket]:
    return list(session.scalars(select(Ticket).where(Ticket.status == "queued").order_by(Ticket.created_at).limit(limit)))


def token_matches(session: Session, shard_id: int, ticket_id: uuid.UUID, token_hash: str) -> bool:
    found = session.scalar(
        select(Ticket.id).where(Ticket.shard_id == shard_id, Ticket.id == ticket_id, Ticket.token_hash == token_hash)
    )
    return found is not None


def locate(session: Session, ticket_id: uuid.UUID) -> int:
    shard_id = session.scalar(select(TicketIndex.shard_id).where(TicketIndex.ticket_id == ticket_id))
    if shard_id is None:
        raise NotFound()
    return int(shard_id)


def get_ticket(session: Session, shard_id: int, ticket_id: uuid.UUID) -> Ticket:
    ticket = session.get(Ticket, (shard_id, ticket_id))
    if ticket is None:
        raise NotFound()
    return ticket


def lock_ticket(session: Session, shard_id: int, ticket_id: uuid.UUID) -> Ticket:
    # Refresh an already loaded row after waiting for another transaction.
    ticket = session.scalar(
        select(Ticket)
        .where(Ticket.shard_id == shard_id, Ticket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ticket is None:
        raise NotFound()
    if ticket.status in ("resolved", "closed"):
        raise Conflict()
    return ticket


OPERATOR_PRESENCE = "operator_presence"
OPERATOR_HANDOFF = "operator_handoff"
OPERATOR_WAIT = timedelta(seconds=60)
CLIENT_SWITCH = "Переключение на другого оператора"
NORMAL_HANDOFF = "Предыдущий оператор штатно прекратил работу"
CRASH_HANDOFF = "Предыдущий оператор отключился: пропала связь с панелью"


def claim(session: Session, shard_id: int, ticket_id: uuid.UUID, operator_id: uuid.UUID, tunnel_id: uuid.UUID, now: datetime | None = None) -> None:
    result = session.execute(
        update(Ticket)
        .where(Ticket.shard_id == shard_id, Ticket.id == ticket_id, Ticket.status == "queued")
        .values(status="assigned", assignee=operator_id, tunnel_id=tunnel_id)
    )
    if result.rowcount == 0:
        raise Conflict()
    _join_operator(session, shard_id, ticket_id, operator_id)
    _action(session, shard_id, ticket_id, str(operator_id), "claim", "applied")
    touch_operator(session, shard_id, ticket_id, operator_id, now or datetime.now(timezone.utc))


def touch_operator(session: Session, shard_id: int, ticket_id: uuid.UUID, operator_id: uuid.UUID, now: datetime) -> None:
    row = session.scalar(
        select(ActionLog).where(
            ActionLog.shard_id == shard_id,
            ActionLog.ticket_id == ticket_id,
            ActionLog.intent == OPERATOR_PRESENCE,
        ).limit(1)
    )
    if row is None:
        session.add(ActionLog(
            shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, actor=str(operator_id),
            intent=OPERATOR_PRESENCE, phase="applied", detail={}, created_at=now,
        ))
    else:
        row.actor = str(operator_id)
        row.created_at = now


TUNNEL_ACCESS = "tunnel_access"


def save_tunnel_access(session: Session, shard_id: int, ticket_id: uuid.UUID, client_secret: str, operator_secret: str) -> None:
    session.add(ActionLog(
        shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, actor="сервис",
        intent=TUNNEL_ACCESS, phase="applied",
        detail={"client_secret": client_secret, "operator_secret": operator_secret},
    ))


def media_secret(session: Session, shard_id: int, ticket_id: uuid.UUID, role: str) -> str:
    detail = session.scalar(
        select(ActionLog.detail).where(
            ActionLog.shard_id == shard_id,
            ActionLog.ticket_id == ticket_id,
            ActionLog.intent == TUNNEL_ACCESS,
        ).order_by(ActionLog.created_at.desc()).limit(1)
    )
    if not isinstance(detail, dict):
        return ""
    value = str(detail.get("client_secret" if role == "client" else "operator_secret") or "")
    return value if len(value) == 64 else ""


def handoff_note(session: Session, shard_id: int, ticket_id: uuid.UUID) -> dict:
    detail = session.scalar(
        select(ActionLog.detail).where(
            ActionLog.shard_id == shard_id,
            ActionLog.ticket_id == ticket_id,
            ActionLog.intent == OPERATOR_HANDOFF,
        ).order_by(ActionLog.created_at.desc()).limit(1)
    )
    return detail if isinstance(detail, dict) else {}


def release_operator(session: Session, shard_id: int, ticket_id: uuid.UUID, now: datetime, kind: str) -> bool:
    try:
        ticket = lock_ticket(session, shard_id, ticket_id)
    except Conflict:
        return False
    if ticket.status == "queued":
        return False
    _clear_operator(session, shard_id, ticket, now)
    ticket.status = "queued"
    ticket.assignee = None
    ticket.tunnel_id = None
    session.add(ActionLog(
        shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, actor="operator",
        intent=OPERATOR_HANDOFF, phase="applied",
        detail={
            "kind": kind,
            "operator": CRASH_HANDOFF if kind == "crash" else NORMAL_HANDOFF,
            "client": CLIENT_SWITCH,
        },
    ))
    return True


def expire_operators(session: Session, now: datetime) -> list[uuid.UUID]:
    tickets = list(session.scalars(select(Ticket).where(Ticket.status.notin_(("queued", "resolved", "closed")))))
    released = []
    for ticket in tickets:
        if _operator_silent(session, ticket, now) and release_operator(session, ticket.shard_id, ticket.id, now, "crash"):
            released.append(ticket.id)
    return released


def _operator_silent(session: Session, ticket: Ticket, now: datetime) -> bool:
    if ticket.assignee is None:
        return False
    seen = session.scalar(
        select(ActionLog.created_at).where(
            ActionLog.shard_id == ticket.shard_id,
            ActionLog.ticket_id == ticket.id,
            ActionLog.intent == OPERATOR_PRESENCE,
        ).order_by(ActionLog.created_at.desc()).limit(1)
    )
    return seen is not None and now - seen >= OPERATOR_WAIT


def _join_operator(session: Session, shard_id: int, ticket_id: uuid.UUID, operator_id: uuid.UUID) -> None:
    member_id = str(operator_id)
    member = session.scalar(
        select(ChatMember).where(
            ChatMember.shard_id == shard_id,
            ChatMember.ticket_id == ticket_id,
            ChatMember.member_id == member_id,
        )
    )
    if member is None:
        session.add(ChatMember(shard_id=shard_id, ticket_id=ticket_id, member_id=member_id, role="operator"))
    else:
        member.left_at = None
        member.role = "operator"


def _clear_operator(session: Session, shard_id: int, ticket: Ticket, now: datetime) -> None:
    session.execute(
        update(ControlGrant)
        .where(
            ControlGrant.shard_id == shard_id,
            ControlGrant.ticket_id == ticket.id,
            ControlGrant.status.in_(("active", "pending")),
        )
        .values(status="revoked", scope="", expires_at=now)
    )
    session.execute(
        update(ActionLog)
        .where(
            ActionLog.shard_id == shard_id,
            ActionLog.ticket_id == ticket.id,
            ActionLog.phase == "proposed",
            ActionLog.detail["type"].astext.in_(("pointer", "key", "text")),
        )
        .values(phase="cancelled")
    )
    session.execute(
        update(ChatMember)
        .where(
            ChatMember.shard_id == shard_id,
            ChatMember.ticket_id == ticket.id,
            ChatMember.role == "operator",
            ChatMember.left_at.is_(None),
        )
        .values(left_at=func.now())
    )


def add_message(session: Session, shard_id: int, ticket_id: uuid.UUID, author: str, body: str) -> None:
    lock_ticket(session, shard_id, ticket_id)
    session.add(ChatMessage(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, author=author, body=body))
    _action(session, shard_id, ticket_id, author, "chat", "applied")


def messages(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[ChatMessage]:
    return list(
        session.scalars(
            select(ChatMessage).where(ChatMessage.shard_id == shard_id, ChatMessage.ticket_id == ticket_id).order_by(ChatMessage.created_at)
        )
    )


def request_control(session: Session, shard_id: int, ticket_id: uuid.UUID, intent: str) -> uuid.UUID:
    lock_ticket(session, shard_id, ticket_id)
    result = session.execute(
        update(Ticket)
        .where(Ticket.shard_id == shard_id, Ticket.id == ticket_id, Ticket.status.in_(("assigned", "guided", "remote_control")))
        .values(status="awaiting_permission")
    )
    if result.rowcount == 0:
        raise Conflict()
    grant_id = uuid.uuid4()
    session.add(ControlGrant(shard_id=shard_id, id=grant_id, ticket_id=ticket_id, status="pending", scope=""))
    _action(session, shard_id, ticket_id, "operator", intent, "proposed")
    return grant_id


def decide_control(session: Session, shard_id: int, ticket_id: uuid.UUID, grant_id: uuid.UUID, allow: bool, scope: str, ttl: timedelta) -> None:
    ticket = lock_ticket(session, shard_id, ticket_id)
    grant = session.get(ControlGrant, (shard_id, grant_id))
    if grant is None or grant.ticket_id != ticket_id or grant.status != "pending":
        raise Conflict()
    grant.status = "active" if allow else "denied"
    grant.scope = scope
    grant.expires_at = datetime.now(timezone.utc) + ttl if allow else None
    ticket.status = "remote_control" if allow else "guided"
    _action(session, shard_id, ticket_id, "client", "control_decision", "applied" if allow else "rejected_by_client")


def grants_for(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[ControlGrant]:
    return list(session.scalars(select(ControlGrant).where(ControlGrant.shard_id == shard_id, ControlGrant.ticket_id == ticket_id)))


def grant_for_input(session: Session, shard_id: int, ticket_id: uuid.UUID, grant_id: uuid.UUID) -> ControlGrant:
    lock_ticket(session, shard_id, ticket_id)
    now = datetime.now(timezone.utc)
    grant = session.get(ControlGrant, (shard_id, grant_id))
    if grant is None or grant.ticket_id != ticket_id or not usable(grant, now):
        raise Conflict()
    return grant


def propose_input(session: Session, shard_id: int, ticket_id: uuid.UUID, grant: ControlGrant, intent: str, detail: dict) -> uuid.UUID:
    action_id = uuid.uuid4()
    session.add(
        ActionLog(
            shard_id=shard_id,
            id=action_id,
            ticket_id=ticket_id,
            actor="operator",
            intent=intent,
            phase="proposed",
            detail=detail,
        )
    )
    if grant.ticket_id != ticket_id:
        raise Conflict()
    return action_id


def finish_input(session: Session, shard_id: int, ticket_id: uuid.UUID, action_id: uuid.UUID, phase: str, observation: str) -> None:
    lock_ticket(session, shard_id, ticket_id)
    result = session.execute(
        update(ActionLog)
        .where(ActionLog.shard_id == shard_id, ActionLog.id == action_id, ActionLog.ticket_id == ticket_id, ActionLog.phase == "proposed")
        .values(phase=phase)
    )
    if result.rowcount == 0:
        raise NotFound()
    if observation:
        session.add(ChatMessage(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, author="client", body=observation))


def log_blocked(session: Session, shard_id: int, ticket_id: uuid.UUID, actor: str, intent: str, detail: dict) -> None:
    lock_ticket(session, shard_id, ticket_id)
    session.add(
        ActionLog(
            shard_id=shard_id,
            id=uuid.uuid4(),
            ticket_id=ticket_id,
            actor=actor,
            intent=intent,
            phase="blocked_by_policy",
            detail=detail,
        )
    )


def create_plan(session: Session, shard_id: int, ticket_id: uuid.UUID, problem_id: uuid.UUID | None, steps: list[str]) -> uuid.UUID:
    lock_ticket(session, shard_id, ticket_id)
    plan_id = uuid.uuid4()
    session.add(Plan(shard_id=shard_id, id=plan_id, ticket_id=ticket_id, problem_id=problem_id))
    # Шаг ссылается на план. План должен уже быть в транзакции, иначе внешний ключ не видит его.
    session.flush()
    position = _next_position(session, shard_id, ticket_id)
    for body in steps:
        session.add(PlanStep(
            shard_id=shard_id, id=uuid.uuid4(), plan_id=plan_id, ticket_id=ticket_id,
            body=body, status="todo", position=position,
        ))
        position += 1
    session.execute(
        update(Ticket)
        .where(Ticket.shard_id == shard_id, Ticket.id == ticket_id, Ticket.status != "remote_control")
        .values(status="guided")
    )
    _action(session, shard_id, ticket_id, "operator", "create_plan", "applied")
    return plan_id


def steps(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[PlanStep]:
    return list(
        session.scalars(
            select(PlanStep)
            .where(PlanStep.shard_id == shard_id, PlanStep.ticket_id == ticket_id)
            .order_by(PlanStep.position, PlanStep.status == "cancelled", PlanStep.id)
        )
    )


def observations(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[Observation]:
    return list(
        session.scalars(
            select(Observation)
            .where(Observation.shard_id == shard_id, Observation.ticket_id == ticket_id)
            .order_by(Observation.created_at, Observation.id)
        )
    )


def add_observation(session: Session, shard_id: int, ticket_id: uuid.UUID, step_id: uuid.UUID, body: str, announce: bool = False) -> None:
    lock_ticket(session, shard_id, ticket_id)
    step = session.get(PlanStep, (shard_id, step_id))
    if step is None or step.ticket_id != ticket_id:
        raise NotFound()
    session.add(Observation(shard_id=shard_id, id=uuid.uuid4(), step_id=step_id, ticket_id=ticket_id, body=body))
    if announce:
        session.add(ChatMessage(
            shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, author="client",
            body=f"По шагу «{step.body}»: {body}",
        ))


def complete_step(session: Session, shard_id: int, ticket_id: uuid.UUID, step_id: uuid.UUID, seen: bool, body: str) -> None:
    lock_ticket(session, shard_id, ticket_id)
    if seen:
        add_observation(session, shard_id, ticket_id, step_id, "вижу на экране: " + body)
        session.flush()
    count = session.scalar(
        select(func.count()).select_from(Observation).where(
            Observation.shard_id == shard_id, Observation.step_id == step_id, Observation.ticket_id == ticket_id
        )
    )
    if not count:
        raise Conflict()
    result = session.execute(
        update(PlanStep)
        .where(PlanStep.shard_id == shard_id, PlanStep.id == step_id, PlanStep.ticket_id == ticket_id, PlanStep.status == "todo")
        .values(status="done")
    )
    if result.rowcount == 0:
        raise Conflict()


def revise_step(session: Session, shard_id: int, ticket_id: uuid.UUID, step_id: uuid.UUID, body: str) -> None:
    lock_ticket(session, shard_id, ticket_id)
    step = session.get(PlanStep, (shard_id, step_id))
    if step is None or step.ticket_id != ticket_id or step.status != "todo":
        raise Conflict()
    plan_id = step.plan_id
    step.status = "cancelled"
    session.add(PlanStep(
        shard_id=shard_id, id=uuid.uuid4(), plan_id=plan_id, ticket_id=ticket_id,
        body=body, status="todo", position=step.position,
    ))


def add_problem(session: Session, shard_id: int, ticket_id: uuid.UUID, parent_step: uuid.UUID, description: str, plan_steps: list[str]) -> uuid.UUID:
    lock_ticket(session, shard_id, ticket_id)
    problem_id = uuid.uuid4()
    session.add(Problem(shard_id=shard_id, id=problem_id, ticket_id=ticket_id, parent_step_id=parent_step, description=description))
    result = session.execute(
        update(PlanStep)
        .where(PlanStep.shard_id == shard_id, PlanStep.id == parent_step, PlanStep.ticket_id == ticket_id)
        .values(status="blocked")
    )
    if result.rowcount == 0:
        raise NotFound()
    session.flush()
    create_plan(session, shard_id, ticket_id, problem_id, plan_steps)
    return problem_id


def confirm(session: Session, shard_id: int, ticket_id: uuid.UUID, role: str, accept: bool, now: datetime) -> str:
    ticket = lock_ticket(session, shard_id, ticket_id)
    session.add(Confirmation(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, role=role, accept=accept))
    session.flush()
    if not accept:
        active, _ = pick_controls(grants_for(session, shard_id, ticket_id), now)
        ticket.status = "remote_control" if active is not None and usable(active, now) else "guided"
        return ticket.status
    open_steps = session.scalar(
        select(func.count()).select_from(PlanStep).where(
            PlanStep.shard_id == shard_id,
            PlanStep.ticket_id == ticket_id,
            PlanStep.status.in_(("todo", "blocked")),
        )
    )
    if open_steps:
        raise Conflict()
    operator_yes = _latest_accept(session, shard_id, ticket_id, "operator")
    client_yes = _latest_accept(session, shard_id, ticket_id, "client")
    if operator_yes and client_yes:
        return _end_ticket(session, shard_id, ticket, now, "resolved")
    ticket.status = "awaiting_client"
    return ticket.status


def close_resolved(session: Session, shard_id: int, ticket_id: uuid.UUID, role: str, now: datetime) -> str:
    """Клиент считает проблему решённой и закрывает заявку, даже если шаги ещё открыты."""
    ticket = lock_ticket(session, shard_id, ticket_id)
    session.add(Confirmation(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, role=role, accept=True))
    session.flush()
    return _end_ticket(session, shard_id, ticket, now, "resolved")


def close_unresolved(session: Session, shard_id: int, ticket_id: uuid.UUID, role: str, review: str, now: datetime) -> str:
    """Клиент закрывает заявку отзывом, не дожидаясь согласия оператора и незакрытых шагов."""
    ticket = lock_ticket(session, shard_id, ticket_id)
    session.add(Confirmation(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, role=role, accept=False))
    session.add(ChatMessage(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, author=role, body=review))
    session.add(
        ActionLog(
            shard_id=shard_id,
            id=uuid.uuid4(),
            ticket_id=ticket_id,
            actor=role,
            intent="unresolved_review",
            phase="applied",
            detail={"body": review},
        )
    )
    session.flush()
    return _end_ticket(session, shard_id, ticket, now, "closed")


def _end_ticket(session: Session, shard_id: int, ticket: Ticket, now: datetime, status: str) -> str:
    _clear_operator(session, shard_id, ticket, now)
    ticket.status = status
    return status


def pending_inputs(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[ActionLog]:
    rows = session.scalars(
        select(ActionLog)
        .where(ActionLog.shard_id == shard_id, ActionLog.ticket_id == ticket_id, ActionLog.phase == "proposed")
        .order_by(ActionLog.created_at)
    )
    return [row for row in rows if isinstance(row.detail, dict) and row.detail.get("type") in {"pointer", "key", "text"}]


def confirmations(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[Confirmation]:
    return list(
        session.scalars(
            select(Confirmation).where(Confirmation.shard_id == shard_id, Confirmation.ticket_id == ticket_id).order_by(Confirmation.created_at)
        )
    )


def _latest_accept(session: Session, shard_id: int, ticket_id: uuid.UUID, role: str) -> bool:
    value = session.scalar(
        select(Confirmation.accept)
        .where(Confirmation.shard_id == shard_id, Confirmation.ticket_id == ticket_id, Confirmation.role == role)
        .order_by(Confirmation.created_at.desc())
        .limit(1)
    )
    return bool(value)


PRESENCE = "client_presence"
CLIENT_WAIT = timedelta(seconds=60)


def touch_client(session: Session, shard_id: int, ticket_id: uuid.UUID, now: datetime) -> None:
    ticket = session.get(Ticket, (shard_id, ticket_id))
    if ticket is None or ticket.status in ("resolved", "closed"):
        return
    row = session.scalar(
        select(ActionLog).where(
            ActionLog.shard_id == shard_id,
            ActionLog.ticket_id == ticket_id,
            ActionLog.intent == PRESENCE,
        ).limit(1)
    )
    if row is None:
        session.add(ActionLog(
            shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, actor="client",
            intent=PRESENCE, phase="applied", detail={}, created_at=now,
        ))
    else:
        row.created_at = now


def expire_ticket(session: Session, shard_id: int, ticket_id: uuid.UUID, now: datetime) -> bool:
    ticket = session.get(Ticket, (shard_id, ticket_id))
    if ticket is None or ticket.status in ("resolved", "closed") or not _client_gone(session, shard_id, ticket, now):
        return False
    try:
        ticket = lock_ticket(session, shard_id, ticket_id)
    except Conflict:
        return False
    session.add(ChatMessage(
        shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, author="сервис",
        body="Заявка закрыта: клиент не ответил или отключился",
    ))
    session.add(Confirmation(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, role="system", accept=False))
    _end_ticket(session, shard_id, ticket, now, "closed")
    return True


def expire_open(session: Session, now: datetime) -> list[uuid.UUID]:
    tickets = list(session.scalars(select(Ticket).where(Ticket.status.notin_(("resolved", "closed")))))
    return [ticket.id for ticket in tickets if expire_ticket(session, ticket.shard_id, ticket.id, now)]


def _client_gone(session: Session, shard_id: int, ticket: Ticket, now: datetime) -> bool:
    if ticket.status == "awaiting_client":
        started = session.scalar(
            select(Confirmation.created_at)
            .where(
                Confirmation.shard_id == shard_id,
                Confirmation.ticket_id == ticket.id,
                Confirmation.accept.is_(True),
            )
            .order_by(Confirmation.created_at.desc())
            .limit(1)
        )
        if started is not None and now - started >= CLIENT_WAIT:
            return True
    seen = session.scalar(
        select(ActionLog.created_at)
        .where(
            ActionLog.shard_id == shard_id,
            ActionLog.ticket_id == ticket.id,
            ActionLog.intent == PRESENCE,
        )
        .order_by(ActionLog.created_at.desc())
        .limit(1)
    )
    return seen is not None and now - seen >= CLIENT_WAIT


def _next_position(session: Session, shard_id: int, ticket_id: uuid.UUID) -> int:
    current = session.scalar(
        select(func.max(PlanStep.position)).where(
            PlanStep.shard_id == shard_id,
            PlanStep.ticket_id == ticket_id,
            PlanStep.position >= 0,
        )
    )
    return 0 if current is None else int(current) + 1


def _action(session: Session, shard_id: int, ticket_id: uuid.UUID, actor: str, intent: str, phase: str) -> None:
    session.add(ActionLog(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, actor=actor, intent=intent, phase=phase, detail={}))
