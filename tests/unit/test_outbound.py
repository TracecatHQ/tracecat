"""Exercise the real HTTPX/HTTPcore stack with a recording socket backend."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import uuid
from collections.abc import Iterable
from typing import Literal
from unittest.mock import AsyncMock

import httpcore
import litellm
import orjson
import pytest
from cryptography.fernet import Fernet
from litellm.caching.dual_cache import DualCache
from litellm.exceptions import APIError
from litellm.proxy._types import UserAPIKeyAuth
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.gateway import TracecatCallbackHandler
from tracecat.agent.gateway_outbound import OutboundLLMHTTPHandler
from tracecat.agent.mcp.user_client import _create_transport, list_remote_mcp_tools
from tracecat.agent.provider.service import (
    AgentCustomProviderService,
    fetch_openai_compatible_models,
)
from tracecat.auth.types import Role
from tracecat.integrations.schemas import MCPHttpIntegrationTestConnectionRequest
from tracecat.integrations.service import IntegrationService
from tracecat.network import DisallowedUrlError
from tracecat.outbound import (
    OutboundNetworkBackend,
    SocketOption,
    create_outbound_http_client,
    resolve_outbound_addresses,
)


class RecordingStream(httpcore.AsyncNetworkStream):
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.writes: list[bytes] = []
        self.server_hostname: str | None = None
        self.ssl_context: ssl.SSLContext | None = None
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        response, self.response = self.response[:max_bytes], self.response[max_bytes:]
        return response

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.ssl_context = ssl_context
        self.server_hostname = server_hostname
        return self

    async def aclose(self) -> None:
        self.closed = True


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []
        self.streams: list[RecordingStream] = []
        self.responses = [
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
        ]

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connections.append((host, port))
        stream = RecordingStream(self.responses.pop(0))
        self.streams.append(stream)
        return stream


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> RecordingBackend:
    backend = RecordingBackend()
    monkeypatch.setattr("tracecat.outbound.AnyIOBackend", lambda: backend)
    monkeypatch.setattr(config, "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS", ())
    return backend


def dns_answer(address: str) -> tuple:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "192.0.2.1",
        "[::1]",
        "[::]",
        "[fc00::1]",
        "[fe80::1]",
        "[ff02::1]",
        "[::ffff:127.0.0.1]",
    ],
)
async def test_prohibited_literals_never_open_a_socket(
    host: str, network: RecordingBackend
) -> None:
    async with create_outbound_http_client() as client:
        with pytest.raises(DisallowedUrlError):
            await client.get(
                f"http://{host}/", headers={"Authorization": "Bearer synthetic"}
            )
    assert network.connections == []


@pytest.mark.anyio
@pytest.mark.parametrize("answers", [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], []])
async def test_dns_rejects_entire_answer_set(
    answers: list[str], network: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = AsyncMock(return_value=[dns_answer(address) for address in answers])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    async with create_outbound_http_client() as client:
        with pytest.raises(DisallowedUrlError):
            await client.get("https://models.example.com/models")
    assert network.connections == []


@pytest.mark.anyio
async def test_dns_pinning_preserves_host_and_tls_identity(
    network: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = AsyncMock(
        side_effect=[[dns_answer("8.8.8.8")], [dns_answer("127.0.0.1")]]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    async with create_outbound_http_client() as client:
        response = await client.get("https://models.example.com/models")
        assert response.status_code == 200
        # A later connection must resolve again, rather than trust validation
        # performed when the provider was created or the client instantiated.
        with pytest.raises(DisallowedUrlError):
            await client.get("https://models.example.com/models")
    assert resolver.await_count == 2
    assert network.connections == [("8.8.8.8", 443)]
    stream = network.streams[0]
    assert b"Host: models.example.com\r\n" in b"".join(stream.writes)
    assert stream.server_hostname == "models.example.com"
    assert stream.ssl_context is not None
    assert stream.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert stream.ssl_context.check_hostname
    assert stream.closed


@pytest.mark.anyio
async def test_same_origin_redirect_rechecks_dns(
    network: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    network.responses = [
        b"HTTP/1.1 307 Redirect\r\nLocation: /new\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    ]
    resolver = AsyncMock(
        side_effect=[[dns_answer("8.8.8.8")], [dns_answer("10.0.0.1")]]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    async with create_outbound_http_client(
        origin_url="https://mcp.example.com", follow_redirects=True
    ) as client:
        with pytest.raises(DisallowedUrlError):
            await client.get("https://mcp.example.com/")
    assert network.connections == [("8.8.8.8", 443)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "target",
    ["http://127.0.0.1/", "https://other.example.com/", "http://mcp.example.com/"],
)
async def test_cross_origin_redirect_cannot_forward_credentials(
    target: str, network: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    network.responses = [
        f"HTTP/1.1 307 Redirect\r\nLocation: {target}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
    ]
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(return_value=[dns_answer("8.8.8.8")]),
    )
    transport = _create_transport(
        "https://mcp.example.com/mcp", "http", {"X-API-Key": "synthetic"}
    )
    assert transport.httpx_client_factory is not None
    async with transport.httpx_client_factory(headers=transport.headers) as client:
        with pytest.raises(DisallowedUrlError, match="Cross-origin"):
            await client.get("https://mcp.example.com/mcp")
    assert network.connections == [("8.8.8.8", 443)]


@pytest.mark.anyio
async def test_operator_exception_is_narrow(
    network: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS",
        (ipaddress.ip_network("10.0.0.5/32"),),
    )
    async with create_outbound_http_client() as client:
        assert (await client.get("http://10.0.0.5/")).status_code == 200
        with pytest.raises(DisallowedUrlError):
            await client.get("http://10.0.0.6/")
    assert network.connections == [("10.0.0.5", 80)]


@pytest.mark.anyio
async def test_model_discovery_blocks_before_sending_credentials(
    network: RecordingBackend,
) -> None:
    with pytest.raises(DisallowedUrlError):
        await fetch_openai_compatible_models(
            base_url="http://127.0.0.1", api_key="synthetic", timeout=1
        )
    assert network.connections == []


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_real_mcp_handshake_cannot_connect_to_private_host(
    transport: Literal["http", "sse"], network: RecordingBackend
) -> None:
    # FastMCP wraps transport failures, so the invariant is no socket opened.
    with pytest.raises((DisallowedUrlError, RuntimeError, ExceptionGroup)):
        await list_remote_mcp_tools(
            {
                "type": "http",
                "name": "synthetic",
                "url": "http://127.0.0.1/mcp",
                "transport": transport,
                "headers": {"Authorization": "Bearer synthetic"},
            }
        )
    assert network.connections == []


@pytest.mark.anyio
async def test_dns_timeout_is_bounded(
    network: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stalled_dns(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", stalled_dns)
    with pytest.raises(httpcore.ConnectTimeout):
        await OutboundNetworkBackend().connect_tcp(
            "models.example.com", 443, timeout=0.01
        )
    assert network.connections == []


@pytest.mark.anyio
async def test_scoped_ipv6_is_rejected(network: RecordingBackend) -> None:
    with pytest.raises(DisallowedUrlError):
        await resolve_outbound_addresses("fe80::1%lo0", 80)
    assert network.connections == []


@pytest.mark.parametrize("override", ["proxy", "mounts", "transport", "trust_env"])
def test_transport_bypasses_are_rejected(override: str) -> None:
    with pytest.raises(ValueError):
        create_outbound_http_client(origin_url=None, **{override: True})


@pytest.mark.anyio
async def test_managed_custom_provider_uses_guarded_handler(
    network: RecordingBackend,
) -> None:
    handler = OutboundLLMHTTPHandler()
    try:
        with pytest.raises(APIError) as exc_info:
            await litellm.acompletion(
                model="vendor/synthetic-model",
                custom_llm_provider="hosted_vllm",
                api_base="http://127.0.0.1/v1",
                api_key="synthetic",
                messages=[{"role": "user", "content": "hello"}],
                client=handler,
                num_retries=0,
                max_retries=0,
            )
        assert exc_info.value.status_code == 403
        assert network.connections == []
    finally:
        await handler.close()


@pytest.mark.anyio
async def test_managed_custom_provider_public_request_preserves_model(
    network: RecordingBackend,
) -> None:
    body = orjson.dumps(
        {
            "id": "synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": "vendor/synthetic-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )
    network.responses = [
        f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    ]
    handler = OutboundLLMHTTPHandler()
    try:
        await litellm.acompletion(
            model="vendor/synthetic-model",
            custom_llm_provider="hosted_vllm",
            api_base="https://8.8.8.8/v1",
            api_key="synthetic",
            messages=[{"role": "user", "content": "hello"}],
            client=handler,
            num_retries=0,
            max_retries=0,
        )
    finally:
        await handler.close()
    assert network.connections == [("8.8.8.8", 443)]
    wire = b"".join(network.streams[0].writes)
    assert b"/v1/chat/completions" in wire
    assert (
        orjson.loads(wire.split(b"\r\n\r\n", 1)[1])["model"] == "vendor/synthetic-model"
    )


@pytest.mark.anyio
async def test_mcp_connectivity_service_blocks_private_destination(
    session: AsyncSession,
    test_role: Role,
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    service = IntegrationService(session=session, role=test_role)
    result = await service.test_mcp_http_connection(
        params=MCPHttpIntegrationTestConnectionRequest(
            server_uri="http://127.0.0.1/mcp"
        )
    )
    assert result.success is False
    assert network.connections == []


@pytest.mark.anyio
async def test_provider_validation_service_blocks_private_destination(
    session: AsyncSession, test_role: Role, network: RecordingBackend
) -> None:
    service = AgentCustomProviderService(session=session, role=test_role)
    assert not await service.validate_provider("http://127.0.0.1", api_key="synthetic")
    assert network.connections == []


@pytest.mark.anyio
async def test_gateway_hook_installs_guard_for_custom_provider(
    network: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.gateway.get_provider_credentials",
        AsyncMock(
            return_value={
                "CUSTOM_MODEL_PROVIDER_BASE_URL": "http://127.0.0.1/v1",
                "CUSTOM_MODEL_PROVIDER_API_KEY": "synthetic",
                "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "vendor/synthetic-model",
            }
        ),
    )
    handler = TracecatCallbackHandler()
    data = await handler.async_pre_call_hook(
        user_api_key_dict=UserAPIKeyAuth(
            api_key="synthetic",
            metadata={
                "workspace_id": str(uuid.uuid4()),
                "organization_id": str(uuid.uuid4()),
                "model": "synthetic-model",
                "provider": "custom-model-provider",
                "model_settings": {},
                "use_workspace_credentials": True,
            },
        ),
        cache=DualCache(),
        data={},
        call_type="completion",
    )
    try:
        with pytest.raises(APIError) as exc_info:
            await litellm.acompletion(
                **data,
                messages=[{"role": "user", "content": "hello"}],
                num_retries=0,
                max_retries=0,
            )
        assert exc_info.value.status_code == 403
        assert network.connections == []
    finally:
        assert handler._outbound_http_handler is not None
        await handler._outbound_http_handler.close()
