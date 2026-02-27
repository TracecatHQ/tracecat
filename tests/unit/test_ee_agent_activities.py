"""Tests for EE agent activities MCP selection validation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_ee.agent.activities import AgentActivities, BuildToolDefsArgs

from tracecat.agent.schemas import ToolFilters
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError
from tracecat.registry.lock.types import RegistryLock


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset(),
    )


@pytest.mark.anyio
async def test_build_tool_definitions_raises_when_mcp_selected_without_servers(
    role: Role,
) -> None:
    activity = AgentActivities()
    args = BuildToolDefsArgs(
        role=role,
        tool_filters=ToolFilters(actions=["mcp.Linear.list_issues"]),
        mcp_servers=None,
    )

    with patch(
        "tracecat_ee.agent.activities.build_agent_tools",
        new=AsyncMock(return_value=SimpleNamespace(tools=[])),
    ):
        with pytest.raises(
            TracecatValidationError,
            match="no MCP servers are configured",
        ):
            await activity.build_tool_definitions(args)


@pytest.mark.anyio
async def test_build_tool_definitions_rejects_selected_stdio_tools_with_dotted_server_name(
    role: Role,
) -> None:
    activity = AgentActivities()
    args = BuildToolDefsArgs(
        role=role,
        tool_filters=ToolFilters(actions=["mcp.acme.com.list_issues"]),
        mcp_servers=[
            {
                "type": "stdio",
                "name": "acme.com",
                "command": "acme-mcp",
            }
        ],
    )

    mock_lock_service = AsyncMock()
    mock_lock_service.resolve_lock_with_bindings.return_value = RegistryLock(
        origins={},
        actions={},
    )
    mock_lock_ctx = AsyncMock()
    mock_lock_ctx.__aenter__.return_value = mock_lock_service

    with (
        patch(
            "tracecat_ee.agent.activities.build_agent_tools",
            new=AsyncMock(return_value=SimpleNamespace(tools=[])),
        ),
        patch(
            "tracecat_ee.agent.activities.discover_user_mcp_tools",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "tracecat_ee.agent.activities.RegistryLockService.with_session",
            return_value=mock_lock_ctx,
        ),
    ):
        with pytest.raises(
            TracecatValidationError,
            match="Stdio MCP tools cannot be allowlisted individually",
        ):
            await activity.build_tool_definitions(args)


@pytest.mark.anyio
async def test_build_tool_definitions_rejects_stdio_tool_approvals(
    role: Role,
) -> None:
    activity = AgentActivities()
    args = BuildToolDefsArgs(
        role=role,
        tool_filters=ToolFilters(actions=None),
        tool_approvals={"mcp.acme.com.list_issues": True},
        mcp_servers=[
            {
                "type": "stdio",
                "name": "acme.com",
                "command": "acme-mcp",
            }
        ],
    )

    mock_lock_service = AsyncMock()
    mock_lock_service.resolve_lock_with_bindings.return_value = RegistryLock(
        origins={},
        actions={},
    )
    mock_lock_ctx = AsyncMock()
    mock_lock_ctx.__aenter__.return_value = mock_lock_service

    with (
        patch(
            "tracecat_ee.agent.activities.build_agent_tools",
            new=AsyncMock(return_value=SimpleNamespace(tools=[])),
        ),
        patch(
            "tracecat_ee.agent.activities.discover_user_mcp_tools",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "tracecat_ee.agent.activities.RegistryLockService.with_session",
            return_value=mock_lock_ctx,
        ),
    ):
        with pytest.raises(
            TracecatValidationError,
            match="Stdio MCP tool approvals are not supported",
        ):
            await activity.build_tool_definitions(args)


@pytest.mark.anyio
async def test_build_tool_definitions_rejects_missing_http_mcp_tool_approvals(
    role: Role,
) -> None:
    activity = AgentActivities()
    args = BuildToolDefsArgs(
        role=role,
        tool_filters=ToolFilters(actions=None),
        tool_approvals={"mcp.custom_linear.list_issues": True},
        mcp_servers=[
            {
                "type": "http",
                "name": "custom_linear",
                "url": "https://linear.example.com/mcp",
            }
        ],
    )

    mock_lock_service = AsyncMock()
    mock_lock_service.resolve_lock_with_bindings.return_value = RegistryLock(
        origins={},
        actions={},
    )
    mock_lock_ctx = AsyncMock()
    mock_lock_ctx.__aenter__.return_value = mock_lock_service

    with (
        patch(
            "tracecat_ee.agent.activities.build_agent_tools",
            new=AsyncMock(return_value=SimpleNamespace(tools=[])),
        ),
        patch(
            "tracecat_ee.agent.activities.discover_user_mcp_tools",
            new=AsyncMock(
                return_value={
                    "mcp__custom_linear__list_projects": SimpleNamespace(
                        description="List projects"
                    )
                }
            ),
        ),
        patch(
            "tracecat_ee.agent.activities.RegistryLockService.with_session",
            return_value=mock_lock_ctx,
        ),
    ):
        with pytest.raises(
            TracecatValidationError,
            match="Some MCP tool approvals did not match configured integrations",
        ):
            await activity.build_tool_definitions(args)
