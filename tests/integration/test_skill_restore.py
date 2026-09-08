"""Restore immutable skill metadata and grants without reinterpreting files."""

import uuid
from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.skill.schemas import SkillCreate
from tracecat.agent.skill.service import (
    ManifestValidationResult,
    SkillFileBlobRef,
    SkillService,
)
from tracecat.agent.skill.types import ResolvedSkillMcpTool, SkillToolProjection
from tracecat.auth.types import Role
from tracecat.db.models import (
    MCPIntegration,
    SkillVersion,
    SkillVersionMcpTool,
    SkillVersionTool,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.service import IntegrationService


async def _publish_snapshot(
    service: SkillService,
    markdown: str,
    projection: SkillToolProjection = SkillToolProjection(),
) -> SkillVersion:
    """Seed a historical snapshot using its accepted canonical metadata."""
    created = await service.create_skill(SkillCreate(name="restore-skill"))
    skill = await service.get_skill(created.id)
    assert skill is not None
    blob = await service._get_or_create_blob(content=markdown.encode())
    version = await service.publish_version_from_blob_refs(
        skill=skill,
        file_refs=[
            ("SKILL.md", SkillFileBlobRef(blob=blob, content_type="text/markdown"))
        ],
        validation=ManifestValidationResult(
            name="restore-skill",
            tool_projection=projection,
        ),
    )
    await service.session.commit()
    return version


@pytest.mark.anyio
@pytest.mark.parametrize(
    "legacy_fields",
    [
        "metadata: legacy-owner\n",
        "description:\n  legacy: value\n",
        "metadata:\n  tools:\n    - core.nonexistent.action\n",
    ],
)
async def test_restore_preserves_accepted_legacy_content_and_empty_grants(
    session: AsyncSession, svc_role: Role, legacy_fields: str
) -> None:
    service = SkillService(session=session, role=svc_role)
    markdown = (
        f"---\nname: restore-skill\n{legacy_fields}---\nHistorical instructions\n"
    )
    source = await _publish_snapshot(service, markdown)
    restored = await service.restore_version(
        skill_id=source.skill_id, version_id=source.id
    )
    assert restored.current_version_id != source.id
    assert restored.current_version_id is not None
    restored_file = await service.get_version_file(
        skill_id=source.skill_id,
        version_id=restored.current_version_id,
        path="SKILL.md",
    )
    assert restored_file is not None
    assert restored_file.text_content == markdown
    assert restored.name == source.name
    assert restored.description == source.description
    for model in (SkillVersionTool, SkillVersionMcpTool):
        assert (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.skill_version_id == restored.current_version_id)
            )
            == 0
        )


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["renamed", "disabled", "recreated"])
async def test_restore_uses_original_mcp_identity_and_checks_availability(
    session: AsyncSession,
    svc_role: Role,
    state: Literal["renamed", "disabled", "recreated"],
) -> None:
    integration = MCPIntegration(
        workspace_id=svc_role.workspace_id,
        name="Synthetic restore MCP",
        slug=f"restore-{uuid.uuid4().hex}",
        server_type="http",
        server_uri="https://mcp.example.test",
        auth_type=MCPAuthType.NONE,
        tools=[{"name": "read", "enabled": True, "status": "available"}],
    )
    session.add(integration)
    await session.commit()
    original_id, original_slug = integration.id, integration.slug
    tool_id = f"mcp.{original_slug}.read"
    service = SkillService(session=session, role=svc_role)
    source = await _publish_snapshot(
        service,
        f"---\nname: restore-skill\nmetadata:\n  tools:\n    - {tool_id}\n---\nInstructions\n",
        SkillToolProjection(
            mcp_tools=(ResolvedSkillMcpTool(tool_id, original_id, "read"),)
        ),
    )
    # Retire the source version so deleting its integration is permitted.
    current = await service.publish_skill(source.skill_id)
    if state == "renamed":
        integration.slug = f"renamed-{uuid.uuid4().hex}"
    elif state == "disabled":
        integration.tools = [{"name": "read", "enabled": False, "status": "available"}]
    else:
        assert await IntegrationService(
            session=session, role=svc_role
        ).delete_mcp_integration(mcp_integration_id=original_id)
        session.add(
            MCPIntegration(
                workspace_id=svc_role.workspace_id,
                name="Replacement MCP",
                slug=original_slug,
                server_type="http",
                server_uri="https://replacement.example.test",
                auth_type=MCPAuthType.NONE,
                tools=[{"name": "read", "enabled": True, "status": "available"}],
            )
        )
    await session.commit()

    if state != "renamed":
        with pytest.raises(TracecatValidationError) as exc_info:
            await service.restore_version(
                skill_id=source.skill_id, version_id=source.id
            )
        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == (
            "skill_mcp_tools_unavailable"
            if state == "disabled"
            else "skill_mcp_integrations_unavailable"
        )
        skill = await service.get_skill(source.skill_id)
        assert skill is not None
        assert skill.current_version_id == current.id
    else:
        restored = await service.restore_version(
            skill_id=source.skill_id, version_id=source.id
        )
        projection = await session.scalar(
            select(SkillVersionMcpTool).where(
                SkillVersionMcpTool.skill_version_id == restored.current_version_id
            )
        )
        assert projection is not None
        assert projection.mcp_integration_id == original_id
        assert projection.tool_id == tool_id
        assert projection.tool_name == "read"
