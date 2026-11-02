"""Verify session ancestry across a rolling application upgrade."""

import uuid

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


def test_session_ancestry_preserves_old_and_new_forks() -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "6d83f2a91c40"
    )
    assert revision is not None
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    source, spawner, existing, old_fork, new_fork, fresh_child, empty_fork = (
        uuid.uuid4() for _ in range(7)
    )
    workspace = uuid.uuid4()
    schema = f"test_session_ancestry_{uuid.uuid4().hex}"
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            # Isolate both the tables and trigger function from application data.
            # Roll back all DDL and data even if an assertion fails.
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(
                text("""
                CREATE TABLE agent_session (
                    id uuid PRIMARY KEY,
                    workspace_id uuid NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    sdk_session_id varchar(255),
                    parent_session_id uuid REFERENCES agent_session(id)
                        ON DELETE SET NULL
                )
            """)
            )
            connection.execute(
                text("""
                CREATE TABLE agent_session_history (
                    surrogate_id integer PRIMARY KEY,
                    session_id uuid NOT NULL,
                    created_at timestamptz NOT NULL
                )
            """)
            )
            connection.execute(
                text("""
                    INSERT INTO agent_session
                        (id, workspace_id, created_at, sdk_session_id, parent_session_id)
                    VALUES
                        (:source, :workspace, '2026-01-01', 'sdk-original', NULL),
                        (:spawner, :workspace, '2026-01-01', NULL, NULL),
                        (:existing, :workspace, '2026-01-03', NULL, :source)
                """),
                {
                    "source": source,
                    "workspace": workspace,
                    "spawner": spawner,
                    "existing": existing,
                },
            )
            connection.execute(
                text("""
                    INSERT INTO agent_session_history VALUES
                        (1, :source, '2026-01-02'), (2, :source, '2026-01-04')
                """),
                {"source": source},
            )
            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()

                def ancestry(
                    session_id: uuid.UUID,
                ) -> tuple[
                    uuid.UUID | None,
                    uuid.UUID | None,
                    uuid.UUID | None,
                    int | None,
                    str | None,
                ]:
                    return tuple(
                        connection.execute(
                            text("""
                                SELECT parent_session_id, forked_from_session_id,
                                    spawned_by_session_id, forked_from_history_id,
                                    forked_from_sdk_session_id
                                FROM agent_session WHERE id = :id
                            """),
                            {"id": session_id},
                        ).one()
                    )

                assert ancestry(existing) == (source, source, None, 1, "sdk-original")
                # The previous app knows only the legacy fork-source column.
                connection.execute(
                    text("""
                        INSERT INTO agent_session (id, workspace_id, parent_session_id)
                        VALUES (:old_fork, :workspace, :source),
                               (:empty_fork, :workspace, :spawner)
                    """),
                    {
                        "old_fork": old_fork,
                        "empty_fork": empty_fork,
                        "workspace": workspace,
                        "source": source,
                        "spawner": spawner,
                    },
                )
                # New app dual-writes the fork source, independently of its spawner.
                connection.execute(
                    text("""
                        INSERT INTO agent_session (
                            id, workspace_id, parent_session_id, forked_from_session_id,
                            spawned_by_session_id, forked_from_history_id,
                            forked_from_sdk_session_id
                        ) VALUES (:id, :workspace, :source, :source, :spawner, 1, 'sdk-captured')
                    """),
                    {
                        "id": new_fork,
                        "workspace": workspace,
                        "source": source,
                        "spawner": spawner,
                    },
                )
                connection.execute(
                    text("""
                        INSERT INTO agent_session (id, workspace_id, spawned_by_session_id)
                        VALUES (:id, :workspace, :spawner)
                    """),
                    {"id": fresh_child, "workspace": workspace, "spawner": spawner},
                )
                # Advancing the source must not change either captured snapshot.
                connection.execute(
                    text(
                        "UPDATE agent_session SET sdk_session_id = 'sdk-later' WHERE id = :id"
                    ),
                    {"id": source},
                )
                connection.execute(
                    text("""
                        INSERT INTO agent_session_history VALUES
                            (3, :source, '2026-01-05'), (4, :spawner, '2026-01-05')
                    """),
                    {"source": source, "spawner": spawner},
                )
                assert ancestry(old_fork) == (source, source, None, 2, "sdk-original")
                assert ancestry(new_fork) == (
                    source,
                    source,
                    spawner,
                    1,
                    "sdk-captured",
                )
                assert ancestry(fresh_child) == (None, None, spawner, None, None)
                assert ancestry(empty_fork) == (spawner, spawner, None, None, None)
                # Removing a spawner must not erase a different history source.
                connection.execute(
                    text("DELETE FROM agent_session WHERE id = :id"), {"id": spawner}
                )
                assert ancestry(new_fork) == (source, source, None, 1, "sdk-captured")
                assert ancestry(empty_fork) == (None, None, None, None, None)
                assert ancestry(fresh_child) == (None, None, None, None, None)

                with pytest.raises(NotImplementedError, match="roll-forward-only"):
                    revision.module.downgrade()
                columns = {
                    column["name"]
                    for column in inspect(connection).get_columns(
                        "agent_session", schema=schema
                    )
                }
                assert {
                    "spawned_by_session_id",
                    "forked_from_session_id",
                    "forked_from_history_id",
                    "forked_from_sdk_session_id",
                } <= columns
                assert ancestry(new_fork) == (source, source, None, 1, "sdk-captured")
                # All forks of the surviving source remain usable by the old app.
                assert set(
                    connection.execute(
                        text(
                            "SELECT id FROM agent_session WHERE parent_session_id = :source"
                        ),
                        {"source": source},
                    ).scalars()
                ) == {existing, old_fork, new_fork}
                # Application rollback keeps the upgraded schema and legacy trigger.
                rollback_fork = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO agent_session (id, workspace_id, parent_session_id) VALUES (:id, :workspace, :source)"
                    ),
                    {"id": rollback_fork, "workspace": workspace, "source": source},
                )
                assert ancestry(rollback_fork) == (source, source, None, 3, "sdk-later")
            transaction.rollback()
    finally:
        engine.dispose()
