import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env.dev")


@pytest.fixture(scope="session")
def postgres_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    # Only a unique test schema is created/dropped; never drop the database.
    schema = "ticket_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
    try:
        with engine.begin() as connection:
            connection.connection.driver_connection.execute(
                (ROOT / "postgres-redis/init/001_schema.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def factory(postgres_engine):
    with postgres_engine.begin() as connection:
        connection.execute(text("TRUNCATE tickets, ticket_index CASCADE"))
    return sessionmaker(bind=postgres_engine, expire_on_commit=False)
