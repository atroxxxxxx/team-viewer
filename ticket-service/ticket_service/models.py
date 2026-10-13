import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ShardDirectory(Base):
    __tablename__ = "shard_directory"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    status: Mapped[str] = mapped_column(Text)


class TicketIndex(Base):
    __tablename__ = "ticket_index"
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    shard_id: Mapped[int] = mapped_column(SmallInteger)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))


class Ticket(Base):
    __tablename__ = "tickets"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    camera: Mapped[bool] = mapped_column(Boolean)
    assignee: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    token_hash: Mapped[str] = mapped_column(Text)
    tunnel_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ActionLog(Base):
    __tablename__ = "action_log"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    actor: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(Text)
    phase: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Problem(Base):
    __tablename__ = "problems"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    description: Mapped[str] = mapped_column(Text)


class Plan(Base):
    __tablename__ = "plans"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    problem_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PlanStep(Base):
    __tablename__ = "plan_steps"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)


class Observation(Base):
    __tablename__ = "observations"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    step_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text)


class ControlGrant(Base):
    __tablename__ = "control_grants"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    author: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ChatMember(Base):
    __tablename__ = "chat_members"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    member_id: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Confirmation(Base):
    __tablename__ = "confirmations"
    shard_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(Text)
    accept: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
