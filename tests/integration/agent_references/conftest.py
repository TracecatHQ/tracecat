"""Isolated PostgreSQL database for reference storage and concurrency tests."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.db.models import Base


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def reference_database():
    name = "test_references_" + uuid4().hex
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DB_CONFIG.base_url + name
    engine = create_engine(url.replace("+asyncpg", "+psycopg"), poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            Base.metadata.create_all(conn)
        yield url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()
