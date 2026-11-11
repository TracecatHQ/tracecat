"""Verify pending approvals persisted one row at a time are grouped into batches."""

import uuid
from datetime import UTC, datetime, timedelta

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


def test_group_pending_approval_batches() -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "03d80dfe8c1d"
    )
    assert revision is not None
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    schema = f"test_approval_batches_{uuid.uuid4().hex}"
    workspace_id, session_id, other_session_id = (uuid.uuid4() for _ in range(3))
    t0 = datetime(2026, 11, 10, 12, tzinfo=UTC)
    rows = {
        # One legacy pause: rows committed a few milliseconds apart.
        "a": (session_id, "PENDING", t0),
        "b": (session_id, "PENDING", t0 + timedelta(milliseconds=8)),
        "c": (session_id, "PENDING", t0 + timedelta(milliseconds=15)),
        # A decided row of an earlier pause stays as it was.
        "old": (session_id, "APPROVED", t0 - timedelta(milliseconds=5)),
        # Pending rows an hour apart are separate pauses.
        "stale": (other_session_id, "PENDING", t0),
        "fresh": (other_session_id, "PENDING", t0 + timedelta(hours=1)),
        "fresh2": (
            other_session_id,
            "PENDING",
            t0 + timedelta(hours=1, milliseconds=4),
        ),
    }
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(
                text("""
                CREATE TABLE approval (
                    id text PRIMARY KEY,
                    workspace_id uuid NOT NULL,
                    session_id uuid,
                    status text NOT NULL,
                    created_at timestamptz NOT NULL
                )
            """)
            )
            for row_id, (session, status, created_at) in rows.items():
                connection.execute(
                    text("""
                    INSERT INTO approval
                    VALUES (:id, :workspace, :session, :status, :created_at)
                """),
                    {
                        "id": row_id,
                        "workspace": workspace_id,
                        "session": session,
                        "status": status,
                        "created_at": created_at,
                    },
                )
            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()
            created = dict(
                connection.execute(text("SELECT id, created_at FROM approval"))
                .tuples()
                .all()
            )
            transaction.rollback()
    finally:
        engine.dispose()

    assert created["a"] == created["b"] == created["c"] == t0
    assert created["old"] == t0 - timedelta(milliseconds=5)
    assert created["stale"] == t0
    assert created["fresh"] == created["fresh2"] == t0 + timedelta(hours=1)
