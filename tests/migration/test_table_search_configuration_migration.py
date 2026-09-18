"""The follow-up migration preserves old bindings and permits absent providers."""

import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tests.migration.test_search_storage_migration import run_alembic


def test_unconfigured_selection_upgrade_and_guarded_downgrade() -> None:
    name = f"test_search_selection_{uuid.uuid4().hex}"
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
    engine = create_engine(url, poolclass=NullPool)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        upgraded = run_alembic(url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO search_collection
                (id, organization_id, workspace_id, source_id, selected_column_ids, chunker_settings, config_version)
                VALUES (:id, :org, :workspace, :source, ARRAY[:column]::uuid[], '{}'::jsonb, NULL)"""),
                {
                    "id": uuid.uuid4(),
                    "org": uuid.uuid4(),
                    "workspace": uuid.uuid4(),
                    "source": uuid.uuid4(),
                    "column": uuid.uuid4(),
                },
            )
        rejected = run_alembic(url, "downgrade", "391f391da70b")
        assert rejected.returncode != 0
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM search_collection WHERE config_version IS NULL"
                    )
                )
                == 1
            )
            conn.execute(
                text("DELETE FROM search_collection WHERE config_version IS NULL")
            )
        downgraded = run_alembic(url, "downgrade", "391f391da70b")
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT is_nullable FROM information_schema.columns WHERE table_name = 'search_collection' AND column_name = 'config_version'"
                    )
                )
                == "NO"
            )
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
