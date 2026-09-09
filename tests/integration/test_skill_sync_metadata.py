"""Workspace skill imports keep file and manifest metadata consistent."""

import hashlib
from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Skill, SkillVersion
from tracecat.exceptions import TracecatValidationError
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.schemas import (
    SkillFileSpec,
    SkillResourceSpec,
    WorkspaceSpec,
)


def _skill_spec() -> SkillResourceSpec:
    content = "---\nname: synced-skill\ndescription: Original description\n---\nInstructions\n"
    return SkillResourceSpec(
        id="synced-skill",
        slug="synced-skill",
        name="synced-skill",
        description="Original description",
        files=[
            SkillFileSpec(
                path="SKILL.md", sha256=hashlib.sha256(content.encode()).hexdigest()
            )
        ],
        file_contents={"SKILL.md": content},
    )


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["name", "description"])
async def test_metadata_mismatch_cannot_publish_a_skill(
    session: AsyncSession, svc_role: Role, field: Literal["name", "description"]
) -> None:
    spec = _skill_spec().model_copy(update={field: "different-value"})
    importer = WorkspaceResourceImportService(session=session, role=svc_role)
    with pytest.raises(TracecatValidationError) as exc_info:
        async with session.begin_nested():
            await importer.import_non_workflow_resources(
                WorkspaceSpec(skills={spec.id: spec})
            )
    assert exc_info.value.detail == {
        "code": "workspace_sync_skill_metadata_mismatch",
        "fields": [field],
    }
    assert (
        await session.scalar(
            select(func.count())
            .select_from(SkillVersion)
            .where(SkillVersion.workspace_id == svc_role.workspace_id)
        )
        == 0
    )


@pytest.mark.anyio
async def test_reimporting_matching_metadata_preserves_the_version(
    session: AsyncSession, svc_role: Role
) -> None:
    spec = _skill_spec()
    importer = WorkspaceResourceImportService(session=session, role=svc_role)
    desired = WorkspaceSpec(skills={spec.id: spec})
    await importer.import_non_workflow_resources(desired)
    skill = await session.scalar(
        select(Skill).where(
            Skill.workspace_id == svc_role.workspace_id, Skill.slug == spec.slug
        )
    )
    assert skill is not None
    version_id = skill.current_version_id
    await importer.import_non_workflow_resources(desired)
    await session.refresh(skill)
    assert skill.current_version_id == version_id
    assert (
        await session.scalar(
            select(func.count())
            .select_from(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
        )
        == 1
    )
