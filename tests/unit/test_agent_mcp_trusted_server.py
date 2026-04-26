import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp import trusted_server
from tracecat.agent.mcp.metadata import PROXY_TOOL_CALL_ID_KEY, PROXY_TOOL_METADATA_KEY
from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.tokens import MCPTokenClaims, UserMCPServerClaim
from tracecat.exceptions import BuiltinRegistryHasNoSelectionError

type ExecuteUserMCPTool = Callable[[str, str, dict[str, object], str], Awaitable[str]]
type ExecuteActionTool = Callable[
    [str, dict[str, object], str, str | None],
    Awaitable[str],
]


def _build_claims(
    *,
    allowed_actions: list[str] | None = None,
    allowed_internal_tools: list[str] | None = None,
    user_mcp_servers: list[UserMCPServerClaim] | None = None,
) -> MCPTokenClaims:
    return MCPTokenClaims(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        session_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        parent_agent_workflow_id="agent/00000000-0000-0000-0000-000000000003",
        parent_agent_run_id="run-123",
        allowed_actions=allowed_actions or ["core.http_request"],
        allowed_internal_tools=allowed_internal_tools or [],
        user_mcp_servers=user_mcp_servers or [],
    )


@pytest.mark.anyio
async def test_execute_user_mcp_tool_returns_descriptive_error_when_not_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trusted_server,
        "verify_mcp_token",
        lambda _: _build_claims(allowed_actions=["mcp__Jira__getIssue"]),
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
            allowed_actions=["core.http_request", "mcp__Jira__getIssue"],
            user_mcp_servers=[
                UserMCPServerClaim(
                    name="Jira",
                    url="https://mcp.atlassian.com/v1/mcp",
                )
            ],
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
        lambda _: _build_claims(),
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
        lambda _: _build_claims(),
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


@pytest.mark.anyio
async def test_build_token_scoped_tools_filters_root_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_tool_definitions(
        action_names: list[str],
    ) -> dict[str, MCPToolDefinition]:
        assert action_names == ["core.cases.list_cases"]
        return {
            "core.cases.list_cases": MCPToolDefinition(
                name="core.cases.list_cases",
                description="List cases",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                    "additionalProperties": False,
                },
            )
        }

    monkeypatch.setattr(
        trusted_server,
        "fetch_tool_definitions",
        fake_fetch_tool_definitions,
    )

    claims = _build_claims(allowed_actions=["core.cases.list_cases"])
    tools = await trusted_server.build_token_scoped_tools(claims)

    assert [tool.name for tool in tools] == ["core__cases__list_cases"]
    schema = tools[0].parameters
    assert isinstance(schema, dict)
    assert PROXY_TOOL_METADATA_KEY in schema["properties"]


@pytest.mark.anyio
async def test_build_token_scoped_tools_filters_subagent_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_tool_definitions(
        action_names: list[str],
    ) -> dict[str, MCPToolDefinition]:
        assert action_names == ["core.cases.list_cases"]
        return {
            "core.cases.list_cases": MCPToolDefinition(
                name="core.cases.list_cases",
                description="List cases",
                parameters_json_schema={"type": "object"},
            )
        }

    class _FakeUserMCPClient:
        parse_user_mcp_tool_name = staticmethod(UserMCPClient.parse_user_mcp_tool_name)

        def __init__(self, configs: list[dict[str, Any]]) -> None:
            self.configs = configs

        async def discover_tools(self) -> dict[str, MCPToolDefinition]:
            assert [config["name"] for config in self.configs] == ["Jira"]
            return {
                "mcp__Jira__getIssue": MCPToolDefinition(
                    name="mcp__Jira__getIssue",
                    description="Get issue",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {"issueKey": {"type": "string"}},
                    },
                ),
                "mcp__Jira__listProjects": MCPToolDefinition(
                    name="mcp__Jira__listProjects",
                    description="List projects",
                    parameters_json_schema={"type": "object"},
                ),
            }

    monkeypatch.setattr(
        trusted_server,
        "fetch_tool_definitions",
        fake_fetch_tool_definitions,
    )
    monkeypatch.setattr(trusted_server, "UserMCPClient", _FakeUserMCPClient)

    claims = _build_claims(
        allowed_actions=["core.cases.list_cases", "mcp__Jira__getIssue"],
        allowed_internal_tools=["internal.builder.get_session"],
        user_mcp_servers=[
            UserMCPServerClaim(
                name="Jira",
                url="https://mcp.atlassian.com/v1/mcp",
            )
        ],
    )

    tools = await trusted_server.build_token_scoped_tools(claims)

    assert [tool.name for tool in tools] == [
        "core__cases__list_cases",
        "internal__builder__get_session",
        "mcp__Jira__getIssue",
    ]


@pytest.mark.anyio
async def test_call_token_scoped_tool_routes_registry_and_strips_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_registry = AsyncMock(return_value='{"ok": true}')
    monkeypatch.setattr(
        trusted_server,
        "_execute_registry_action",
        execute_registry,
    )

    claims = _build_claims(allowed_actions=["core.cases.list_cases"])
    result = await trusted_server.call_token_scoped_tool(
        "core__cases__list_cases",
        {
            "limit": 10,
            PROXY_TOOL_METADATA_KEY: {PROXY_TOOL_CALL_ID_KEY: "toolu_123"},
        },
        claims,
    )

    assert result == '{"ok": true}'
    execute_registry.assert_awaited_once_with(
        "core.cases.list_cases",
        {"limit": 10},
        claims,
        tool_call_id="toolu_123",
    )


@pytest.mark.anyio
async def test_call_token_scoped_tool_routes_internal_and_user_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_internal = AsyncMock(return_value='{"session": true}')
    execute_user_mcp = AsyncMock(return_value='"ISSUE-1"')
    monkeypatch.setattr(trusted_server, "_execute_internal", execute_internal)
    monkeypatch.setattr(trusted_server, "_execute_user_mcp", execute_user_mcp)

    claims = _build_claims(
        allowed_actions=["mcp__Jira__getIssue"],
        allowed_internal_tools=["internal.builder.get_session"],
    )

    internal_result = await trusted_server.call_token_scoped_tool(
        "internal__builder__get_session",
        {"session_id": "builder-session"},
        claims,
    )
    user_result = await trusted_server.call_token_scoped_tool(
        "mcp__Jira__getIssue",
        {"issueKey": "ISSUE-1"},
        claims,
    )

    assert internal_result == '{"session": true}'
    assert user_result == '"ISSUE-1"'
    execute_internal.assert_awaited_once_with(
        "internal.builder.get_session",
        {"session_id": "builder-session"},
        claims,
    )
    execute_user_mcp.assert_awaited_once_with(
        "Jira",
        "getIssue",
        {"issueKey": "ISSUE-1"},
        claims,
    )


@pytest.mark.anyio
async def test_token_scoped_mcp_call_requires_bearer_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trusted_server,
        "get_http_headers",
        lambda include: {},
    )

    with pytest.raises(ToolError, match="Authentication failed"):
        await trusted_server.mcp.call_tool("core__cases__list_cases", {})
