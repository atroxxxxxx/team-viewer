from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def engine_from_url(url: str):
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return create_engine(url, pool_pre_ping=True)


def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def ensure_step_order(factory) -> None:
    """Старые базы не хранили порядок шагов. Номер нужен, чтобы план не сортировался по тексту."""
    with factory() as session:
        session.execute(text(
            "ALTER TABLE plan_steps ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT -1"
        ))
        session.execute(text(
            """
            UPDATE plan_steps AS step
            SET position = ranked.rn
            FROM (
                SELECT shard_id, id,
                       (row_number() OVER (PARTITION BY shard_id, ticket_id ORDER BY ctid) - 1)::integer AS rn
                FROM plan_steps
                WHERE position < 0
            ) AS ranked
            WHERE step.shard_id = ranked.shard_id AND step.id = ranked.id
            """
        ))
        session.commit()


def ensure_tunnel_access(factory) -> None:
    with factory.begin() as session:
        session.execute(text("""
            ALTER TABLE tickets
                ADD COLUMN IF NOT EXISTS client_media_secret text,
                ADD COLUMN IF NOT EXISTS operator_media_secret text
        """))
        session.execute(text("""
            WITH latest AS (
                SELECT DISTINCT ON (shard_id, ticket_id) shard_id, ticket_id, detail
                FROM action_log
                WHERE intent = 'tunnel_access' AND detail ? 'client_secret'
                ORDER BY shard_id, ticket_id, created_at DESC, id DESC
            )
            UPDATE tickets AS ticket
            SET client_media_secret = latest.detail ->> 'client_secret',
                operator_media_secret = latest.detail ->> 'operator_secret'
            FROM latest
            WHERE ticket.shard_id = latest.shard_id AND ticket.id = latest.ticket_id
              AND ticket.tunnel_id IS NOT NULL AND ticket.status NOT IN ('queued', 'resolved', 'closed')
              AND ticket.client_media_secret IS NULL AND ticket.operator_media_secret IS NULL
        """))
        session.execute(text("""
            UPDATE action_log
            SET detail = detail - 'client_secret' - 'operator_secret'
            WHERE intent = 'tunnel_access' AND (detail ? 'client_secret' OR detail ? 'operator_secret')
        """))


def local_shard_count(factory) -> int:
    with factory() as session:
        from ticket_service.models import ShardDirectory

        return session.query(ShardDirectory).filter(ShardDirectory.status == "local").count()
