"""Verify default changes preserve dependency data across upgrade and downgrade."""

import importlib.util
import uuid
from pathlib import Path

import orjson
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


@pytest.mark.parametrize("deletion_marker", ["archived_at", "deleted_at"])
@pytest.mark.parametrize("enabled", [None, False, True])
def test_agents_defaults_migration_preserves_existing_data(
    deletion_marker: str,
    enabled: bool | None,
) -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic/versions/c3a17be4d902_default_agents_config_to_empty_subagents.py"
    )
    spec = importlib.util.spec_from_file_location("agents_defaults", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    workspace, parent, deleted, live, other = (uuid.uuid4() for _ in range(5))
    refs = [
        {"preset_id": str(value), "preset": "reused-slug"}
        for value in (live, deleted, other)
    ]
    agents_config = (
        {"subagents": refs}
        if enabled is None
        else {"enabled": enabled, "subagents": refs}
    )
    try:
        with engine.begin() as conn:
            # Temporary tables shadow production names only on this connection.
            for table in ("agent_preset", "agent_preset_version"):
                conn.execute(
                    sa.text(f"""
                    CREATE TEMP TABLE {table} (
                        id uuid, workspace_id uuid, agents jsonb NOT NULL DEFAULT '{{"enabled": false}}'::jsonb,
                        deleted_at timestamptz
                    ) ON COMMIT DROP
                """)
                )
                conn.execute(
                    sa.text(
                        f"INSERT INTO {table} VALUES (:id, :workspace, CAST(:agents AS jsonb), NULL)"
                    ),
                    {
                        "id": parent,
                        "workspace": workspace,
                        "agents": orjson.dumps(agents_config).decode(),
                    },
                )
            for child, is_deleted in ((deleted, True), (live, False), (other, False)):
                conn.execute(
                    sa.text(
                        "INSERT INTO agent_preset VALUES (:id, :workspace, '{\"subagents\": []}', CASE WHEN :deleted THEN now() END)"
                    ),
                    {"id": child, "workspace": workspace, "deleted": is_deleted},
                )
            conn.execute(
                sa.text(
                    "CREATE TEMP TABLE skill (id uuid, workspace_id uuid, deleted_at timestamptz, archived_at timestamptz) ON COMMIT DROP"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO skill VALUES (:deleted, :workspace, "
                    "CASE WHEN :canonical THEN now() END, "
                    "CASE WHEN NOT :canonical THEN now() END), "
                    "(:live, :workspace, NULL, NULL)"
                ),
                {
                    "deleted": deleted,
                    "live": live,
                    "workspace": workspace,
                    "canonical": deletion_marker == "deleted_at",
                },
            )
            for table in ("agent_preset_skill", "agent_preset_version_skill"):
                conn.execute(
                    sa.text(
                        f"CREATE TEMP TABLE {table} (skill_id uuid, workspace_id uuid) ON COMMIT DROP"
                    )
                )
                conn.execute(
                    sa.text(
                        f"INSERT INTO {table} VALUES (:deleted, :workspace), (:live, :workspace)"
                    ),
                    {"deleted": deleted, "live": live, "workspace": workspace},
                )
            # Compare every row, including legacy shapes and all binding columns.
            tables = (
                "agent_preset",
                "agent_preset_version",
                "agent_preset_skill",
                "agent_preset_version_skill",
                "skill",
            )
            snapshots = {
                table: conn.execute(sa.text(f"SELECT * FROM {table}")).all()
                for table in tables
            }
            with Operations.context(MigrationContext.configure(conn)):
                for migrate, expected_default in (
                    (migration.upgrade, {"enabled": True, "subagents": []}),
                    (migration.upgrade, {"enabled": True, "subagents": []}),
                    (migration.downgrade, {"enabled": False}),
                    (migration.upgrade, {"enabled": True, "subagents": []}),
                ):
                    migrate()
                    for table, before in snapshots.items():
                        assert (
                            conn.execute(sa.text(f"SELECT * FROM {table}")).all()
                            == before
                        )
                    for table in ("agent_preset", "agent_preset_version"):
                        # Roll back probe inserts so the next snapshot stays exact.
                        with conn.begin_nested() as probe:
                            assert (
                                conn.scalar(
                                    sa.text(
                                        f"INSERT INTO {table} DEFAULT VALUES RETURNING agents"
                                    )
                                )
                                == expected_default
                            )
                            probe.rollback()
    finally:
        engine.dispose()
