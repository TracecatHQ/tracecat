import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp import trusted_server
from tracecat.agent.mcp.metadata import PROXY_TOOL_CALL_ID_KEY, PROXY_TOOL_METADATA_KEY
from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.tokens import MCPTokenClaims, UserMCPServerClaim
from tracecat.exceptions import BuiltinRegistryHasNoSelectionError
from tracecat.registry.lock.types import RegistryLock


def _build_claims(
    *,
    allowed_actions: list[str] | None = None,
    allowed_internal_tools: list[str] | None = None,
    user_mcp_servers: list[UserMCPServerClaim] | None = None,
    registry_lock: RegistryLock | None = None,
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
        registry_lock=registry_lock,
    )


@pytest.mark.anyio
async def test_execute_user_mcp_tool_returns_descriptive_error_when_not_authorized() -> (
    None
):
    with pytest.raises(ToolError, match="User MCP server 'Jira' not authorized"):
        await trusted_server._execute_user_mcp(
            "Jira",
            "getIssue",
            {},
            _build_claims(allowed_actions=["mcp__Jira__getIssue"]),
        )


@pytest.mark.anyio
async def test_execute_user_mcp_tool_returns_descriptive_error_on_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_client = AsyncMock()
    mock_client.call_tool.side_effect = RuntimeError(
        "failed converting from {'Authorization': 'Bearer secret'}"
    )
    monkeypatch.setattr(trusted_server, "UserMCPClient", lambda _: mock_client)
    claims = _build_claims(
        allowed_actions=["core.http_request", "mcp__Jira__getIssue"],
        user_mcp_servers=[
            UserMCPServerClaim(
                name="Jira",
                url="https://mcp.atlassian.com/v1/mcp",
            )
        ],
    )

    with pytest.raises(
        ToolError,
        match="^User MCP tool 'getIssue' on server 'Jira' failed$",
    ):
        await trusted_server._execute_user_mcp(
            "Jira",
            "getIssue",
            {},
            claims,
        )


@pytest.mark.anyio
async def test_execute_action_tool_forwards_tool_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    result = await trusted_server._execute_registry_action(
        "core.http_request",
        {"url": "https://example.com"},
        _build_claims(),
        tool_call_id="toolu_123",
    )

    execute_action.assert_awaited_once()
    call = execute_action.await_args
    assert call is not None
    assert call.args[0] == "core.http_request"
    assert call.args[1] == {"url": "https://example.com"}
    assert call.kwargs["tool_call_id"] == "toolu_123"
    assert result == '{"ok": true}'


@pytest.mark.anyio
async def test_execute_action_tool_uses_registry_lock_from_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_action = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(trusted_server, "execute_action", execute_action)
    monkeypatch.setattr(
        trusted_server.RegistryLockService,
        "with_session",
        lambda: pytest.fail("registry lock should come from token claims"),
    )
    registry_lock = RegistryLock(
        origins={"tracecat_registry": "pinned-version"},
        actions={"core.http_request": "tracecat_registry"},
    )

    result = await trusted_server._execute_registry_action(
        "core.http_request",
        {"url": "https://example.com"},
        _build_claims(registry_lock=registry_lock),
        tool_call_id="toolu_123",
    )

    execute_action.assert_awaited_once()
    call = execute_action.await_args
    assert call is not None
    assert call.args[3] == registry_lock
    assert result == '{"ok": true}'


@pytest.mark.anyio
async def test_execute_action_tool_surfaces_builtin_registry_sync_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    with pytest.raises(ToolError, match="retry shortly"):
        await trusted_server._execute_registry_action(
            "core.http_request",
            {"url": "https://example.com"},
            _build_claims(),
        )


@pytest.mark.anyio
async def test_build_token_scoped_tools_does_not_advertise_internal_from_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_tool_definitions(
        action_names: list[str],
    ) -> dict[str, MCPToolDefinition]:
        assert action_names == []
        return {}

    monkeypatch.setattr(
        trusted_server,
        "fetch_tool_definitions",
        fake_fetch_tool_definitions,
    )

    claims = _build_claims(
        allowed_actions=["internal.builder.get_session"],
        allowed_internal_tools=[],
    )
    tools = await trusted_server.build_token_scoped_tools(claims)

    assert tools == []


