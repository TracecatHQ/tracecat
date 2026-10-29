"""Statement counts for MCP integration reads on the agent's hot paths.

Invariants:
- A tool compile reads the workspace's MCP integration rows once, however
  many servers it selects.
- A user MCP tool call reads its integration row once.
- Both reads keep the workspace predicate: a row from another workspace is
  refused, never used. Methods that take an already-loaded row re-check it.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.agent import activities as agent_activities
from tracecat_ee.agent.activities import AgentActivities, BuildToolDefsArgs

from tracecat.agent.common.types import MCPServerConfig, MCPToolDefinition
from tracecat.agent.mcp import trusted_server, user_client
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.tokens import UserMCPServerClaim
from tracecat.agent.tools import BuildToolsResult
from tracecat.auth.types import Role
from tracecat.db.models import MCPIntegration
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.schemas import (
    MCPHttpIntegrationCreate,
    MCPStdioIntegrationCreate,
    MCPToolSummary,
)
from tracecat.integrations.service import IntegrationService
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock

pytestmark = pytest.mark.usefixtures("db")


@contextmanager
def _count_integration_reads(session: AsyncSession) -> Iterator[list[str]]:
    """Collect every statement that reads the MCP integration table."""
    statements: list[str] = []

    def capture(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if "FROM mcp_integration" in statement:
            statements.append(statement)

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", capture)


@pytest.fixture
def use_test_session(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the code under test's preset sessions to the test session."""

    @asynccontextmanager
    async def with_session(
        role: Role | None = None, **_: object
    ) -> AsyncIterator[AgentPresetService]:
        yield AgentPresetService(session, role=role)

    monkeypatch.setattr(AgentPresetService, "with_session", with_session)


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    lock_service = MagicMock(spec=RegistryLockService)
    lock_service.resolve_lock_with_bindings = AsyncMock(
        return_value=RegistryLock(origins={}, actions={})
    )

    @asynccontextmanager
    async def with_session(*_: object, **__: object) -> AsyncIterator[Any]:
        yield lock_service

    monkeypatch.setattr(RegistryLockService, "with_session", with_session)
    monkeypatch.setattr(
        agent_activities,
        "build_agent_tools",
        AsyncMock(return_value=BuildToolsResult(tools=[], collected_secrets=set())),
    )


