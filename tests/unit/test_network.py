"""Tests for connect-time outbound HTTP network policy."""

from __future__ import annotations

import asyncio
import socket
import ssl
import traceback
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any, cast

import httpcore
import httpx
import pytest

from tracecat.network import (
    DisallowedUrlError,
    HostResolutionError,
    HttpEgressPolicy,
    HttpEgressPurpose,
    HttpOrigin,
    SocketInfo,
    configured_http_egress_policy,
    validate_url_resolves_public_async,
)
from tracecat.outbound_http import guarded_async_client

_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
_OK_CLOSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"


def _socket_info(address: str) -> SocketInfo:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    socket_address: tuple[str, int] | tuple[str, int, int, int]
    if family == socket.AF_INET6:
        socket_address = (address, 443, 0, 0)
    else:
        socket_address = (address, 443)
    return SocketInfo(
        address_family=family,
        socket_kind=socket.SOCK_STREAM,
        protocol=socket.IPPROTO_TCP,
        canonical_name="",
        socket_address=socket_address,
    )


class _ResponseStream(httpcore.AsyncNetworkStream):
    """In-memory HTTP/1.1 stream for transport tests."""

    def __init__(
        self,
        tls_server_names: list[str | None],
        responses: Iterable[bytes],
    ) -> None:
        self._responses = deque(responses)
        self._response = b""
        self._request = b""
        self._tls_server_names = tls_server_names

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        data, self._response = self._response[:max_bytes], self._response[max_bytes:]
        return data

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._request += buffer
        if b"\r\n\r\n" in self._request and not self._response:
            self._response = self._responses.popleft()
            self._request = b""

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._tls_server_names.append(server_hostname)
        return self

    def get_extra_info(self, info: str) -> Any:
        if info == "is_readable":
            return False
        return None


