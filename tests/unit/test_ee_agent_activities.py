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
