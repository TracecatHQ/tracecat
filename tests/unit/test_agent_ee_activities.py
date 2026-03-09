from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_ee.agent.activities import AgentActivities, BuildToolDefsArgs

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.schemas import ToolFilters
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.registry.lock.types import RegistryLock


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )


@pytest.mark.anyio
async def test_build_tool_definitions_excludes_canonical_user_mcp_tools_from_lock_resolution(
    mock_role: Role,
) -> None:
    """Test canonical user MCP tools are not sent to registry lock resolution."""
    agent_activities = AgentActivities()
    registry_tool = MCPToolDefinition(
        name="core.http_request",
        description="Make HTTP requests",
        parameters_json_schema={},
    )
    user_mcp_tool = MCPToolDefinition(
        name="mcp.jira.lookup",
        description="Look up Jira issues",
        parameters_json_schema={},
    )
    registry_lock = RegistryLock(
        origins={"tracecat_registry": "test-version"},
        actions={"core.http_request": "tracecat_registry"},
    )
    mock_lock_service = AsyncMock()
    mock_lock_service.resolve_lock_with_bindings.return_value = registry_lock

    @asynccontextmanager
    async def mock_lock_session(*args, **kwargs):
        yield mock_lock_service

    with (
        patch(
            "tracecat_ee.agent.activities.build_agent_tools",
            new_callable=AsyncMock,
            return_value=MagicMock(tools=[registry_tool]),
        ),
        patch(
            "tracecat.agent.mcp.user_client.discover_user_mcp_tools",
            new_callable=AsyncMock,
            return_value={user_mcp_tool.name: user_mcp_tool},
        ),
        patch(
            "tracecat_ee.agent.activities.RegistryLockService.with_session",
            mock_lock_session,
        ),
    ):
        result = await agent_activities.build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=["core.http_request"]),
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "jira",
                        "url": "https://jira.example.com/mcp",
                    }
                ],
            )
        )

    assert "core.http_request" in result.tool_definitions
    assert "mcp.jira.lookup" in result.tool_definitions
    mock_lock_service.resolve_lock_with_bindings.assert_awaited_once_with(
        {"core.http_request"}
    )
