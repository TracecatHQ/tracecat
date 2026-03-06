"""Unit tests for user MCP client helpers."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    PromptMessage,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl

from tracecat.agent.common.types import MCPHttpServerConfig
from tracecat.agent.mcp import user_client as user_client_module
from tracecat.agent.mcp.user_client import UserMCPClient, infer_transport_type


class _FakeClient:
    def __init__(self, transport: Any) -> None:
        self.transport = transport

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=f"{name}:{arguments['value']}")],
            structuredContent={"ok": True},
            isError=False,
        )

    async def read_resource(self, uri: str) -> list[TextResourceContents]:
        return [
            TextResourceContents(uri=AnyUrl("https://example.com/readme"), text=uri)
        ]

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None
    ) -> GetPromptResult:
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"{name}:{(arguments or {}).get('severity', 'none')}",
                    ),
                )
            ]
        )


def test_infer_transport_type_uses_sse_suffix() -> None:
    assert infer_transport_type("https://example.com/mcp/sse") == "sse"
    assert infer_transport_type("https://example.com/mcp") == "http"


@pytest.mark.anyio
async def test_user_mcp_client_supports_full_result_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config: MCPHttpServerConfig = {
        "type": "http",
        "name": "server",
        "url": "https://example.com/mcp",
        "transport": "http",
    }
    monkeypatch.setattr(user_client_module, "Client", _FakeClient)
    client = UserMCPClient([config])

    tool_result = await client.call_tool_result("server", "list_repos", {"value": "x"})
    resource_result = await client.read_resource("server", "docs://readme")
    prompt_result = await client.get_prompt(
        "server", "triage_incident", {"severity": "high"}
    )

    assert tool_result.structuredContent == {"ok": True}
    assert resource_result[0].model_dump(mode="json")["text"] == "docs://readme"
    assert (
        prompt_result.messages[0].content.model_dump(mode="json")["text"]
        == "triage_incident:high"
    )
