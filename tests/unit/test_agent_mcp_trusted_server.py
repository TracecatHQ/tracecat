import uuid
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from tracecat.agent.mcp import trusted_server
from tracecat.agent.tokens import MCPTokenClaims, UserMCPServerClaim
from tracecat.exceptions import BuiltinRegistryHasNoSelectionError

type ExecuteUserMCPTool = Callable[[str, str, dict[str, object], str], Awaitable[str]]
type ExecuteActionTool = Callable[
    [str, dict[str, object], str, str | None],
    Awaitable[str],
]


def _build_claims(*, user_mcp_servers: list[UserMCPServerClaim]) -> MCPTokenClaims:
    return MCPTokenClaims(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        parent_agent_workflow_id="agent/00000000-0000-0000-0000-000000000003",
        parent_agent_run_id="run-123",
        allowed_actions=["core.http_request"],
        user_mcp_servers=user_mcp_servers,
    )


@pytest.mark.anyio
async def test_execute_user_mcp_tool_returns_descriptive_error_when_not_authorized(
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

    with pytest.raises(ToolError, match="User MCP server 'Jira' not authorized"):
        await execute_user_mcp_tool(
            "Jira",
            "getIssue",
            {},
            "token",
        )


@pytest.mark.anyio
async def test_execute_user_mcp_tool_returns_descriptive_error_on_execution_error(
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

    with pytest.raises(
        ToolError,
        match="^User MCP tool 'getIssue' on server 'Jira' failed$",
    ):
        await execute_user_mcp_tool(
            "Jira",
            "getIssue",
            {},
            "token",
        )


@pytest.mark.anyio
async def test_execute_action_tool_forwards_tool_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trusted_server,
        "verify_mcp_token",
        lambda _: _build_claims(user_mcp_servers=[]),
    )
    execute_action = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(trusted_server, "execute_action", execute_action)

    class _AsyncContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def resolve_lock_with_bindings(self, actions):
            return {"actions": actions}

    monkeypatch.setattr(
        trusted_server.RegistryLockService,
        "with_session",
        lambda: _AsyncContext(),
    )

    execute_action_tool = cast(ExecuteActionTool, trusted_server.execute_action_tool)
    result = await execute_action_tool(
        "core.http_request",
        {"url": "https://example.com"},
        "token",
        "toolu_123",
    )

    execute_action.assert_awaited_once()
    call = execute_action.await_args
    assert call is not None
    assert call.args[0] == "core.http_request"
    assert call.args[1] == {"url": "https://example.com"}
    assert call.kwargs["tool_call_id"] == "toolu_123"
    assert result == '{"ok": true}'


@pytest.mark.anyio
async def test_execute_action_tool_surfaces_builtin_registry_sync_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trusted_server,
        "verify_mcp_token",
        lambda _: _build_claims(user_mcp_servers=[]),
    )
    monkeypatch.setattr(
        trusted_server,
        "execute_action",
        AsyncMock(return_value={"ok": True}),
    )

    class _AsyncContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def resolve_lock_with_bindings(self, _actions):
            raise BuiltinRegistryHasNoSelectionError(
                "Builtin registry sync is still in progress. Please retry shortly.",
                detail={"origin": "tracecat_registry"},
            )

    monkeypatch.setattr(
        trusted_server.RegistryLockService,
        "with_session",
        lambda: _AsyncContext(),
    )

    execute_action_tool = cast(ExecuteActionTool, trusted_server.execute_action_tool)
    with pytest.raises(ToolError, match="retry shortly"):
        await execute_action_tool(
            "core.http_request",
            {"url": "https://example.com"},
            "token",
            None,
        )
