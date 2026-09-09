"""Duplicate display names must not merge MCP grants or execution endpoints."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from mcp.types import CallToolResult, TextContent, Tool
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.agent import activities
from tracecat_ee.agent.activities import AgentActivities, BuildToolDefsArgs

from tracecat import config
from tracecat.agent.common.types import is_http_mcp_server
from tracecat.agent.mcp import user_client
from tracecat.agent.mcp.trusted_server import _resolve_user_mcp_config
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.tool_policy import resolve_tool_policy
from tracecat.agent.preset.types import PresetToolInputs
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.tools import BuildToolsResult
from tracecat.auth.types import Role
from tracecat.db.models import MCPIntegration, SkillVersion, SkillVersionMcpTool
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.service import IntegrationService
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock


@pytest.mark.anyio
async def test_duplicate_display_names_keep_subsets_and_endpoints_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    integrations = [
        MCPIntegration(
            id=uuid.uuid4(),
            workspace_id=role.workspace_id,
            name="Shared display name",
            slug=slug,
            server_type="http",
            server_uri=f"https://{slug}.example.test/mcp",
            auth_type=MCPAuthType.NONE,
            tools=[
                {"name": name, "requires_approval": name == "read"}
                for name in ("read", "write")
            ],
        )
        for slug in ("alpha", "beta")
    ]
    by_id = {integration.id: integration for integration in integrations}
    version = SkillVersion(id=uuid.uuid4(), skill_id=uuid.uuid4(), name="triage")
    version.tools = []
    version.mcp_tools = [
        SkillVersionMcpTool(
            tool_id=f"mcp.{integration.slug}.{tool}",
            mcp_integration_id=integration.id,
            tool_name=tool,
        )
        for integration, tool in zip(integrations, ("read", "write"), strict=True)
    ]
    policy = resolve_tool_policy(
        PresetToolInputs(uuid.uuid4(), [], [], [], {}, [version.id]),
        {version.id: version},
        by_id,
    )
    assert policy.tool_approvals == {"mcp.alpha.read": True}
    session = AsyncMock(spec=AsyncSession)
    service = AgentPresetService(session, role=role)
    refs = service._resolve_tool_mcp_grants(policy.mcp_grants, by_id)
    assert refs is not None
    assert [ref["name"] for ref in refs] == ["alpha", "beta"]
    integration_service = IntegrationService(session, role=role)
    for integration, ref in zip(integrations, refs, strict=True):
        server_config = await integration_service.resolve_mcp_http_server_config(
            integration
        )
        assert server_config["name"] == ref["name"]
    monkeypatch.setattr(
        IntegrationService,
        "list_mcp_integrations",
        AsyncMock(return_value=integrations),
    )

    @asynccontextmanager
    async def preset_context(**_kwargs: object) -> AsyncIterator[AgentPresetService]:
        yield service

    lock_service = MagicMock(spec=RegistryLockService)
    lock_service.resolve_lock_with_bindings = AsyncMock(
        return_value=RegistryLock(origins={}, actions={})
    )

    @asynccontextmanager
    async def lock_context() -> AsyncIterator[RegistryLockService]:
        yield lock_service

    calls: list[tuple[str, str]] = []

    class RemoteClient:
        def __init__(self, url: str) -> None:
            self.url = url

        async def __aenter__(self) -> "RemoteClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def list_tools(self) -> list[Tool]:
            return [Tool(name=name, inputSchema={}) for name in ("read", "write")]

        async def call_tool(self, name: str, args: object) -> CallToolResult:
            calls.append((self.url, name))
            return CallToolResult(content=[TextContent(type="text", text="ok")])

    monkeypatch.setattr(AgentPresetService, "with_session", preset_context)
    monkeypatch.setattr(RegistryLockService, "with_session", lock_context)
    monkeypatch.setattr(
        activities,
        "build_agent_tools",
        AsyncMock(return_value=BuildToolsResult(tools=[], collected_secrets=set())),
    )
    monkeypatch.setattr(
        AgentActivities, "_check_tool_approval_entitlement", AsyncMock()
    )
    monkeypatch.setattr(user_client, "_create_transport", lambda url, *args: url)
    monkeypatch.setattr(user_client, "Client", RemoteClient)

    result = await AgentActivities().build_tool_definitions(
        BuildToolDefsArgs(
            role=role,
            tool_filters=ToolFilters(actions=[]),
            mcp_servers=refs,
            tool_approvals=policy.tool_approvals,
            fail_on_mcp_discovery_error=True,
        )
    )
    assert set(result.tool_definitions) == {"mcp__alpha__read", "mcp__beta__write"}
    assert result.tool_approvals == policy.tool_approvals
    assert result.user_mcp_claims is not None
    assert {(claim.name, claim.id) for claim in result.user_mcp_claims} == {
        (integration.slug, integration.id) for integration in integrations
    }
    # Rehydrate the actual claims at the trusted execution boundary, then route calls.
    executable_configs = [
        await _resolve_user_mcp_config(claim, role) for claim in result.user_mcp_claims
    ]
    assert all(is_http_mcp_server(config) for config in executable_configs)
    client = user_client.UserMCPClient(executable_configs)
    for name in result.tool_definitions:
        parsed = client.parse_user_mcp_tool_name(name)
        assert parsed is not None
        await client.call_tool(*parsed, {})
    assert calls == [
        ("https://alpha.example.test/mcp", "read"),
        ("https://beta.example.test/mcp", "write"),
    ]
