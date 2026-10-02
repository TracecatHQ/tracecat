"""Execute the additive revision and its downgrade against legacy table shapes."""

from uuid import uuid4

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool


def test_upgrade_old_writers_and_downgrade(reference_database):
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "fbc6e4b5bbf2"
    )
    assert revision is not None
    engine = create_engine(
        reference_database.replace("+asyncpg", "+psycopg"), poolclass=NullPool
    )
    schema = "migration_" + uuid4().hex
    workspace = uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            conn.execute(text("CREATE TABLE workspace (id uuid PRIMARY KEY)"))
            conn.execute(text("INSERT INTO workspace VALUES (:id)"), {"id": workspace})
            for name in ("skill_version", "agent_preset_version", "agent_session"):
                conn.execute(
                    text(
                        f"CREATE TABLE {name} (id uuid UNIQUE NOT NULL, workspace_id uuid NOT NULL REFERENCES workspace(id))"
                    )
                )
                conn.execute(
                    text(f"INSERT INTO {name} VALUES (:id, :workspace)"),
                    {"id": uuid4(), "workspace": workspace},
                )
            with Operations.context(MigrationContext.configure(conn)):
                revision.module.upgrade()
                for name in ("skill_version", "agent_preset_version"):
                    # Simulate an old application INSERT with no new marker.
                    conn.execute(
                        text(
                            f"INSERT INTO {name} (id, workspace_id) VALUES (:id, :workspace)"
                        ),
                        {"id": uuid4(), "workspace": workspace},
                    )
                    assert conn.execute(
                        text(f"SELECT reference_schema_version FROM {name}")
                    ).scalars().all() == [None, None]
                    columns = {
                        c["name"]: c
                        for c in inspect(conn).get_columns(name, schema=schema)
                    }
                    assert columns["reference_schema_version"]["nullable"] is True
                    assert columns["reference_schema_version"]["default"] is None
                assert (
                    conn.scalar(text("SELECT count(*) FROM skill_version_reference"))
                    == 0
                )
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM agent_reference_run_snapshot")
                    )
                    == 0
                )
                assert {
                    i["name"]
                    for i in inspect(conn).get_indexes(
                        "skill_version_reference", schema=schema
                    )
                } >= {"ix_skill_ref_reverse", "ix_skill_ref_source"}
                revision.module.downgrade()
                assert "skill_version_reference" not in inspect(conn).get_table_names(
                    schema=schema
                )
                assert "reference_schema_version" not in {
                    c["name"]
                    for c in inspect(conn).get_columns("skill_version", schema=schema)
                }
                assert conn.scalar(text("SELECT count(*) FROM skill_version")) == 2
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        engine.dispose()
