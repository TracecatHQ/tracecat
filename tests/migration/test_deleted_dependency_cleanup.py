"""Exercise the deletion backfill against PostgreSQL JSONB and binding rows."""

import importlib.util
import uuid
from pathlib import Path

import orjson
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


def test_deleted_dependency_backfill_preserves_live_refs_and_order() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic/versions/9e32e2825c0b_unlink_deleted_agent_and_skill_.py"
    )
    spec = importlib.util.spec_from_file_location("dependency_backfill", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    workspace, parent, deleted, live, other = (uuid.uuid4() for _ in range(5))
    refs = [
        {"preset_id": str(value), "preset": "reused-slug"}
        for value in (live, deleted, other)
    ]
    try:
        with engine.begin() as conn:
            # Temporary tables shadow production names only on this connection.
            for table in ("agent_preset", "agent_preset_version"):
                conn.execute(
                    sa.text(f"""
                    CREATE TEMP TABLE {table} (
                        id uuid, workspace_id uuid, agents jsonb,
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
                        "agents": orjson.dumps({"subagents": refs}).decode(),
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
                    "INSERT INTO skill VALUES (:deleted, :workspace, NULL, now()), (:live, :workspace, NULL, NULL)"
                ),
                {"deleted": deleted, "live": live, "workspace": workspace},
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
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                migration.upgrade()  # Safe if retried.
                migration.downgrade()  # Never recreates deleted links.
            for table in ("agent_preset", "agent_preset_version"):
                agents = conn.scalar(
                    sa.text(f"SELECT agents FROM {table} WHERE id = :parent"),
                    {"parent": parent},
                )
                assert agents == {"subagents": [refs[0], refs[2]]}
            for table in ("agent_preset_skill", "agent_preset_version_skill"):
                assert conn.scalars(sa.text(f"SELECT skill_id FROM {table}")).all() == [
                    live
                ]
    finally:
        engine.dispose()
