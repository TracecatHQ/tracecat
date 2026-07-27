import httpx
import pytest
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

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


# Regression: lowercasing only wins fastmcp's `inbound | self.headers` union
# when our own credential is an Authorization header. Servers authenticating
# via non-Authorization headers (e.g. Wiz client-credentials sends
# Wiz-Client-Id/Secret) never collide, so the inbound Tracecat session JWT
# reached the third-party server and Wiz rejected the request with a 401.
# _create_transport now installs a client factory that strips the forwarded
# token unless our configured headers deliberately set Authorization.

WIZ_CLIENT_CREDENTIALS = {
    "Wiz-Client-Id": "svc-account-id",
    "Wiz-Client-Secret": "svc-account-secret",
    "Wiz-DataCenter": "us1",
    "X-Wiz-MCP-Mode": "gateway",
}


def _outbound_headers(
    configured: dict[str, str] | None,
    inbound: dict[str, str],
) -> httpx.Headers:
    """Return the headers httpx receives after fastmcp's merge and our factory."""
    transport = _create_transport(
        url="https://mcp.example.com/mcp",
        transport_type="http",
        headers=configured,
        timeout=None,
    )
    assert isinstance(transport, StreamableHttpTransport)
    factory = transport.httpx_client_factory
    assert factory is not None
    # fastmcp http.py: get_http_headers(include={"authorization"}) | self.headers
    merged = inbound | transport.headers
    client = factory(headers=merged, timeout=None, auth=None)
    return client.headers


def test_create_transport_strips_forwarded_auth_for_non_authorization_credentials() -> (
    None
):
    """A server authenticating via custom headers must not receive our JWT."""
    headers = _outbound_headers(
        dict(WIZ_CLIENT_CREDENTIALS),
        {"authorization": "Bearer tracecat-session-jwt"},
    )

    assert "authorization" not in headers
    assert headers["wiz-client-id"] == "svc-account-id"
    assert headers["wiz-client-secret"] == "svc-account-secret"
    assert headers["wiz-datacenter"] == "us1"
    assert headers["x-wiz-mcp-mode"] == "gateway"


def test_create_transport_strips_forwarded_auth_when_no_credentials_configured() -> (
    None
):
    """Servers configured with no auth must not receive an Authorization header."""
    headers = _outbound_headers(None, {"authorization": "Bearer tracecat-session-jwt"})

    # Not merely emptied: the header must be absent, since httpx transmits
    # empty-valued headers rather than dropping them.
    assert "authorization" not in headers


def test_create_transport_preserves_configured_authorization_credential() -> None:
    """OAuth-backed servers must still receive their own bearer token."""
    headers = _outbound_headers(
        {"Authorization": "Bearer wiz-oauth-token"},
        {"authorization": "Bearer tracecat-session-jwt"},
    )

    assert headers["authorization"] == "Bearer wiz-oauth-token"


def test_sse_transport_also_strips_forwarded_auth() -> None:
    """The leak is transport-independent; fastmcp merges in the shared base."""
    transport = _create_transport(
        url="https://mcp.example.com/sse",
        transport_type="sse",
        headers=dict(WIZ_CLIENT_CREDENTIALS),
        timeout=None,
    )
    assert isinstance(transport, SSETransport)
    factory = transport.httpx_client_factory
    assert factory is not None

    merged = {"authorization": "Bearer tracecat-session-jwt"} | transport.headers
    client = factory(headers=merged, timeout=None, auth=None)

    assert "authorization" not in client.headers
    assert client.headers["wiz-client-id"] == "svc-account-id"
