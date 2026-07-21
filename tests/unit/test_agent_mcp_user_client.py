import base64
from typing import Any

import pytest
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import (
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp.user_client import UserMCPClient, _create_transport
from tracecat.agent.mcp.utils import (
    MCP_TOOL_RESULT_MAX_BYTES,
    flatten_mcp_content_blocks,
)


def _mcp_server(name: str) -> MCPHttpServerConfig:
    return {
        "name": name,
        "url": f"https://{name}.example/mcp",
    }


@pytest.mark.anyio
async def test_discover_tools_continues_on_server_failure_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover_server_tools(
        self: UserMCPClient,
        server_name: str,
        config: MCPHttpServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        del self, config
        if server_name == "broken":
            raise RuntimeError("discovery failed")
        return {
            f"mcp__{server_name}__search": MCPToolDefinition(
                name=f"mcp__{server_name}__search",
                description="Search",
                parameters_json_schema={"type": "object"},
            )
        }

    monkeypatch.setattr(
        UserMCPClient,
        "_discover_server_tools",
        fake_discover_server_tools,
    )
    client = UserMCPClient([_mcp_server("working"), _mcp_server("broken")])

    tools = await client.discover_tools()

    assert list(tools) == ["mcp__working__search"]


@pytest.mark.anyio
async def test_discover_tools_fails_closed_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover_server_tools(
        self: UserMCPClient,
        server_name: str,
        config: MCPHttpServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        del self, config
        if server_name == "broken":
            raise RuntimeError("discovery failed")
        return {}

    monkeypatch.setattr(
        UserMCPClient,
        "_discover_server_tools",
        fake_discover_server_tools,
    )
    client = UserMCPClient([_mcp_server("working"), _mcp_server("broken")])

    with pytest.raises(
        RuntimeError,
        match="Failed to discover tools from user MCP server 'broken'",
    ):
        await client.discover_tools(fail_on_error=True)


# Regression: fastmcp's StreamableHttpTransport.connect_session merges any
# inbound `authorization` header (from get_http_headers) with the transport's
# configured headers using `inbound | self.headers`. If our headers use a
# different case for the Authorization key, both end up in the outgoing
# request and httpx joins their values with ", " — Cloudflare 400s the
# malformed header. Normalize keys to lowercase so the dict union collapses
# the two entries and our configured value always wins.


def test_create_transport_lowercases_authorization_so_inbound_jwt_cannot_stack() -> (
    None
):
    """fastmcp does `inbound_lowercase_headers | transport.headers`.

    If our key is "Authorization" (uppercase A), the union keeps both
    `authorization` (the inbound forwarded JWT) and `Authorization` (ours),
    and httpx serializes them as `<jwt>, <our-token>`. Lowercasing our keys
    ensures the union collapses to a single `authorization` entry whose
    value is ours.
    """
    transport = _create_transport(
        url="https://mcp.example.com/mcp",
        transport_type="http",
        headers={"Authorization": "Bearer real-token"},
        timeout=None,
    )
    assert isinstance(transport, StreamableHttpTransport)
    assert "Authorization" not in transport.headers
    assert transport.headers.get("authorization") == "Bearer real-token"


def test_create_transport_lowercased_headers_survive_inbound_merge() -> None:
    """Simulate fastmcp.connect_session's merge to lock the contract.

    fastmcp computes: `get_http_headers(include={'authorization'}) | self.headers`.
    With our lowercase normalization, an inbound forwarded JWT must not
    survive that merge — our configured Notion bearer must win.
    """
    transport = _create_transport(
        url="https://mcp.example.com/mcp",
        transport_type="http",
        headers={"Authorization": "Bearer notion-real"},
        timeout=None,
    )
    assert isinstance(transport, StreamableHttpTransport)

    simulated_inbound = {"authorization": "Bearer tracecat-inbound-jwt"}
    merged = simulated_inbound | transport.headers

    assert merged.get("authorization") == "Bearer notion-real"
    auth_keys = [k for k in merged if k.lower() == "authorization"]
    assert auth_keys == ["authorization"]


def test_flatten_keeps_status_text_and_nested_embedded_resource_body() -> None:
    """Both the status line and the nested file body must survive."""
    file_body = "def main():\n    return 42\n"
    blocks: list[ContentBlock] = [
        TextContent(
            type="text",
            text="successfully downloaded text file (SHA: abc123)",
        ),
        EmbeddedResource(
            type="resource",
            resource=TextResourceContents(
                uri=AnyUrl("https://example.test/repo/main.py"),
                mimeType="text/plain",
                text=file_body,
            ),
        ),
    ]

    flattened = flatten_mcp_content_blocks(blocks)

    assert "successfully downloaded text file (SHA: abc123)" in flattened
    assert file_body in flattened
    # A pydantic repr means the nested lookup fell through to str(block).
    assert "TextResourceContents(" not in flattened
    assert flattened == (
        f"successfully downloaded text file (SHA: abc123)\n\n{file_body}"
    )


def test_flatten_emits_placeholder_for_blob_resource_without_leaking_base64() -> None:
    """Binary payloads must be described, never inlined as base64."""
    blob = base64.b64encode(b"\x89PNG\r\n\x1a\n" * 512).decode()
    blocks: list[ContentBlock] = [
        EmbeddedResource(
            type="resource",
            resource=BlobResourceContents(
                uri=AnyUrl("https://example.test/repo/logo.png"),
                mimeType="image/png",
                blob=blob,
            ),
        ),
    ]

    flattened = flatten_mcp_content_blocks(blocks)

    assert flattened == (
        "[binary resource: https://example.test/repo/logo.png (image/png)]"
    )
    assert blob not in flattened
    assert "iVBOR" not in flattened


def test_flatten_truncates_over_cap_with_loud_marker() -> None:
    """Over-cap results are truncated loudly, not silently dropped."""
    oversized = "x" * (MCP_TOOL_RESULT_MAX_BYTES + 2_000_000)
    blocks: list[ContentBlock] = [TextContent(type="text", text=oversized)]

    flattened = flatten_mcp_content_blocks(blocks)

    assert "[truncated:" in flattened
    assert "omitted]" in flattened
    assert len(flattened) < len(oversized)
    assert len(flattened.encode("utf-8")) < MCP_TOOL_RESULT_MAX_BYTES + 1024
    assert flattened.startswith("x" * 1000)


def test_flatten_single_text_block_is_unchanged() -> None:
    """Regression: the common single-block case keeps its exact prior value."""
    blocks: list[ContentBlock] = [TextContent(type="text", text="plain result")]

    assert flatten_mcp_content_blocks(blocks) == "plain result"


@pytest.mark.parametrize("content", [None, []])
def test_flatten_empty_content_returns_empty_string(
    content: list[ContentBlock] | None,
) -> None:
    assert flatten_mcp_content_blocks(content) == ""


@pytest.mark.anyio
async def test_call_tool_returns_all_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: call_tool must not drop the embedded file body."""

    class _StubResult:
        content: list[ContentBlock] = [
            TextContent(type="text", text="successfully downloaded text file"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri=AnyUrl("https://example.test/a.txt"),
                    mimeType="text/plain",
                    text="the real file body",
                ),
            ),
        ]

    class _StubClient:
        def __init__(self, transport: Any) -> None:
            del transport

        async def __aenter__(self) -> "_StubClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def call_tool(self, tool_name: str, args: dict[str, Any]) -> _StubResult:
            del tool_name, args
            return _StubResult()

    monkeypatch.setattr("tracecat.agent.mcp.user_client.Client", _StubClient)
    client = UserMCPClient([_mcp_server("github")])

    result = await client.call_tool("github", "get_file_contents", {"path": "a.txt"})

    assert "successfully downloaded text file" in result
    assert "the real file body" in result
