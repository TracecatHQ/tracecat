"""Stdio subsets fail closed at authoring and immutable grant compilation."""

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.skill.frontmatter import SkillFrontmatter, SkillMetadata
from tracecat.agent.skill.grants import SkillToolGrantService
from tracecat.agent.skill.service import ManifestValidationResult, SkillService
from tracecat.agent.skill.types import ResolvedSkillRef, SkillMcpGrant
from tracecat.agent.skill.validation import STDIO_MCP_TOOL_SUBSET_UNSUPPORTED
from tracecat.auth.types import Role
from tracecat.db.models import MCPIntegration, SkillVersion, SkillVersionMcpTool
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.service import IntegrationService


@dataclass(frozen=True, slots=True)
class GrantContext:
    role: Role
    session: AsyncMock
    integration: MCPIntegration
    version: SkillVersion
    resolved: ResolvedSkillRef


@pytest.fixture
def grant_context(monkeypatch: pytest.MonkeyPatch) -> GrantContext:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    integration = MCPIntegration(
        id=uuid.uuid4(),
        slug="synthetic",
        server_type="stdio",
        tools=[{"name": "search", "enabled": True, "status": "available"}],
    )
    version = SkillVersion(id=uuid.uuid4(), tools=[], mcp_tools=[])
    session = AsyncMock(spec=AsyncSession)
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [version]
    session.execute.return_value = rows
    monkeypatch.setattr(
        IntegrationService,
        "list_mcp_integrations",
        AsyncMock(return_value=[integration]),
    )
    resolved = ResolvedSkillRef(
        skill_id=uuid.uuid4(),
        skill_name="synthetic",
        skill_version_id=version.id,
        manifest_sha256="a" * 64,
    )
    return GrantContext(role, session, integration, version, resolved)


@pytest.mark.anyio
@pytest.mark.parametrize("server_type", ["http", "stdio"])
@pytest.mark.parametrize(
    "tool_names", [(None,), ("search",), (None, "search"), ("search", None)]
)
async def test_authoring_rejects_only_unsupported_stdio_declarations(
    grant_context: GrantContext, server_type: str, tool_names: tuple[str | None, ...]
) -> None:
    ctx = grant_context
    ctx.integration.server_type = server_type
    tool_ids = [
        f"mcp.synthetic.{name}" if name else "mcp.synthetic" for name in tool_names
    ]
    result = ManifestValidationResult(
        frontmatter=SkillFrontmatter(
            name="synthetic", metadata=SkillMetadata(tools=tool_ids)
        )
    )
    service = SkillService(ctx.session, role=ctx.role)
    await service._validate_declared_tools(result)

    if server_type == "stdio" and "search" in tool_names:
        assert [error.code for error in result.errors] == [
            STDIO_MCP_TOOL_SUBSET_UNSUPPORTED
        ]
        assert result.tool_projection is None
        assert "mcp.synthetic.search" in result.errors[0].message
    else:
        assert result.errors == []
        assert result.tool_projection is not None
        assert [tool.tool_name for tool in result.tool_projection.mcp_tools] == list(
            tool_names
        )


@pytest.mark.anyio
@pytest.mark.parametrize("server_type", ["http", "stdio"])
@pytest.mark.parametrize(
    "tool_names", [(None,), ("search",), (None, "search"), ("search", None)]
)
async def test_compilation_rechecks_existing_immutable_stdio_grants(
    grant_context: GrantContext,
    monkeypatch: pytest.MonkeyPatch,
    server_type: str,
    tool_names: tuple[str | None, ...],
) -> None:
    ctx = grant_context
    ctx.integration.server_type = server_type
    ctx.version.mcp_tools = [
        SkillVersionMcpTool(
            tool_id=f"mcp.synthetic.{name}" if name else "mcp.synthetic",
            mcp_integration_id=ctx.integration.id,
            tool_name=name,
        )
        for name in tool_names
    ]
    service = SkillToolGrantService(ctx.session, role=ctx.role)
    monkeypatch.setattr(service, "require_entitlement", AsyncMock())

    if server_type == "stdio" and "search" in tool_names:
        with pytest.raises(TracecatValidationError) as exc:
            await service.compile_tool_grants(resolved_skills=[ctx.resolved])
        assert exc.value.detail is not None
        assert exc.value.detail["code"] == STDIO_MCP_TOOL_SUBSET_UNSUPPORTED
        assert exc.value.detail["tool_ids"] == ["mcp.synthetic.search"]
    else:
        grants = await service.compile_tool_grants(resolved_skills=[ctx.resolved])
        assert grants.mcp_grants == (
            SkillMcpGrant(
                ctx.integration.id,
                None if None in tool_names else frozenset({"search"}),
            ),
        )
