"""Verify preset reasoning levels across deploy, app rollback, and re-upgrade.

The previous app version reads and writes only ``enable_thinking``; the new one
writes ``reasoning_effort``. While both can write, every row must mean the same
thing to both versions: ``enable_thinking`` is false exactly when
``reasoning_effort`` is ``"off"``, and an older write that turns thinking back
on clears ``"off"`` without discarding any other level.
"""

import uuid

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

_TABLES = ("agent_preset", "agent_preset_version")


def _state(
    connection: Connection, table: str, row_id: uuid.UUID
) -> tuple[str | None, bool]:
    reasoning_effort, enable_thinking = connection.execute(
        text(f"SELECT reasoning_effort, enable_thinking FROM {table} WHERE id = :id"),
        {"id": row_id},
    ).one()
    return reasoning_effort, enable_thinking


def test_reasoning_effort_stays_in_sync_with_legacy_writes() -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "73832e0810da"
    )
    assert revision is not None
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    schema = f"test_preset_reasoning_{uuid.uuid4().hex}"
    pre_off, pre_on, new_high, old_off, old_on = (uuid.uuid4() for _ in range(5))
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            # Isolate the tables and trigger function; roll everything back.
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            for table in _TABLES:
                connection.execute(
                    text(f"""
                        CREATE TABLE {table} (
                            id uuid PRIMARY KEY,
                            retries integer NOT NULL DEFAULT 3,
                            enable_thinking boolean NOT NULL DEFAULT true
                        )
                    """)
                )
                connection.execute(
                    text(f"""
                        INSERT INTO {table} (id, enable_thinking)
                        VALUES (:pre_off, false), (:pre_on, true)
                    """),
                    {"pre_off": pre_off, "pre_on": pre_on},
                )

            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()

                for table in _TABLES:
                    # Deploy: the backfill maps the legacy flag.
                    assert _state(connection, table, pre_off) == ("off", False)
                    assert _state(connection, table, pre_on) == (None, True)

                    # The new app writes reasoning_effort; the flag follows it.
                    connection.execute(
                        text(f"""
                            INSERT INTO {table} (id, reasoning_effort, enable_thinking)
                            VALUES (:id, 'high', true)
                        """),
                        {"id": new_high},
                    )
                    connection.execute(
                        text(
                            f"UPDATE {table} SET reasoning_effort = 'off' WHERE id = :id"
                        ),
                        {"id": pre_on},
                    )
                    assert _state(connection, table, pre_on) == ("off", False)

                    # App rollback: the old app writes only enable_thinking.
                    connection.execute(
                        text(f"""
                            INSERT INTO {table} (id, enable_thinking)
                            VALUES (:off, false), (:on, true)
                        """),
                        {"off": old_off, "on": old_on},
                    )
                    connection.execute(
                        text(
                            f"UPDATE {table} SET enable_thinking = true WHERE id = :id"
                        ),
                        {"id": pre_on},
                    )
                    connection.execute(
                        text(f"UPDATE {table} SET retries = 5 WHERE id = :id"),
                        {"id": new_high},
                    )

                    # Re-upgrade: the new app reads what the old app meant.
                    assert _state(connection, table, old_off) == ("off", False)
                    assert _state(connection, table, old_on) == (None, True)
                    assert _state(connection, table, pre_on) == (None, True)
                    assert _state(connection, table, new_high) == ("high", True)

                    # An old app disabling thinking turns any level off.
                    connection.execute(
                        text(
                            f"UPDATE {table} SET enable_thinking = false WHERE id = :id"
                        ),
                        {"id": new_high},
                    )
                    assert _state(connection, table, new_high) == ("off", False)

                revision.module.downgrade()

            inspector = inspect(connection)
            for table in _TABLES:
                columns = {column["name"] for column in inspector.get_columns(table)}
                assert "reasoning_effort" not in columns
                assert "enable_thinking" in columns
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_proc "
                        "WHERE proname = 'sync_agent_preset_reasoning_effort'"
                    )
                ).scalar_one()
                == 0
            )
            transaction.rollback()
    finally:
        engine.dispose()
