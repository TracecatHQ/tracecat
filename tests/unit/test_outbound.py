"""Exercise the real HTTPX/HTTPcore stack with a recording socket backend."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock

import anyio
import certifi
import httpcore
import litellm
import orjson
import pytest
from cryptography.fernet import Fernet
from litellm.anthropic_interface import acreate as create_anthropic_message
from litellm.caching.dual_cache import DualCache
from litellm.caching.llm_caching_handler import LLMClientCache
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    InternalServerError,
    PermissionDeniedError,
)
from litellm.llms.base_llm.chat.transformation import BaseLLMException
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
    monkeypatch.setattr(litellm, "in_memory_llm_clients_cache", LLMClientCache())
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
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


@pytest.mark.anyio
@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
async def test_outbound_preserves_operator_ca_settings(
    variable: str,
    tmp_path: Path,
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    value = certifi.where() if variable == "SSL_CERT_FILE" else str(tmp_path)
    monkeypatch.setenv(variable, value)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    create_context = Mock(wraps=ssl.create_default_context)
    monkeypatch.setattr(ssl, "create_default_context", create_context)
    async with create_outbound_http_client() as client:
        await client.get("https://8.8.8.8")
    create_context.assert_called_once_with(
        **{"cafile" if variable == "SSL_CERT_FILE" else "capath": value}
    )
    assert network.connections == [("8.8.8.8", 443)]
    assert network.streams[0].ssl_context is not None
    assert network.streams[0].ssl_context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.anyio
async def test_explicit_tls_context_overrides_environment(
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ssl.create_default_context()
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/synthetic-ca.pem")
    async with create_outbound_http_client(verify=context) as client:
        await client.get("https://8.8.8.8")
    assert network.streams[0].ssl_context is context


@pytest.mark.anyio
@pytest.mark.parametrize("status", [307, 308])
@pytest.mark.parametrize("location", ["/canonical", "http://127.0.0.1/private"])
async def test_gateway_redirects_preserve_only_same_origin(
    status: int,
    location: str,
    network: RecordingBackend,
) -> None:
    network.responses.insert(
        0,
        (
            f"HTTP/1.1 {status} Redirect\r\nLocation: {location}\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode(),
    )
    handler = OutboundLLMHTTPHandler()
    try:
        if location.startswith("http:"):
            with pytest.raises(DisallowedUrlError):
                await handler.post("https://8.8.8.8/start", data={"model": "synthetic"})
            assert network.connections == [("8.8.8.8", 443)]
        else:
            response = await handler.post(
                "https://8.8.8.8/start", data={"model": "synthetic"}
            )
            assert response is not None
            assert response.status_code == 200
            assert len(network.connections) == 2
            wire = b"".join(network.streams[1].writes)
            assert b"POST /canonical " in wire
            assert b"synthetic" in wire
    finally:
        await handler.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider",
    [
        "openai",
        "anthropic",
        "mistral",
        "ollama",
        "vllm",
        "openrouter",
        "litellm",
        "azure_openai",
        "azure_ai",
        "azure_openai_cloudflare",
    ],
)
@pytest.mark.parametrize("private", [True, False])
async def test_gateway_credential_urls_use_guarded_transport(
    provider: str,
    private: bool,
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = "http://127.0.0.1/v1" if private else "https://8.8.8.8/v1"
    if provider == "azure_openai_cloudflare":
        provider = "azure_openai"
        base += "/gateway.ai.cloudflare.com"
    prefix = "AZURE" if provider.startswith("azure") else provider.upper()
    creds = {
        f"{prefix}_API_KEY": "synthetic",
        f"{prefix}_BASE_URL": base,
        "AZURE_API_BASE": base,
        "AZURE_API_VERSION": "2024-02-01",
        "AZURE_DEPLOYMENT_NAME": "synthetic-model",
        "AZURE_AI_MODEL_NAME": "synthetic-model",
    }
    monkeypatch.setattr(
        "tracecat.agent.gateway.get_provider_credentials", AsyncMock(return_value=creds)
    )
    handler = TracecatCallbackHandler()
    data = await handler.async_pre_call_hook(
        user_api_key_dict=UserAPIKeyAuth(
            api_key="synthetic",
            metadata={
                "workspace_id": str(uuid.uuid4()),
                "organization_id": str(uuid.uuid4()),
                "model": "synthetic-model",
                "provider": provider,
                "model_settings": {},
                "use_workspace_credentials": True,
            },
        ),
        cache=DualCache(),
        data={},
        call_type="completion",
    )
    body = {
        "id": "synthetic",
        "object": "chat.completion",
        "created": 1,
        "model": "synthetic-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    if provider == "anthropic":
        body = {
            "id": "synthetic",
            "type": "message",
            "role": "assistant",
            "model": "synthetic-model",
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    elif provider == "ollama":
        body = {
            "model": "synthetic-model",
            "message": {"role": "assistant", "content": "hello"},
            "done": True,
            "prompt_eval_count": 1,
            "eval_count": 1,
        }
    encoded = orjson.dumps(body)
    network.responses = [
        f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode()
        + encoded
    ]
    try:
        if private:
            with pytest.raises(
                (
                    APIError,
                    APIConnectionError,
                    InternalServerError,
                    PermissionDeniedError,
                )
            ):
                await litellm.acompletion(
                    **data,
                    messages=[{"role": "user", "content": "hello"}],
                    num_retries=0,
                    max_retries=0,
                )
            assert network.connections == []
        else:
            result = await litellm.acompletion(
                **data,
                messages=[{"role": "user", "content": "hello"}],
                num_retries=0,
                max_retries=0,
            )
            assert isinstance(result, litellm.ModelResponse)
            assert result.choices[0].message.content == "hello"
            assert network.connections == [("8.8.8.8", 443)]
    finally:
        if handler._outbound_http_handler is not None:
            await handler._outbound_http_handler.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,protocol",
    [
        (provider, protocol)
        for provider in ("openai", "azure", "anthropic")
        for protocol in ("completion", "responses", "anthropic_messages", "bridge")
        if (provider, protocol) != ("anthropic", "bridge")
    ],
)
@pytest.mark.parametrize("stream", [False, True])
async def test_litellm_protocol_switches_cannot_bypass_guard(
    provider: str,
    protocol: str,
    stream: bool,
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Assert the policy ran, not merely that an SDK rejected our test input.
    resolver = AsyncMock(wraps=resolve_outbound_addresses)
    monkeypatch.setattr("tracecat.outbound.resolve_outbound_addresses", resolver)
    model = f"{provider}/synthetic-model"
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": "synthetic",
        "api_base": "http://127.0.0.1/v1",
        "num_retries": 0,
        "max_retries": 0,
        "stream": stream,
    }
    if provider == "azure":
        kwargs["api_version"] = "2024-02-01"
    with pytest.raises(
        (
            APIError,
            APIConnectionError,
            InternalServerError,
            PermissionDeniedError,
            DisallowedUrlError,
            BaseLLMException,
        )
    ):
        if protocol == "responses":
            await litellm.aresponses(**kwargs, input="hello")
        elif protocol == "anthropic_messages":
            await create_anthropic_message(
                **kwargs, messages=[{"role": "user", "content": "hello"}], max_tokens=10
            )
        else:
            if protocol == "bridge":
                kwargs["model"] = f"{provider}/responses/synthetic-model"
            result = await litellm.acompletion(
                **kwargs, messages=[{"role": "user", "content": "hello"}]
            )
            if stream:
                assert isinstance(result, litellm.CustomStreamWrapper)
                async for _ in result:
                    pass
    resolver.assert_awaited()
    assert network.connections == []


@pytest.mark.anyio
async def test_openai_responses_bridge_preserves_public_requests(
    network: RecordingBackend,
) -> None:
    body = orjson.dumps(
        {
            "id": "resp_synthetic",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "synthetic-model",
            "output": [
                {
                    "id": "msg_synthetic",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "hello", "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    )
    network.responses = [
        f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    ]
    response = await litellm.acompletion(
        model="openai/responses/synthetic-model",
        api_base="https://8.8.8.8/v1",
        api_key="synthetic",
        messages=[{"role": "user", "content": "hello"}],
        num_retries=0,
        max_retries=0,
    )
    assert isinstance(response, litellm.ModelResponse)
    assert response.choices[0].message.content == "hello"
    assert network.connections == [("8.8.8.8", 443)]
    assert b"POST /v1/responses " in b"".join(network.streams[0].writes)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["stall", "error", "timeout"])
async def test_address_failover_preserves_shared_deadline(
    failure: str,
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                dns_answer("8.8.8.8"),
                dns_answer("1.1.1.1"),
            ]
        ),
    )
    cancelled = asyncio.Event()
    attempts: list[str] = []
    winner = RecordingStream(b"")

    async def connect(host: str, *args, **kwargs):
        attempts.append(host)
        assert tuple(kwargs["socket_options"]) == (
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        )
        if host == "1.1.1.1":
            return winner
        if failure == "error":
            raise httpcore.ConnectError("synthetic refusal")
        if failure == "timeout":
            raise httpcore.ConnectTimeout("synthetic timeout")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(network, "connect_tcp", connect)
    # Immediate failures must not wait for the 250ms stagger.
    timeout = 1.0 if failure == "stall" else 0.15
    stream = await OutboundNetworkBackend().connect_tcp(
        "models.example.com",
        443,
        timeout=timeout,
        socket_options=iter([(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]),
    )
    assert stream is winner
    assert attempts == ["8.8.8.8", "1.1.1.1"]
    assert not winner.closed
    if failure == "stall":
        assert cancelled.is_set()
    await stream.aclose()


@pytest.mark.anyio
async def test_all_stalled_addresses_share_one_timeout(
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                dns_answer("8.8.8.8"),
                dns_answer("1.1.1.1"),
            ]
        ),
    )
    started: list[str] = []
    cancelled: list[str] = []

    async def connect(host: str, *args, **kwargs):
        started.append(host)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(host)

    monkeypatch.setattr(network, "connect_tcp", connect)
    with pytest.raises(httpcore.ConnectTimeout):
        await OutboundNetworkBackend().connect_tcp(
            "models.example.com", 443, timeout=0.4
        )
    assert started == ["8.8.8.8", "1.1.1.1"]
    assert sorted(cancelled) == sorted(started)


@pytest.mark.anyio
async def test_simultaneous_address_success_closes_loser(
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                dns_answer("8.8.8.8"),
                dns_answer("1.1.1.1"),
            ]
        ),
    )
    ready = anyio.Event()
    streams: list[RecordingStream] = []

    async def connect(host: str, *args, **kwargs):
        stream = RecordingStream(b"")
        streams.append(stream)
        if len(streams) == 2:
            ready.set()
        # Simulate a socket completion racing cancellation of the losing dial.
        with anyio.CancelScope(shield=True):
            await ready.wait()
        return stream

    monkeypatch.setattr(network, "connect_tcp", connect)
    winner = await OutboundNetworkBackend().connect_tcp(
        "models.example.com", 443, timeout=1
    )
    assert len(streams) == 2
    assert isinstance(winner, RecordingStream)
    assert not winner.closed
    assert all(stream.closed for stream in streams if stream is not winner)
    await winner.aclose()


@pytest.mark.anyio
async def test_cancellation_closes_winner_during_loser_cleanup(
    network: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                dns_answer("8.8.8.8"),
                dns_answer("1.1.1.1"),
            ]
        ),
    )
    cleanup_started = anyio.Event()
    finish_cleanup = anyio.Event()
    winner = RecordingStream(b"")

    async def connect(host: str, *args, **kwargs):
        if host == "1.1.1.1":
            return winner
        try:
            await anyio.sleep_forever()
        finally:
            with anyio.CancelScope(shield=True):
                cleanup_started.set()
                await finish_cleanup.wait()

    monkeypatch.setattr(network, "connect_tcp", connect)
    task = asyncio.create_task(
        OutboundNetworkBackend().connect_tcp("models.example.com", 443)
    )
    async with asyncio.timeout(2):
        await cleanup_started.wait()
        task.cancel()
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert winner.closed
