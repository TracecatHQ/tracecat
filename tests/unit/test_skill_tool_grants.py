"""Isolated regressions for immutable skill grant compilation."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.skill.grants import SkillToolGrantService
from tracecat.agent.skill.types import ResolvedSkillRef, SkillMcpGrant
from tracecat.auth.types import Role
from tracecat.db.models import MCPIntegration, SkillVersion, SkillVersionMcpTool
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.service import IntegrationService


@pytest.mark.anyio
@pytest.mark.parametrize("whole_first", [True, False])
@pytest.mark.parametrize("available", [True, False])
async def test_explicit_requirements_are_validated_before_union(
    monkeypatch: pytest.MonkeyPatch, whole_first: bool, available: bool
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    workspace_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    version_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
    )
    whole = SkillVersionMcpTool(
        tool_id="mcp.synthetic",
        mcp_integration_id=integration_id,
        tool_name=None,
    )
    explicit = SkillVersionMcpTool(
        tool_id="mcp.synthetic.read",
        mcp_integration_id=integration_id,
        tool_name="read",
    )
    version = SkillVersion(
        id=version_id,
        tools=[],
        mcp_tools=[whole, explicit] if whole_first else [explicit, whole],
    )
    session = AsyncMock(spec=AsyncSession)
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [version]
    session.execute.return_value = rows
    integration = MCPIntegration(
        id=integration_id,
        tools=[{"name": "read", "enabled": available, "status": "available"}],
    )
    monkeypatch.setattr(
        IntegrationService,
        "list_mcp_integrations",
        AsyncMock(return_value=[integration]),
    )
    service = SkillToolGrantService(session=session, role=role)
    monkeypatch.setattr(service, "require_entitlement", AsyncMock())
    resolved = [
        ResolvedSkillRef(
            skill_id=uuid.uuid4(),
            skill_name="synthetic",
            skill_version_id=version_id,
            manifest_sha256="a" * 64,
        )
    ]

    if not available:
        with pytest.raises(TracecatValidationError) as exc_info:
            await service.compile_tool_grants(
                preset_version_id=uuid.uuid4(), resolved_skills=resolved
            )
        assert exc_info.value.detail is not None
        assert exc_info.value.detail["code"] == "skill_mcp_tools_unavailable"
        assert exc_info.value.detail["tool_ids"] == ["mcp.synthetic.read"]
    else:
        grants = await service.compile_tool_grants(
            preset_version_id=uuid.uuid4(), resolved_skills=resolved
        )
        assert grants.mcp_grants == (SkillMcpGrant(integration_id, None),)
