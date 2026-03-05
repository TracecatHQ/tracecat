import uuid
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from tracecat.agent.mcp import trusted_server
from tracecat.agent.tokens import MCPTokenClaims, UserMCPServerClaim

type ExecuteUserMCPTool = Callable[[str, str, dict[str, object], str], Awaitable[str]]


def _build_claims(*, user_mcp_servers: list[UserMCPServerClaim]) -> MCPTokenClaims:
    return MCPTokenClaims(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        allowed_actions=["core.http_request"],
        user_mcp_servers=user_mcp_servers,
    )


@pytest.mark.anyio
async def test_execute_user_mcp_tool_returns_server_name_when_not_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trusted_server,
        "verify_mcp_token",
        lambda _: _build_claims(user_mcp_servers=[]),
    )
    execute_user_mcp_tool = cast(
        ExecuteUserMCPTool, trusted_server.execute_user_mcp_tool
    )

    with pytest.raises(ToolError, match="^Jira$"):
        await execute_user_mcp_tool(
            "Jira",
            "getIssue",
            {},
            "token",
        )


@pytest.mark.anyio
async def test_execute_user_mcp_tool_returns_server_name_on_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trusted_server,
        "verify_mcp_token",
        lambda _: _build_claims(
            user_mcp_servers=[
                UserMCPServerClaim(
                    name="Jira",
                    url="https://mcp.atlassian.com/v1/mcp",
                )
            ]
        ),
    )
    mock_client = AsyncMock()
    mock_client.call_tool.side_effect = RuntimeError(
        "failed converting from {'Authorization': 'Bearer secret'}"
    )
    monkeypatch.setattr(trusted_server, "UserMCPClient", lambda _: mock_client)
    execute_user_mcp_tool = cast(
        ExecuteUserMCPTool, trusted_server.execute_user_mcp_tool
    )

    with pytest.raises(ToolError, match="^Jira$"):
        await execute_user_mcp_tool(
            "Jira",
            "getIssue",
            {},
            "token",
        )