@pytest.mark.anyio
async def test_token_scoped_fastmcp_get_tool_reuses_token_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _build_claims(allowed_actions=["core.cases.list_cases"])
    tool = trusted_server._build_scoped_tool(
        tool_name="core__cases__list_cases",
        description="List cases",
        parameters_json_schema={"type": "object"},
        claims=claims,
    )
    build_token_scoped_tools = AsyncMock(return_value=[tool])

    monkeypatch.setattr(
        trusted_server,
        "get_http_headers",
        lambda include: {"authorization": "Bearer token"},
    )
    monkeypatch.setattr(
        trusted_server,
        "_claims_from_authorization_header",
        lambda authorization: claims,
    )
    monkeypatch.setattr(
        trusted_server,
        "build_token_scoped_tools",
        build_token_scoped_tools,
    )

    mcp = trusted_server.TokenScopedFastMCP("test")

    assert await mcp.get_tool("core__cases__list_cases") is tool
    assert await mcp.get_tool("core__cases__list_cases") is tool
    build_token_scoped_tools.assert_awaited_once_with(claims)


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


@pytest.mark.parametrize(
    (
        "tool_name",
        "tool_input",
        "claims",
        "expected_result",
        "expected_registry_call",
        "expected_internal_call",
        "expected_user_mcp_call",
    ),
    [
        pytest.param(
            "core__cases__list_cases",
            {
                "limit": 10,
                PROXY_TOOL_METADATA_KEY: {PROXY_TOOL_CALL_ID_KEY: "toolu_123"},
            },
            _build_claims(allowed_actions=["core.cases.list_cases"]),
            '{"ok": true}',
            ("core.cases.list_cases", {"limit": 10}, "toolu_123"),
            None,
            None,
            id="registry-with-metadata",
        ),
        pytest.param(
            "mcp__tracecat-registry-analyst__core__cases__list_cases",
            {"limit": 10},
            _build_claims(allowed_actions=["core.cases.list_cases"]),
            '{"ok": true}',
            ("core.cases.list_cases", {"limit": 10}, None),
            None,
            None,
            id="subagent-registry",
        ),
        pytest.param(
            "mcp__tracecat-registry-analyst__mcp__Jira__getIssue",
            {"issueKey": "ISSUE-1"},
            _build_claims(allowed_actions=["mcp__Jira__getIssue"]),
            '"ISSUE-1"',
            None,
            None,
            ("Jira", "getIssue", {"issueKey": "ISSUE-1"}),
            id="subagent-user-mcp",
        ),
        pytest.param(
            "internal__builder__get_session",
            {"session_id": "builder-session"},
            _build_claims(allowed_internal_tools=["internal.builder.get_session"]),
            '{"session": true}',
            None,
            ("internal.builder.get_session", {"session_id": "builder-session"}),
            None,
            id="internal-tool",
        ),
        pytest.param(
            "mcp__Jira__getIssue",
            {"issueKey": "ISSUE-1"},
            _build_claims(allowed_actions=["mcp__Jira__getIssue"]),
            '"ISSUE-1"',
            None,
            None,
            ("Jira", "getIssue", {"issueKey": "ISSUE-1"}),
            id="root-user-mcp",
        ),
    ],
)
@pytest.mark.anyio
async def test_call_token_scoped_tool_routes_to_executor(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    tool_input: dict[str, Any],
    claims: MCPTokenClaims,
    expected_result: str,
    expected_registry_call: tuple[str, dict[str, Any], str | None] | None,
    expected_internal_call: tuple[str, dict[str, Any]] | None,
    expected_user_mcp_call: tuple[str, str, dict[str, Any]] | None,
) -> None:
    execute_registry = AsyncMock(return_value='{"ok": true}')
    execute_internal = AsyncMock(return_value='{"session": true}')
    execute_user_mcp = AsyncMock(return_value='"ISSUE-1"')
    monkeypatch.setattr(trusted_server, "_execute_registry_action", execute_registry)
    monkeypatch.setattr(trusted_server, "_execute_internal", execute_internal)
    monkeypatch.setattr(trusted_server, "_execute_user_mcp", execute_user_mcp)

    result = await trusted_server.call_token_scoped_tool(
        tool_name,
        tool_input,
        claims,
    )

    assert result == expected_result
    if expected_registry_call is None:
        execute_registry.assert_not_awaited()
    else:
        action_name, args, tool_call_id = expected_registry_call
        execute_registry.assert_awaited_once_with(
            action_name,
            args,
            claims,
            tool_call_id=tool_call_id,
        )

    if expected_internal_call is None:
        execute_internal.assert_not_awaited()
    else:
        tool, args = expected_internal_call
        execute_internal.assert_awaited_once_with(tool, args, claims)

    if expected_user_mcp_call is None:
        execute_user_mcp.assert_not_awaited()
    else:
        server_name, server_tool, args = expected_user_mcp_call
        execute_user_mcp.assert_awaited_once_with(
            server_name,
            server_tool,
            args,
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
