"""Verify default-agent and chat-visibility schema expansion and downgrade."""

import uuid
from typing import Literal

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


@pytest.mark.parametrize("default_change", ["unchanged", "replaced", "cleared"])
def test_default_agent_and_chat_visibility_expansion(
    default_change: Literal["unchanged", "replaced", "cleared"],
) -> None:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "d45011587673"
    )
    assert revision is not None
    engine = create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    schema = f"test_chat_visibility_{uuid.uuid4().hex}"
    workspaces = {
        key: uuid.uuid4()
        for key in (
            "valid",
            "empty",
            "missing",
            "deleted",
            "foreign",
            "malformed",
            "absent",
            "null_settings",
        )
    }
    preset_id, deleted_id, foreign_id, new_id = (uuid.uuid4() for _ in range(4))
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(
                text("""
                CREATE TABLE workspace (id uuid PRIMARY KEY, settings jsonb);
                CREATE TABLE agent_preset (
                    id uuid PRIMARY KEY,
                    workspace_id uuid NOT NULL REFERENCES workspace(id),
                    deleted_at timestamptz
                )
            """)
            )
            for key, workspace_id in workspaces.items():
                selection = {
                    "valid": str(preset_id),
                    "empty": None,
                    "missing": str(uuid.uuid4()),
                    "deleted": str(deleted_id),
                    "foreign": str(foreign_id),
                    "malformed": "not-a-uuid",
                    "absent": None,
                    "null_settings": None,
                }[key]
                connection.execute(
                    text("""
                    INSERT INTO workspace VALUES (
                        :id, jsonb_build_object(
                            'default_agent_preset_id', CAST(:selection AS text),
                            'validate_attachment_magic_number', true
                        )
                    )
                """),
                    {"id": workspace_id, "selection": selection},
                )
            connection.execute(
                text("""
                UPDATE workspace
                SET settings = settings - 'default_agent_preset_id'
                WHERE id = :id
                """),
                {"id": workspaces["absent"]},
            )
            connection.execute(
                text("UPDATE workspace SET settings = NULL WHERE id = :id"),
                {"id": workspaces["null_settings"]},
            )
            for agent_id, workspace_id, deleted in (
                (preset_id, workspaces["valid"], False),
                (deleted_id, workspaces["deleted"], True),
                (foreign_id, workspaces["empty"], False),
            ):
                connection.execute(
                    text("""
                    INSERT INTO agent_preset VALUES (
                        :id, :workspace, CASE WHEN :deleted THEN now() END
                    )
                """),
                    {"id": agent_id, "workspace": workspace_id, "deleted": deleted},
                )
            original_settings = dict(
                connection.execute(text("SELECT id, settings FROM workspace"))
                .tuples()
                .all()
            )

            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()
                defaults = dict(
                    connection.execute(
                        text("SELECT id, default_agent_preset_id FROM workspace")
                    )
                    .tuples()
                    .all()
                )
                assert defaults == {
                    workspace_id: preset_id if key == "valid" else None
                    for key, workspace_id in workspaces.items()
                }
                assert (
                    dict(
                        connection.execute(text("SELECT id, settings FROM workspace"))
                        .tuples()
                        .all()
                    )
                    == original_settings
                )
                assert (
                    connection.execute(text("SELECT use_in_chat FROM agent_preset"))
                    .scalars()
                    .all()
                    == [False] * 3
                )

                # An old application insert does not know the new boolean.
                connection.execute(
                    text("""
                    INSERT INTO agent_preset (id, workspace_id) VALUES (:id, :workspace)
                """),
                    {"id": new_id, "workspace": workspaces["valid"]},
                )
                assert (
                    connection.scalar(
                        text("SELECT use_in_chat FROM agent_preset WHERE id = :id"),
                        {"id": new_id},
                    )
                    is False
                )
                connection.execute(
                    text("UPDATE agent_preset SET use_in_chat = true WHERE id = :id"),
                    {"id": new_id},
                )
                assert (
                    connection.scalar(
                        text("SELECT use_in_chat FROM agent_preset WHERE id = :id"),
                        {"id": new_id},
                    )
                    is True
                )

                if default_change == "replaced":
                    connection.execute(
                        text("DELETE FROM agent_preset WHERE id = :id"),
                        {"id": preset_id},
                    )
                    assert (
                        connection.scalar(
                            text(
                                "SELECT default_agent_preset_id FROM workspace WHERE id = :id"
                            ),
                            {"id": workspaces["valid"]},
                        )
                        is None
                    )
                if default_change != "unchanged":
                    connection.execute(
                        text(
                            "UPDATE workspace SET default_agent_preset_id = :agent WHERE id = :workspace"
                        ),
                        {
                            "agent": new_id if default_change == "replaced" else None,
                            "workspace": workspaces["valid"],
                        },
                    )
                revision.module.downgrade()
                assert "default_agent_preset_id" not in {
                    column["name"]
                    for column in inspect(connection).get_columns("workspace")
                }
                assert "use_in_chat" not in {
                    column["name"]
                    for column in inspect(connection).get_columns("agent_preset")
                }
                rolled_back_settings = dict(
                    connection.execute(text("SELECT id, settings FROM workspace"))
                    .tuples()
                    .all()
                )
                expected_settings = original_settings.copy()
                expected_settings[workspaces["valid"]] = {
                    "default_agent_preset_id": {
                        "unchanged": str(preset_id),
                        "replaced": str(new_id),
                        "cleared": None,
                    }[default_change],
                    "validate_attachment_magic_number": True,
                }
                assert rolled_back_settings == expected_settings
            transaction.rollback()
    finally:
        engine.dispose()
