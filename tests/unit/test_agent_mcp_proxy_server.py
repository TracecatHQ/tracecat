from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.mcp import proxy_server


@pytest.mark.anyio
async def test_create_proxy_mcp_server_only_augments_registry_tool_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_tools: list[dict[str, object]] = []

    def fake_tool(name: str, description: str, schema: dict[str, object]):
        def decorator(handler):
            captured_tools.append(
                {
                    "name": name,
                    "description": description,
                    "schema": schema,
                    "handler": handler,
                }
            )
            return handler

        return decorator

    monkeypatch.setattr(proxy_server, "tool", fake_tool)
    monkeypatch.setattr(proxy_server, "create_sdk_mcp_server", lambda **kwargs: kwargs)

    allowed_actions = {
        "core.http_request": MCPToolDefinition(
            name="core.http_request",
            description="HTTP request",
            parameters_json_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        ),
        "mcp__Jira__getIssue": MCPToolDefinition(
            name="mcp__Jira__getIssue",
            description="Get issue",
            parameters_json_schema={
                "type": "object",
                "properties": {"issueKey": {"type": "string"}},
                "required": ["issueKey"],
                "additionalProperties": False,
            },
        ),
        "internal.builder.list_sessions": MCPToolDefinition(
            name="internal.builder.list_sessions",
            description="List sessions",
            parameters_json_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
    }

    await proxy_server.create_proxy_mcp_server(
        allowed_actions=allowed_actions,
        auth_token="token",
    )

    schemas_by_name = {
        item["name"]: item["schema"]
        for item in captured_tools
        if isinstance(item, dict)
    }
    registry_schema = schemas_by_name["core__http_request"]
    assert isinstance(registry_schema, dict)
    assert proxy_server.PROXY_TOOL_METADATA_KEY in registry_schema["properties"]

    user_schema = schemas_by_name["mcp__Jira__getIssue"]
    assert isinstance(user_schema, dict)
    assert proxy_server.PROXY_TOOL_METADATA_KEY not in user_schema["properties"]

    internal_schema = schemas_by_name["internal__builder__list_sessions"]
    assert isinstance(internal_schema, dict)
    assert proxy_server.PROXY_TOOL_METADATA_KEY not in internal_schema["properties"]


@pytest.mark.anyio
async def test_registry_proxy_handler_strips_metadata_and_forwards_tool_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_tool = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(text='{"ok": true}')],
            is_error=False,
        )
    )

    class _FakeClient:
        def __init__(self, transport):
            self.transport = transport

        async def __aenter__(self):
            return SimpleNamespace(call_tool=call_tool)

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(proxy_server, "_create_uds_transport", lambda _: object())
    monkeypatch.setattr(proxy_server, "Client", _FakeClient)

    handler = proxy_server._make_tool_handler(
        "execute_action_tool",
        {"action_name": "core.http_request"},
        "auth-token",
        {"tool_type": "registry", "action_name": "core.http_request"},
    )

    result = await handler(
        {
            "url": "https://example.com",
            proxy_server.PROXY_TOOL_METADATA_KEY: {
                proxy_server.PROXY_TOOL_CALL_ID_KEY: "toolu_123",
            },
        }
    )

    call_tool.assert_awaited_once_with(
        "execute_action_tool",
        {
            "action_name": "core.http_request",
            "args": {"url": "https://example.com"},
            "auth_token": "auth-token",
            "tool_call_id": "toolu_123",
        },
    )
    assert result == {"content": [{"type": "text", "text": '{"ok": true}'}]}
