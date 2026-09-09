"""MCP routing must preserve integration identity and avoid reserved names."""

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
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.mcp_validation import MCPConfigurationError
from tracecat.integrations.service import IntegrationService
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock


@pytest.fixture(autouse=True)
def db_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "slugs", [("alpha", "beta"), ("user-tracecat-registry", "user-tracecat_registry")]
)
async def test_duplicate_display_names_keep_subsets_and_endpoints_separate(
    monkeypatch: pytest.MonkeyPatch,
    slugs: tuple[str, str],
) -> None:
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
        for slug in slugs
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
    assert policy.tool_approvals == {f"mcp.{slugs[0]}.read": True}
    session = AsyncMock(spec=AsyncSession)
    service = AgentPresetService(session, role=role)
    refs = service._resolve_tool_mcp_grants(policy.mcp_grants, by_id)
    assert refs is not None
    assert [ref["name"] for ref in refs] == list(slugs)
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
    assert set(result.tool_definitions) == {
        f"mcp__{slugs[0]}__read",
        f"mcp__{slugs[1]}__write",
    }
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
        (f"https://{slugs[0]}.example.test/mcp", "read"),
        (f"https://{slugs[1]}.example.test/mcp", "write"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "requested_slug", "separator", "expected"),
    [
        ("Tracecat Registry", None, "-", "user-tracecat-registry"),
        ("Tracecat Registry Team", None, "-", "user-tracecat-registry-team"),
        ("Example", "tracecat-registry", "-", "user-tracecat-registry"),
        ("Example", "tracecat_registry", "_", "user-tracecat_registry"),
        ("Example", "tracecat_registry_team", "_", "user-tracecat_registry_team"),
        ("Example", "tracecat-registryish", "-", "tracecat-registryish"),
        ("Example", "tracecat_registryish", "_", "tracecat_registryish"),
        ("Example Tools", None, "-", "example-tools"),
        ("Example", "custom_route", "_", "custom_route"),
    ],
)
async def test_only_reserved_integration_slugs_are_escaped(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    requested_slug: str | None,
    separator: str,
    expected: str,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = IntegrationService(AsyncMock(spec=AsyncSession), role=role)
    monkeypatch.setattr(
        service, "_mcp_integration_slug_taken", AsyncMock(return_value=False)
    )
    slug = await service._generate_mcp_integration_slug(
        name=name,
        requested_slug=requested_slug,
        requested_slug_separator=separator,
    )
    assert slug == expected
    assert user_client.UserMCPClient.parse_user_mcp_tool_name(f"mcp__{slug}__read") == (
        slug,
        "read",
    )


@pytest.mark.anyio
async def test_escaped_integration_slug_remains_unique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = IntegrationService(AsyncMock(spec=AsyncSession), role=role)
    slug_taken = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(service, "_mcp_integration_slug_taken", slug_taken)
    assert (
        await service._generate_mcp_integration_slug(name="Tracecat Registry")
        == "user-tracecat-registry-1"
    )
    assert [call.args[0] for call in slug_taken.await_args_list] == [
        "user-tracecat-registry",
        "user-tracecat-registry-1",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "slug",
    [
        "tracecat-registry",
        "tracecat-registry-team",
        "tracecat_registry",
        "tracecat_registry_team",
    ],
)
async def test_existing_reserved_slugs_fail_before_routing(slug: str) -> None:
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    session = AsyncMock(spec=AsyncSession)
    integration = MCPIntegration(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        name="Example integration",
        slug=slug,
        server_type="http",
        server_uri="https://example.test/mcp",
        auth_type=MCPAuthType.NONE,
    )
    preset_service = AgentPresetService(session, role=role)
    with pytest.raises(TracecatValidationError) as exc_info:
        preset_service._mcp_integration_refs(
            [str(integration.id)], {integration.id: integration}
        )
    assert exc_info.value.detail == {
        "code": "reserved_mcp_integration_slug",
        "mcp_integration_id": str(integration.id),
    }
    integration_service = IntegrationService(session, role=role)
    with pytest.raises(MCPConfigurationError, match="Recreate the integration"):
        await integration_service.resolve_mcp_http_server_config(integration)
    assert integration.slug == slug
    session.execute.assert_not_awaited()
    assert (
        user_client.UserMCPClient.parse_user_mcp_tool_name(f"mcp__{slug}__read") is None
    )
