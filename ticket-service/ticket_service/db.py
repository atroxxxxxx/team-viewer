from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def engine_from_url(url: str):
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return create_engine(url, pool_pre_ping=True)


def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def local_shard_count(factory) -> int:
    with factory() as session:
        from ticket_service.models import ShardDirectory

        return session.query(ShardDirectory).filter(ShardDirectory.status == "local").count()
