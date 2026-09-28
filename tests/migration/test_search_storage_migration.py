"""Live migration prerequisite, source compatibility and downgrade checks."""

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


def run_alembic(
    url: str, *arguments: str, role: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "TRACECAT__DB_URI": url}
    if role is not None:
        env["PGOPTIONS"] = f"-c role={role}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("preinstalled", [False, True])
def test_search_migration_enables_vector_and_preserves_source_data(
    preinstalled: bool,
) -> None:
    name = f"test_search_migration_{uuid.uuid4().hex}"
    restricted_role = f"migration_role_{uuid.uuid4().hex}"
    admin = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
    engine = create_engine(url, poolclass=NullPool)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        previous = run_alembic(url, "upgrade", "a7c3e9f1b2d4")
        assert previous.returncode == 0, previous.stderr
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE synthetic_source (id integer PRIMARY KEY, body text NOT NULL)"
                )
            )
            conn.execute(
                text("INSERT INTO synthetic_source VALUES (1, 'preserved source')")
            )
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
                == 0
            )
            if preinstalled:
                conn.execute(text("CREATE EXTENSION vector WITH SCHEMA public"))
        if not preinstalled:
            with engine.begin() as conn:
                conn.execute(text(f'CREATE ROLE "{restricted_role}"'))
                conn.execute(
                    text(f'GRANT CREATE ON DATABASE "{name}" TO "{restricted_role}"')
                )
                conn.execute(
                    text(f'GRANT USAGE ON SCHEMA public TO "{restricted_role}"')
                )
                conn.execute(
                    text(f'GRANT SELECT ON alembic_version TO "{restricted_role}"')
                )
            denied = run_alembic(url, "upgrade", "9680c861644a", role=restricted_role)
            assert denied.returncode != 0
            assert "permission denied to create extension" in denied.stderr
            assert "https://docs.tracecat.com/self-hosting/pgvector" in denied.stderr
        else:
            with engine.begin() as conn:
                conn.execute(text("CREATE SCHEMA synthetic_vector"))
                conn.execute(text("ALTER EXTENSION vector SET SCHEMA synthetic_vector"))
            wrong_schema = run_alembic(url, "upgrade", "9680c861644a")
            assert wrong_schema.returncode != 0
            assert "Tracecat requires pgvector" in wrong_schema.stderr
            assert (
                "https://docs.tracecat.com/self-hosting/pgvector" in wrong_schema.stderr
            )
            with engine.begin() as conn:
                conn.execute(text("ALTER EXTENSION vector SET SCHEMA public"))
        with engine.connect() as conn:
            assert (
                conn.scalar(text("SELECT version_num FROM alembic_version"))
                == "a7c3e9f1b2d4"
            )
        upgraded = run_alembic(url, "upgrade", "9680c861644a")
        assert upgraded.returncode == 0, upgraded.stderr
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_extension e "
                        "JOIN pg_namespace n ON n.oid = e.extnamespace "
                        "WHERE e.extname = 'vector' AND n.nspname = 'public'"
                    )
                )
                == 1
            )
            assert (
                conn.scalar(text("SELECT body FROM synthetic_source WHERE id = 1"))
                == "preserved source"
            )
            conn.execute(
                text(
                    "INSERT INTO synthetic_source VALUES (2, 'old writer still works')"
                )
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE tablename LIKE 'search_%'"
                    )
                )
                == 5
            )
            assert conn.scalar(text("SELECT count(*) FROM search_workspace_state")) == 0
        recipe_upgrade = run_alembic(url, "upgrade", "391f391da70b")
        assert recipe_upgrade.returncode == 0, recipe_upgrade.stderr
        with engine.begin() as conn:
            # An old writer omits the new column and still succeeds.
            conn.execute(
                text("""
                INSERT INTO search_embedding_config
                (organization_id, workspace_id, version, provider, model, endpoint,
                 credential_id, credential_environment, dimensions, input_token_limit)
                VALUES (:org, :workspace, 1, 'openai', 'text-embedding-3-small',
                        'https://api.openai.com/v1/embeddings', :secret, 'default', 1536, 8191)
            """),
                {
                    "org": uuid.uuid4(),
                    "workspace": uuid.uuid4(),
                    "secret": uuid.uuid4(),
                },
            )
            assert (
                conn.scalar(text("SELECT recipe_revision FROM search_embedding_config"))
                is None
            )
        recipe_downgrade = run_alembic(url, "downgrade", "9680c861644a")
        assert recipe_downgrade.returncode == 0, recipe_downgrade.stderr
        with engine.connect() as conn:
            assert (
                conn.scalar(text("SELECT count(*) FROM search_embedding_config")) == 1
            )
        downgraded = run_alembic(url, "downgrade", "a7c3e9f1b2d4")
        assert downgraded.returncode == 0, downgraded.stderr
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT count(*) FROM synthetic_source")) == 2
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
                == 1
            )
            assert conn.scalar(text("SELECT to_regclass('search_chunk')")) is None
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{restricted_role}"'))
        admin.dispose()
