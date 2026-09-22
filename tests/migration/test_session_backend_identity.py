"""Exercise backend identity expansion and guarded rollback in PostgreSQL."""

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


def test_backend_identity_preserves_harness_and_old_writes() -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "acbacbf8ef53"
    )
    assert revision is not None
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            # A temporary table isolates this additive column contract from all
            # other application migrations and contains no user data.
            connection.execute(
                text(
                    "CREATE TEMP TABLE agent_session (id integer PRIMARY KEY, harness_type varchar(50))"
                )
            )
            connection.execute(
                text("INSERT INTO agent_session VALUES (1, 'claude_code'), (2, NULL)")
            )
            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()
                assert connection.execute(
                    text(
                        "SELECT backend_id, harness_type FROM agent_session ORDER BY id"
                    )
                ).all() == [("oss", "claude_code"), ("oss", None)]
                # Old application versions omit backend_id entirely.
                connection.execute(
                    text(
                        "INSERT INTO agent_session (id, harness_type) VALUES (3, 'claude_code')"
                    )
                )
                assert (
                    connection.scalar(
                        text("SELECT backend_id FROM agent_session WHERE id = 3")
                    )
                    == "oss"
                )
                connection.execute(
                    text(
                        "INSERT INTO agent_session VALUES (4, 'synthetic_harness', 'ee')"
                    )
                )
                with pytest.raises(RuntimeError, match="non-oss sessions"):
                    revision.module.downgrade()
                assert (
                    connection.scalar(
                        text("SELECT backend_id FROM agent_session WHERE id = 4")
                    )
                    == "ee"
                )
                connection.execute(text("DELETE FROM agent_session WHERE id = 4"))
                revision.module.downgrade()
                assert "backend_id" not in {
                    column["name"]
                    for column in inspect(connection).get_columns("agent_session")
                }
                assert (
                    connection.scalar(text("SELECT count(*) FROM agent_session")) == 3
                )
    finally:
        engine.dispose()
