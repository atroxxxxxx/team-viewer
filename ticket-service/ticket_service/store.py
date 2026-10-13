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


def claim(session: Session, shard_id: int, ticket_id: uuid.UUID, operator_id: uuid.UUID, tunnel_id: uuid.UUID) -> None:
    result = session.execute(
        update(Ticket)
        .where(Ticket.shard_id == shard_id, Ticket.id == ticket_id, Ticket.status == "queued")
        .values(status="assigned", assignee=operator_id, tunnel_id=tunnel_id)
    )
    if result.rowcount == 0:
        raise Conflict()
    session.add(ChatMember(shard_id=shard_id, ticket_id=ticket_id, member_id=str(operator_id), role="operator"))
    _action(session, shard_id, ticket_id, str(operator_id), "claim", "applied")


def add_message(session: Session, shard_id: int, ticket_id: uuid.UUID, author: str, body: str) -> None:
    session.add(ChatMessage(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, author=author, body=body))
    _action(session, shard_id, ticket_id, author, "chat", "applied")


def messages(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[ChatMessage]:
    return list(
        session.scalars(
            select(ChatMessage).where(ChatMessage.shard_id == shard_id, ChatMessage.ticket_id == ticket_id).order_by(ChatMessage.created_at)
        )
    )


def request_control(session: Session, shard_id: int, ticket_id: uuid.UUID, intent: str) -> uuid.UUID:
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
    grant = session.get(ControlGrant, (shard_id, grant_id))
    if grant is None or grant.ticket_id != ticket_id or grant.status != "pending":
        raise Conflict()
    grant.status = "active" if allow else "denied"
    grant.scope = scope
    grant.expires_at = datetime.now(timezone.utc) + ttl if allow else None
    ticket = get_ticket(session, shard_id, ticket_id)
    ticket.status = "remote_control" if allow else "guided"
    _action(session, shard_id, ticket_id, "client", "control_decision", "applied" if allow else "rejected_by_client")


def grants_for(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[ControlGrant]:
    return list(session.scalars(select(ControlGrant).where(ControlGrant.shard_id == shard_id, ControlGrant.ticket_id == ticket_id)))


def grant_for_input(session: Session, shard_id: int, ticket_id: uuid.UUID, grant_id: uuid.UUID, now: datetime) -> ControlGrant:
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
    plan_id = uuid.uuid4()
    session.add(Plan(shard_id=shard_id, id=plan_id, ticket_id=ticket_id, problem_id=problem_id))
    # Шаг ссылается на план. План должен уже быть в транзакции, иначе внешний ключ не видит его.
    session.flush()
    for body in steps:
        session.add(PlanStep(shard_id=shard_id, id=uuid.uuid4(), plan_id=plan_id, ticket_id=ticket_id, body=body, status="todo"))
    session.execute(
        update(Ticket)
        .where(Ticket.shard_id == shard_id, Ticket.id == ticket_id, Ticket.status != "remote_control")
        .values(status="guided")
    )
    _action(session, shard_id, ticket_id, "operator", "create_plan", "applied")
    return plan_id


def steps(session: Session, shard_id: int, ticket_id: uuid.UUID) -> list[PlanStep]:
    return list(
        session.scalars(select(PlanStep).where(PlanStep.shard_id == shard_id, PlanStep.ticket_id == ticket_id).order_by(PlanStep.body))
    )


def add_observation(session: Session, shard_id: int, ticket_id: uuid.UUID, step_id: uuid.UUID, body: str) -> None:
    step = session.get(PlanStep, (shard_id, step_id))
    if step is None or step.ticket_id != ticket_id:
        raise NotFound()
    session.add(Observation(shard_id=shard_id, id=uuid.uuid4(), step_id=step_id, ticket_id=ticket_id, body=body))


def complete_step(session: Session, shard_id: int, ticket_id: uuid.UUID, step_id: uuid.UUID, seen: bool, body: str) -> None:
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
    step = session.get(PlanStep, (shard_id, step_id))
    if step is None or step.ticket_id != ticket_id or step.status != "todo":
        raise Conflict()
    plan_id = step.plan_id
    step.status = "cancelled"
    session.add(PlanStep(shard_id=shard_id, id=uuid.uuid4(), plan_id=plan_id, ticket_id=ticket_id, body=body, status="todo"))


def add_problem(session: Session, shard_id: int, ticket_id: uuid.UUID, parent_step: uuid.UUID, description: str, plan_steps: list[str]) -> uuid.UUID:
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
    session.add(Confirmation(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, role=role, accept=accept))
    session.flush()
    ticket = get_ticket(session, shard_id, ticket_id)
    if not accept:
        active, _ = pick_controls(grants_for(session, shard_id, ticket_id), now)
        ticket.status = "remote_control" if active is not None else "guided"
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
    status = "resolved" if operator_yes and client_yes else "awaiting_client"
    if status == "resolved":
        session.execute(
            update(ChatMember)
            .where(
                ChatMember.shard_id == shard_id,
                ChatMember.ticket_id == ticket_id,
                ChatMember.role == "operator",
                ChatMember.left_at.is_(None),
            )
            .values(left_at=func.now())
        )
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


def _action(session: Session, shard_id: int, ticket_id: uuid.UUID, actor: str, intent: str, phase: str) -> None:
    session.add(ActionLog(shard_id=shard_id, id=uuid.uuid4(), ticket_id=ticket_id, actor=actor, intent=intent, phase=phase, detail={}))
