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


def local_shard_count(factory) -> int:
    with factory() as session:
        from ticket_service.models import ShardDirectory

        return session.query(ShardDirectory).filter(ShardDirectory.status == "local").count()
