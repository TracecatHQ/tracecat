import pytest
from fastmcp.client.transports import StreamableHttpTransport

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp.user_client import UserMCPClient, _create_transport


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