type ResponseFactory = Callable[[str, int], Iterable[bytes]]


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    """Record the numeric host selected by the guarded backend."""

    def __init__(self, responses: ResponseFactory | None = None) -> None:
        self.hosts: list[str] = []
        self.tls_server_names: list[str | None] = []
        self._responses = responses or (lambda _host, _connection: (_OK,))

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        connection = len(self.hosts)
        self.hosts.append(host)
        return _ResponseStream(
            self.tls_server_names,
            self._responses(host, connection),
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise AssertionError("Unexpected Unix socket connection")

    async def sleep(self, seconds: float) -> None:
        return None


class _FirstAddressTimeoutBackend(_RecordingBackend):
    """Spend the first address budget, then connect to the fallback."""

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if not self.hosts:
            self.hosts.append(host)
            assert timeout is not None
            await asyncio.sleep(timeout)
            raise httpcore.ConnectTimeout("First address timed out")
        return await super().connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _AlwaysTimeoutBackend(_RecordingBackend):
    """Fail every numeric address with a connect timeout."""

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.hosts.append(host)
        raise httpcore.ConnectTimeout("Synthetic address timeout")


@pytest.mark.anyio
async def test_guarded_transport_connects_to_validated_numeric_address() -> None:
    backend = _RecordingBackend()
    resolver_calls: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        resolver_calls.append((host, port))
        return (_socket_info("93.184.216.34"),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        response = await client.get("http://example.test/resource")

    assert response.text == "OK"
    assert resolver_calls == [("example.test", 80)]
    assert backend.hosts == ["93.184.216.34"]


@pytest.mark.anyio
async def test_guarded_transport_preserves_hostname_for_tls() -> None:
    backend = _RecordingBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (_socket_info("93.184.216.34"),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        response = await client.get("https://secure.example.test/resource")

    assert response.status_code == 200
    assert backend.hosts == ["93.184.216.34"]
    assert backend.tls_server_names == ["secure.example.test"]


@pytest.mark.anyio
async def test_guarded_transport_rejects_dns_rebinding_before_connect() -> None:
    backend = _RecordingBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (_socket_info("127.0.0.1"),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        with pytest.raises(DisallowedUrlError, match="Host is not allowed"):
            await client.get("http://rebind.example.test/resource")

    assert backend.hosts == []


@pytest.mark.anyio
async def test_guarded_transport_rejects_mixed_public_private_answers() -> None:
    backend = _RecordingBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (
            _socket_info("93.184.216.34"),
            _socket_info("169.254.169.254"),
        )

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        with pytest.raises(DisallowedUrlError, match="Host is not allowed"):
            await client.get("http://mixed.example.test/resource")

    assert backend.hosts == []


@pytest.mark.anyio
async def test_guarded_transport_budgets_timeout_across_resolved_addresses() -> None:
    backend = _FirstAddressTimeoutBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (
            _socket_info("93.184.216.34"),
            _socket_info("142.250.72.14"),
        )

    async with guarded_async_client(
        resolver=resolver,
        backend=backend,
        timeout=httpx.Timeout(0.1),
    ) as client:
        response = await client.get("http://fallback.example.test/resource")

    assert response.status_code == 200
    assert backend.hosts == ["93.184.216.34", "142.250.72.14"]


@pytest.mark.anyio
async def test_guarded_transport_preserves_exhausted_connect_timeout() -> None:
    backend = _AlwaysTimeoutBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (
            _socket_info("93.184.216.34"),
            _socket_info("142.250.72.14"),
        )

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        with pytest.raises(httpx.ConnectTimeout, match="Connection timed out"):
            await client.get("http://timeout.example.test/resource")

    assert backend.hosts == ["93.184.216.34", "142.250.72.14"]


@pytest.mark.anyio
async def test_redirect_to_private_origin_is_revalidated() -> None:
    backend = _RecordingBackend(
        lambda _host, _connection: (
            b"HTTP/1.1 302 Found\r\n"
            b"Location: http://private.example.test/secret\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n\r\n",
        )
    )

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        if host == "private.example.test":
            return (_socket_info("127.0.0.1"),)
        return (_socket_info("93.184.216.34"),)

    async with guarded_async_client(
        resolver=resolver,
        backend=backend,
        follow_redirects=True,
    ) as client:
        with pytest.raises(DisallowedUrlError, match="Host is not allowed"):
            await client.get("http://public.example.test/redirect")

    assert backend.hosts == ["93.184.216.34"]


@pytest.mark.anyio
async def test_same_origin_is_revalidated_when_connection_is_recreated() -> None:
    backend = _RecordingBackend(lambda _host, _connection: (_OK_CLOSE,))
    resolver_calls = 0

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 1:
            return (_socket_info("93.184.216.34"),)
        return (_socket_info("127.0.0.1"),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        first = await client.get("http://rebind.example.test/first")
        with pytest.raises(DisallowedUrlError, match="Host is not allowed"):
            await client.get("http://rebind.example.test/second")

    assert first.status_code == 200
    assert resolver_calls == 2
    assert backend.hosts == ["93.184.216.34"]


@pytest.mark.anyio
async def test_connection_pool_reuses_already_validated_connection() -> None:
    backend = _RecordingBackend(lambda _host, _connection: (_OK, _OK))
    resolver_calls = 0

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        nonlocal resolver_calls
        resolver_calls += 1
        return (_socket_info("93.184.216.34"),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        first = await client.get("http://pool.example.test/first")
        second = await client.get("http://pool.example.test/second")

    assert (first.status_code, second.status_code) == (200, 200)
    assert resolver_calls == 1
    assert backend.hosts == ["93.184.216.34"]


@pytest.mark.anyio
async def test_concurrent_origins_keep_separate_connection_context() -> None:
    backend = _RecordingBackend()
    addresses = {
        "one.example.test": "93.184.216.34",
        "two.example.test": "142.250.72.14",
    }

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        await asyncio.sleep(0)
        return (_socket_info(addresses[host]),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        one, two = await asyncio.gather(
            client.get("http://one.example.test/resource"),
            client.get("http://two.example.test/resource"),
        )

    assert (one.status_code, two.status_code) == (200, 200)
    assert set(backend.hosts) == set(addresses.values())


@pytest.mark.anyio
async def test_idna_hostname_uses_same_normalized_origin_at_connect_time() -> None:
    backend = _RecordingBackend()
    resolver_calls: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        resolver_calls.append((host, port))
        return (_socket_info("93.184.216.34"),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        response = await client.get("http://täst.example/resource")

    assert response.status_code == 200
    assert resolver_calls == [("xn--tst-qla.example", 80)]


@pytest.mark.anyio
async def test_guarded_client_ignores_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:8080")
    backend = _RecordingBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (_socket_info("93.184.216.34"),)

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        response = await client.get("http://example.test/resource")

    assert response.status_code == 200
    assert backend.hosts == ["93.184.216.34"]


def test_guarded_client_rejects_explicit_proxy_and_mount_bypasses() -> None:
    factory = cast(Callable[..., httpx.AsyncClient], guarded_async_client)

    with pytest.raises(TypeError, match="unexpected keyword argument 'proxy'"):
        factory(proxy="http://127.0.0.1:8080")
    with pytest.raises(TypeError, match="unexpected keyword argument 'mounts'"):
        factory(mounts={"http://": httpx.AsyncHTTPTransport()})


@pytest.mark.anyio
async def test_async_dns_resolution_obeys_connect_timeout() -> None:
    backend = _RecordingBackend()
    never = asyncio.Event()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        await never.wait()
        raise AssertionError("Unreachable")

    async with guarded_async_client(
        resolver=resolver,
        backend=backend,
        timeout=httpx.Timeout(0.01),
    ) as client:
        with pytest.raises(httpx.ConnectTimeout, match="Connection timed out"):
            await client.get("http://slow-dns.example.test/resource")

    assert backend.hosts == []


@pytest.mark.anyio
@pytest.mark.parametrize("error_code", [socket.EAI_AGAIN, socket.EAI_NONAME])
async def test_dns_resolution_failure_is_a_retryable_connect_error(
    monkeypatch: pytest.MonkeyPatch,
    error_code: int,
) -> None:
    backend = _RecordingBackend()

    def fail_resolution(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        raise socket.gaierror(error_code, "Name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)

    async with guarded_async_client(backend=backend) as client:
        with pytest.raises(httpx.ConnectError, match="Host could not be resolved"):
            await client.get("http://temporary-dns-failure.example.test/resource")

    assert backend.hosts == []


@pytest.mark.anyio
async def test_empty_dns_result_is_a_retryable_connect_error() -> None:
    backend = _RecordingBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        del host, port
        return ()

    async with guarded_async_client(resolver=resolver, backend=backend) as client:
        with pytest.raises(httpx.ConnectError, match="Host could not be resolved"):
            await client.get("http://empty-dns-result.example.test/resource")

    assert backend.hosts == []


@pytest.mark.anyio
async def test_validation_only_dns_failure_remains_disallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolution(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        raise socket.gaierror(socket.EAI_AGAIN, "Name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)

    with pytest.raises(HostResolutionError, match="Host could not be resolved") as exc:
        await validate_url_resolves_public_async(
            "http://temporary-dns-failure.example.test/resource"
        )

    assert isinstance(exc.value, DisallowedUrlError)


@pytest.mark.anyio
async def test_guarded_transport_allows_exact_operator_approved_private_origin() -> (
    None
):
    backend = _RecordingBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (_socket_info("10.0.0.8"),)

    policy = HttpEgressPolicy.from_allowed_private_origins(
        ["http://llm.internal:11434"]
    )
    async with guarded_async_client(
        policy,
        resolver=resolver,
        backend=backend,
    ) as client:
        response = await client.get("http://llm.internal:11434/v1/models")

    assert response.status_code == 200
    assert backend.hosts == ["10.0.0.8"]


@pytest.mark.anyio
async def test_private_origin_allowance_is_scheme_and_port_specific() -> None:
    backend = _RecordingBackend()

    async def resolver(host: str, port: int) -> tuple[SocketInfo, ...]:
        return (_socket_info("10.0.0.8"),)

    policy = HttpEgressPolicy.from_allowed_private_origins(["https://service.internal"])
    async with guarded_async_client(
        policy,
        resolver=resolver,
        backend=backend,
    ) as client:
        with pytest.raises(DisallowedUrlError, match="Host is not allowed"):
            await client.get("http://service.internal/")

    assert backend.hosts == []


def test_private_origins_are_scoped_to_product_purpose() -> None:
    configured = (
        "llm=http://llm.internal:11434",
        "mcp=https://mcp.internal",
    )

    llm_policy = HttpEgressPolicy.for_purpose(HttpEgressPurpose.LLM, configured)
    mcp_policy = HttpEgressPolicy.for_purpose(HttpEgressPurpose.MCP, configured)

    assert llm_policy == HttpEgressPolicy.from_allowed_private_origins(
        ["http://llm.internal:11434"]
    )
    assert mcp_policy == HttpEgressPolicy.from_allowed_private_origins(
        ["https://mcp.internal"]
    )


def test_configured_policy_rejects_private_origins_in_multi_tenant_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.config.TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        "tracecat.config.TRACECAT__HTTP_EGRESS_ALLOWED_PRIVATE_ORIGINS",
        ("llm=http://llm.internal:11434",),
    )

    with pytest.raises(ValueError, match="multi-tenant deployments"):
        configured_http_egress_policy(HttpEgressPurpose.LLM)


def test_http_origin_normalizes_idna_hostname() -> None:
    assert HttpOrigin.from_url("https://täst.example") == HttpOrigin(
        scheme="https",
        host="xn--tst-qla.example",
        port=443,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test:0",
        "http://example.test:65536",
        "http://[::1",
    ],
)
def test_http_origin_rejects_invalid_ports_and_malformed_hosts(url: str) -> None:
    with pytest.raises(DisallowedUrlError):
        HttpOrigin.from_url(url)


def test_private_origin_config_normalizes_malformed_url_errors() -> None:
    with pytest.raises(ValueError, match="Invalid private HTTP origin"):
        HttpEgressPolicy.from_allowed_private_origins(["http://[::1"])


@pytest.mark.parametrize(
    ("origin", "sensitive_text"),
    [
        ("https://user:synthetic-secret@service.internal", "synthetic-secret"),
        ("https://service.internal/synthetic-secret", "synthetic-secret"),
    ],
)
def test_private_origin_config_errors_redact_rejected_values(
    origin: str,
    sensitive_text: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        HttpEgressPolicy.from_allowed_private_origins([origin])

    assert sensitive_text not in str(exc_info.value)
    assert sensitive_text not in "".join(traceback.format_exception(exc_info.value))


def test_private_origin_config_errors_redact_unknown_purpose() -> None:
    with pytest.raises(ValueError) as exc_info:
        HttpEgressPolicy.for_purpose(
            HttpEgressPurpose.LLM,
            ("synthetic-secret=https://service.internal",),
        )

    assert "synthetic-secret" not in str(exc_info.value)
    assert "synthetic-secret" not in "".join(traceback.format_exception(exc_info.value))
