"""Database lock coverage for agent preset import dependency safety."""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset, Skill, Workspace
from tracecat.db.soft_delete import with_deleted
from tracecat.workspace_sync.adapters import AGENT_PRESET_RESOURCE_ADAPTER
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.schemas import (
    AgentPresetResourceSpec,
    AgentPresetSkillBinding,
)

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


async def test_import_holds_dependency_locks_until_transaction_finishes(
    svc_role: Role,
) -> None:
    """Deletion cannot acquire dependencies midway through import writes."""
    role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()}, deep=True)
    engine = create_async_engine(TEST_DB_CONFIG.test_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as seed:
            seed.add(
                Workspace(
                    id=role.workspace_id,
                    name="Import locking test",
                    organization_id=role.organization_id,
                )
            )
            await seed.flush()
            skills = [
                Skill(workspace_id=role.workspace_id, name="current", slug="current"),
                Skill(workspace_id=role.workspace_id, name="legacy", slug=None),
                Skill(
                    workspace_id=role.workspace_id, name="unrelated", slug="unrelated"
                ),
            ]
            presets = [
                AgentPreset(
                    workspace_id=role.workspace_id,
                    name=name,
                    slug=name,
                    model_name="test-model",
                    model_provider="test-provider",
                    agents={"subagents": []},
                    deleted_at=datetime.now(UTC) if name == "deleted" else None,
                )
                for name in ("live", "deleted")
            ]
            seed.add_all([*skills, *presets])
            await seed.commit()

        spec = AgentPresetResourceSpec(
            id="live",
            name="live",
            slug="live",
            skills=[
                AgentPresetSkillBinding(slug=name) for name in ("current", "legacy")
            ],
        )
        async with sessions() as importing, sessions() as competing:
            await AGENT_PRESET_RESOURCE_ADAPTER._lock_import_dependencies(
                WorkspaceResourceImportService(importing, role=role), {"live": spec}
            )
            available_skills = set(
                (
                    await competing.scalars(
                        sa.select(Skill.id)
                        .where(Skill.workspace_id == role.workspace_id)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            assert available_skills == {skills[2].id}
            assert not (
                await competing.scalars(
                    with_deleted(
                        sa.select(AgentPreset.id)
                        .where(AgentPreset.workspace_id == role.workspace_id)
                        .with_for_update(skip_locked=True)
                    )
                )
            ).all()
            await competing.rollback()
            await importing.rollback()
            assert set(
                (
                    await competing.scalars(
                        with_deleted(
                            sa.select(AgentPreset.id)
                            .where(AgentPreset.workspace_id == role.workspace_id)
                            .with_for_update(skip_locked=True)
                        )
                    )
                ).all()
            ) == {preset.id for preset in presets}
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                sa.delete(Workspace).where(Workspace.id == role.workspace_id)
            )
            await cleanup.commit()
        await engine.dispose()
