import asyncio
import base64
import ipaddress
from typing import Any

import httpx
import pytest
from aiohttp import web
from fastmcp import settings as fastmcp_settings
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp import McpError
from mcp.types import (
    METHOD_NOT_FOUND,
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ErrorData,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl

from tracecat import config
from tracecat.agent.common.exceptions import (
    UserMCPDiscoveryAuthError,
    UserMCPDiscoveryBudgetExceededError,
    UserMCPDiscoveryError,
    UserMCPDiscoveryTimeoutError,
    UserMCPDiscoveryUnavailableError,
)
from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp import user_client
from tracecat.agent.mcp.http_limits import (
    BoundedResponseTransport,
    MCPResponseTooLargeError,
)
from tracecat.agent.mcp.user_client import UserMCPClient, _create_transport
from tracecat.agent.mcp.utils import (
    flatten_mcp_content_blocks,
)


def _mcp_server(name: str) -> MCPHttpServerConfig:
    return {
        "name": name,
        "url": f"https://{name}.example/mcp",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "phase", ["initialize", "tools/list", "sse_connect", "healthy"]
)
async def test_discovery_deadline_with_real_fastmcp_http_client(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Exercise FastMCP's background session and cancellation over an HTTP socket."""
    methods: list[str] = []
    release = asyncio.Event()

    async def mcp_handler(request: web.Request) -> web.Response:
        if request.method != "POST":
            if phase == "sse_connect":
                methods.append(phase)
                await release.wait()
            return web.Response(status=405)
        message = await request.json()
        method = message["method"]
        methods.append(method)
        if method == phase:
            await release.wait()
        if "id" not in message:
            return web.Response(status=202)
        result = (
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-mcp", "version": "1.0"},
            }
            if method == "initialize"
            else {"tools": [{"name": "search", "inputSchema": {"type": "object"}}]}
        )
        return web.json_response(
            {"jsonrpc": "2.0", "id": message["id"], "result": result}
        )

    app = web.Application()
    app.router.add_route("*", "/mcp", mcp_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setattr(
        config,
        "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS",
        [ipaddress.ip_network("127.0.0.0/8")],
    )
    monkeypatch.setattr(
        user_client,
        "MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS",
        2 if phase == "healthy" else 0.2,
    )
    # Discovery must own the timeout even with a shorter FastMCP default.
    monkeypatch.setattr(fastmcp_settings, "client_init_timeout", 0.02)
    client = UserMCPClient(
        [
            {
                "name": "synthetic-mcp",
                "url": f"http://127.0.0.1:{port}/mcp",
                "transport": "sse" if phase == "sse_connect" else "http",
            }
        ]
    )
    try:
        if phase == "healthy":
            tools = await asyncio.wait_for(client.discover_tools(fail_on_error=True), 2)
            assert list(tools) == ["mcp__synthetic-mcp__search"]
        else:
            with pytest.raises(UserMCPDiscoveryTimeoutError) as raised:
                await asyncio.wait_for(client.discover_tools(fail_on_error=True), 2)
            assert raised.value.server_name == "synthetic-mcp"
            assert phase in methods
    finally:
        release.set()
        await runner.cleanup()


@pytest.mark.anyio
@pytest.mark.parametrize("phase", ["connect", "list_tools"])
async def test_discovery_hang_returns_named_timeout_before_activity_deadline(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    cleaned_up = asyncio.Event()

    class HangingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "HangingClient":
            if phase == "connect":
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned_up.set()
            return self

        async def list_tools(self) -> list[Any]:
            await asyncio.Event().wait()
            return []

        async def __aexit__(self, *args: Any) -> None:
            cleaned_up.set()

    monkeypatch.setattr(user_client, "Client", HangingClient)
    monkeypatch.setattr(
        user_client, "MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS", 0.02, raising=False
    )
    with pytest.raises(UserMCPDiscoveryUnavailableError) as raised:
        await asyncio.wait_for(
            UserMCPClient([_mcp_server("hanging")]).discover_tools(fail_on_error=True),
            timeout=1,
        )
    assert raised.value.server_name == "hanging"
    assert raised.value.retryable is True
    assert cleaned_up.is_set()


@pytest.mark.anyio
async def test_discovery_retries_share_one_server_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def fail(
        self: UserMCPClient, name: str, config: MCPHttpServerConfig
    ) -> dict[str, MCPToolDefinition]:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("synthetic private diagnostic")

    monkeypatch.setattr(UserMCPClient, "_discover_server_tools_once", fail)
    monkeypatch.setattr(
        user_client, "MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS", 0.02, raising=False
    )
    with pytest.raises(UserMCPDiscoveryUnavailableError):
        await asyncio.wait_for(
            UserMCPClient([_mcp_server("retrying")]).discover_tools(fail_on_error=True),
            timeout=1,
        )
    assert attempts == 1


@pytest.mark.anyio
async def test_discovery_servers_share_total_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contacted: list[str] = []

    async def hang(
        self: UserMCPClient, name: str, config: MCPHttpServerConfig
    ) -> dict[str, MCPToolDefinition]:
        contacted.append(name)
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(UserMCPClient, "_discover_server_tools", hang)
    monkeypatch.setattr(
        user_client, "MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS", 1, raising=False
    )
    monkeypatch.setattr(
        user_client, "MCP_DISCOVERY_TIMEOUT_SECONDS", 0.02, raising=False
    )
    result = await asyncio.wait_for(
        UserMCPClient(
            [_mcp_server("first"), _mcp_server("second")]
        ).discover_tools_detailed(),
        timeout=1,
    )
    assert contacted == ["first"]
    assert set(result.failed_servers) == {"first", "second"}


@pytest.mark.anyio
async def test_strict_discovery_raises_budget_error_for_uncontacted_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contacted: list[str] = []

    async def record(
        self: UserMCPClient, name: str, config: MCPHttpServerConfig
    ) -> dict[str, MCPToolDefinition]:
        contacted.append(name)
        return {}

    monkeypatch.setattr(UserMCPClient, "_discover_server_tools", record)
    monkeypatch.setattr(user_client, "MCP_DISCOVERY_TIMEOUT_SECONDS", 0, raising=False)
    with pytest.raises(UserMCPDiscoveryBudgetExceededError) as raised:
        await UserMCPClient([_mcp_server("first")]).discover_tools(fail_on_error=True)
    assert contacted == []
    assert raised.value.server_name == "first"


@pytest.mark.anyio
async def test_discovery_cancellation_is_not_an_mcp_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def hang(
        self: UserMCPClient, name: str, config: MCPHttpServerConfig
    ) -> dict[str, MCPToolDefinition]:
        started.set()
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(UserMCPClient, "_discover_server_tools", hang)
    task = asyncio.create_task(
        UserMCPClient([_mcp_server("cancelled")]).discover_tools(fail_on_error=True)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


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


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://broken.example/mcp")
    return httpx.HTTPStatusError(
        "status error",
        request=request,
        response=httpx.Response(status_code, request=request),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_type", "retryable"),
    [
        (_http_status_error(401), UserMCPDiscoveryAuthError, None),
        (_http_status_error(403), UserMCPDiscoveryAuthError, None),
        (_http_status_error(503), UserMCPDiscoveryUnavailableError, True),
        (_http_status_error(408), UserMCPDiscoveryUnavailableError, True),
        (_http_status_error(429), UserMCPDiscoveryUnavailableError, True),
        (TimeoutError(), UserMCPDiscoveryTimeoutError, True),
        (httpx.ReadTimeout("synthetic timeout"), UserMCPDiscoveryTimeoutError, True),
        (httpx.ConnectError("refused"), UserMCPDiscoveryUnavailableError, True),
        (_http_status_error(404), UserMCPDiscoveryUnavailableError, False),
        (
            MCPResponseTooLargeError(16 * 1024 * 1024, observed=17_000_000),
            UserMCPDiscoveryUnavailableError,
            False,
        ),
        (
            McpError(ErrorData(code=METHOD_NOT_FOUND, message="Method not found")),
            UserMCPDiscoveryUnavailableError,
            False,
        ),
        (ValueError("bad schema"), UserMCPDiscoveryError, None),
    ],
)
@pytest.mark.parametrize("nested_in_group", [False, True])
async def test_discover_tools_strict_mode_raises_typed_errors(
    monkeypatch: pytest.MonkeyPatch,
    nested_in_group: bool,
    error: BaseException,
    expected_type: type[UserMCPDiscoveryError],
    retryable: bool | None,
) -> None:
    async def fake_discover_server_tools(
        self: UserMCPClient,
        server_name: str,
        config: MCPHttpServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        del self, server_name, config
        # Wrap like fastmcp/anyio do so the typed mapping must walk both the
        # cause chain and ExceptionGroup members.
        if nested_in_group:
            raise RuntimeError("Client failed to connect") from BaseExceptionGroup(
                "unhandled errors in a TaskGroup", [error]
            )
        raise RuntimeError("Client failed to connect") from error

    monkeypatch.setattr(
        UserMCPClient,
        "_discover_server_tools",
        fake_discover_server_tools,
    )
    client = UserMCPClient([_mcp_server("broken")])

    with pytest.raises(UserMCPDiscoveryError) as exc_info:
        await client.discover_tools(fail_on_error=True)

    assert type(exc_info.value) is expected_type
    assert exc_info.value.server_name == "broken"
    if retryable is not None:
        assert isinstance(exc_info.value, UserMCPDiscoveryUnavailableError)
        assert exc_info.value.retryable is retryable


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
    assert isinstance(client._transport, BoundedResponseTransport)


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


def test_flatten_single_text_block_is_unchanged() -> None:
    """Regression: the common single-block case keeps its exact prior value."""
    blocks: list[ContentBlock] = [TextContent(type="text", text="plain result")]

    assert flatten_mcp_content_blocks(blocks) == "plain result"


def test_flatten_replaces_lone_surrogates_instead_of_raising() -> None:
    """Lone surrogates cannot cross JSON serialization; replace, don't raise."""
    blocks: list[ContentBlock] = [TextContent(type="text", text="ok\ud800end")]

    flattened = flatten_mcp_content_blocks(blocks)

    assert "\ud800" not in flattened
    assert "ok" in flattened
    assert "end" in flattened
    flattened.encode("utf-8")


def test_flatten_multiblock_under_cap_is_identical_join() -> None:
    """Multi-block under-cap output is the exact "\\n\\n"-joined text."""
    bodies = ["first block", "second block\nwith newline", "third"]
    blocks: list[ContentBlock] = [
        TextContent(type="text", text=body) for body in bodies
    ]

    flattened = flatten_mcp_content_blocks(blocks)

    assert flattened == "\n\n".join(bodies)


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


@pytest.mark.anyio
async def test_call_tool_converts_response_too_large_to_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The byte-cap error becomes a descriptive ToolError, not a raw error."""
    from fastmcp.exceptions import ToolError

    from tracecat.agent.mcp.http_limits import MCPResponseTooLargeError

    class _StubClient:
        def __init__(self, transport: Any) -> None:
            del transport

        async def __aenter__(self) -> "_StubClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
            del tool_name, args
            # Mirror the tools/call surfacing: bare error under an anyio group.
            group = ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [MCPResponseTooLargeError(16 * 1024 * 1024, observed=17_000_000)],
            )
            raise MCPResponseTooLargeError(
                16 * 1024 * 1024, observed=17_000_000
            ) from group

    monkeypatch.setattr("tracecat.agent.mcp.user_client.Client", _StubClient)
    client = UserMCPClient([_mcp_server("github")])

    with pytest.raises(ToolError, match="16 MiB"):
        await client.call_tool("github", "big_tool", {})


@pytest.mark.anyio
async def test_call_tool_propagates_cancellation_carrying_cap_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CancelledError with the cap error in __context__ must not become ToolError."""
    import asyncio

    from tracecat.agent.mcp.http_limits import MCPResponseTooLargeError

    class _StubClient:
        def __init__(self, transport: Any) -> None:
            del transport

        async def __aenter__(self) -> "_StubClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
            del tool_name, args
            # Teardown cancellation carrying the cap error as its __context__;
            # implicit chaining is the point, so keep the bare re-raise.
            try:
                raise MCPResponseTooLargeError(16 * 1024 * 1024, observed=17_000_000)
            except MCPResponseTooLargeError:
                raise asyncio.CancelledError()  # noqa: B904

    monkeypatch.setattr("tracecat.agent.mcp.user_client.Client", _StubClient)
    client = UserMCPClient([_mcp_server("github")])

    with pytest.raises(asyncio.CancelledError):
        await client.call_tool("github", "big_tool", {})


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", ["shared_timeout", "server_timeout", "inner_timeout"]
)
@pytest.mark.parametrize("suppress_cancellation", [False, True])
async def test_discovery_respects_fired_timeout_before_clock_deadline(
    monkeypatch: pytest.MonkeyPatch, failure: str, suppress_cancellation: bool
) -> None:
    contacted: list[str] = []
    deadlines: list[float] = []
    loop = asyncio.get_running_loop()

    class EarlyTimeout:
        def __init__(self, deadline: float) -> None:
            self.timeout = asyncio.Timeout(deadline)

        async def __aenter__(self) -> asyncio.Timeout:
            result = await self.timeout.__aenter__()
            if failure != "inner_timeout":
                # Deterministically simulate an early timer callback while the
                # discovery clock remains well before the requested deadline.
                self.timeout.reschedule(loop.time())
            return result

        async def __aexit__(self, *args: Any) -> bool | None:
            return await self.timeout.__aexit__(*args)

        def expired(self) -> bool:
            return self.timeout.expired()

    def timeout_at(deadline: float) -> EarlyTimeout:
        deadlines.append(deadline)
        return EarlyTimeout(deadline)

    async def discover(
        self: UserMCPClient, name: str, config: MCPHttpServerConfig
    ) -> dict[str, MCPToolDefinition]:
        contacted.append(name)
        if failure == "inner_timeout":
            raise TimeoutError("Server request timed out")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not suppress_cancellation:
                raise
        return {}

    monkeypatch.setattr(user_client.asyncio, "timeout_at", timeout_at)
    monkeypatch.setattr(UserMCPClient, "_discover_server_tools", discover)
    monkeypatch.setattr(user_client, "MCP_DISCOVERY_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(
        user_client,
        "MCP_SERVER_DISCOVERY_TIMEOUT_SECONDS",
        120 if failure != "server_timeout" else 30,
    )
    result = await UserMCPClient(
        [_mcp_server("first"), _mcp_server("second")]
    ).discover_tools_detailed()
    assert loop.time() < min(deadlines)
    assert contacted == (
        ["first"] if failure == "shared_timeout" else ["first", "second"]
    )
    expected_failures = {"first", "second"}
    if suppress_cancellation and failure != "inner_timeout":
        expected_failures = {"second"} if failure == "shared_timeout" else set()
    assert set(result.failed_servers) == expected_failures
    if failure == "shared_timeout":
        assert result.failed_servers["second"] == "UserMCPDiscoveryBudgetExceededError"


@pytest.mark.anyio
@pytest.mark.parametrize("fail_on_error", [False, True])
async def test_discovery_isolates_deadline_setup_failure(
    monkeypatch: pytest.MonkeyPatch, fail_on_error: bool
) -> None:
    contacted: list[str] = []

    async def discover(
        self: UserMCPClient, name: str, config: MCPHttpServerConfig
    ) -> dict[str, MCPToolDefinition]:
        contacted.append(name)
        return {}

    monkeypatch.setattr(UserMCPClient, "_discover_server_tools", discover)
    first = _mcp_server("first")
    first["timeout"] = -(10**1000)
    client = UserMCPClient([first, _mcp_server("second")])
    if fail_on_error:
        with pytest.raises(UserMCPDiscoveryError) as raised:
            await client.discover_tools_detailed(fail_on_error=True)
        assert raised.value.server_name == "first"
        assert isinstance(raised.value.__cause__, OverflowError)
        assert contacted == []
    else:
        result = await client.discover_tools_detailed()
        assert result.failed_servers == {"first": "OverflowError"}
        assert contacted == ["second"]