@pytest.fixture
def discovered(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake every HTTP server: each lists one tool. Records contacted URLs."""
    urls: list[str] = []

    async def discover(
        configs: list[dict[str, Any]], *, fail_on_error: bool = False
    ) -> dict[str, MCPToolDefinition]:
        tools: dict[str, MCPToolDefinition] = {}
        for config in configs:
            urls.append(config["url"])
            name = f"mcp__{config['name']}__search"
            tools[name] = MCPToolDefinition(
                name=name,
                description="Search",
                parameters_json_schema={"type": "object"},
            )
        return tools

    monkeypatch.setattr(user_client, "discover_user_mcp_tools", discover)
    return urls


async def _http_integrations(
    session: AsyncSession, role: Role, count: int
) -> list[MCPIntegration]:
    service = IntegrationService(session, role=role)
    return [
        await service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name=f"Synthetic MCP {i}",
                server_uri=f"https://mcp-{i}.example.test/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        for i in range(count)
    ]


async def _refs(
    session: AsyncSession, role: Role, integrations: list[MCPIntegration]
) -> list[MCPServerConfig]:
    refs = await AgentPresetService(session, role=role).resolve_mcp_integration_refs(
        [str(integration.id) for integration in integrations]
    )
    assert refs is not None
    return refs


def _other_workspace(role: Role) -> Role:
    return role.model_copy(update={"workspace_id": uuid.uuid4()})


@pytest.mark.anyio
@pytest.mark.usefixtures("use_test_session", "fake_registry")
@pytest.mark.parametrize("server_count", [1, 5])
async def test_http_compile_reads_integrations_once(
    session: AsyncSession,
    svc_role: Role,
    discovered: list[str],
    server_count: int,
) -> None:
    integrations = await _http_integrations(session, svc_role, server_count)
    refs = await _refs(session, svc_role, integrations)

    with _count_integration_reads(session) as reads:
        result = await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=svc_role,
                tool_filters=ToolFilters(actions=[]),
                mcp_servers=refs,
            )
        )

    assert len(reads) == 1
    assert len(result.tool_definitions) == server_count
    assert sorted(discovered) == sorted(i.server_uri or "" for i in integrations)


@pytest.mark.anyio
@pytest.mark.usefixtures("use_test_session", "fake_registry")
async def test_stdio_compile_reads_integrations_once(
    session: AsyncSession,
    svc_role: Role,
    discovered: list[str],
) -> None:
    service = IntegrationService(session, role=svc_role)
    integration = await service.create_mcp_integration(
        params=MCPStdioIntegrationCreate(
            name="Synthetic stdio MCP",
            stdio_command="npx",
            stdio_args=["-y", "example-mcp-server"],
        )
    )
    refs = await _refs(session, svc_role, [integration])

    with _count_integration_reads(session) as reads:
        await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=svc_role,
                tool_filters=ToolFilters(actions=[]),
                mcp_servers=refs,
                include_stdio_mcp_servers=True,
            )
        )

    assert len(reads) == 1


@pytest.mark.anyio
@pytest.mark.usefixtures("use_test_session", "fake_registry")
async def test_compile_refuses_rows_from_another_workspace(
    session: AsyncSession,
    svc_role: Role,
    discovered: list[str],
) -> None:
    integrations = await _http_integrations(session, svc_role, 2)
    refs = await _refs(session, svc_role, integrations)

    result = await AgentActivities().build_tool_definitions(
        BuildToolDefsArgs(
            role=_other_workspace(svc_role),
            tool_filters=ToolFilters(actions=[]),
            mcp_servers=refs,
        )
    )

    assert discovered == []
    assert result.tool_definitions == {}


@pytest.mark.anyio
@pytest.mark.usefixtures("use_test_session")
async def test_user_mcp_call_reads_its_integration_once(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    (integration,) = await _http_integrations(session, svc_role, 1)
    claim = UserMCPServerClaim(name=integration.slug, id=integration.id)

    with _count_integration_reads(session) as reads:
        config = await trusted_server._resolve_user_mcp_config(claim, svc_role)

    assert len(reads) == 1
    assert cast(dict[str, Any], config)["url"] == integration.server_uri


@pytest.mark.anyio
@pytest.mark.usefixtures("use_test_session")
async def test_user_mcp_call_refuses_row_from_another_workspace(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    (integration,) = await _http_integrations(session, svc_role, 1)
    claim = UserMCPServerClaim(name=integration.slug, id=integration.id)

    with pytest.raises(ToolError, match="is unavailable"):
        await trusted_server._resolve_user_mcp_config(claim, _other_workspace(svc_role))


@pytest.mark.anyio
async def test_loaded_row_methods_refuse_rows_from_another_workspace(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    """Rows passed in by a caller are re-checked, not trusted."""
    service = IntegrationService(session, role=svc_role)
    (http_row,) = await _http_integrations(session, svc_role, 1)
    await service.persist_mcp_integration_tools(
        mcp_integration_id=http_row.id,
        discovered_tools=[MCPToolSummary(name="search")],
    )
    stdio_row = await service.create_mcp_integration(
        params=MCPStdioIntegrationCreate(
            name="Synthetic stdio MCP",
            stdio_command="npx",
            stdio_args=["-y", "example-mcp-server"],
        )
    )
    owner = AgentPresetService(session, role=svc_role)
    other = AgentPresetService(session, role=_other_workspace(svc_role))

    assert owner.mcp_tool_policies([http_row])
    assert other.mcp_tool_policies([http_row]) == {}

    assert owner.mcp_integration_refs([str(http_row.id)], {http_row.id: http_row})
    with pytest.raises(TracecatValidationError):
        other.mcp_integration_refs([str(http_row.id)], {http_row.id: http_row})

    assert await owner.resolve_loaded_mcp_integration_secrets(http_row) is not None
    assert await other.resolve_loaded_mcp_integration_secrets(http_row) is None

    assert (
        await owner.resolve_stdio_mcp_integration(stdio_row, resolve_env=False)
        is not None
    )
    assert (
        await other.resolve_stdio_mcp_integration(stdio_row, resolve_env=False) is None
    )
