"""Tests for the LLM provider base URL SSRF guard."""

from __future__ import annotations

import socket
from collections.abc import Sequence
from typing import Final

import httpx
import pytest

from tracecat import config as tracecat_config
from tracecat.agent.provider import service as provider_service_module
from tracecat.agent.provider.service import fetch_openai_compatible_models
from tracecat.agent.provider.url_guard import (
    LLMProviderUrlNotAllowedError,
    validate_llm_provider_url,
)

pytestmark = pytest.mark.anyio

_PUBLIC_IP: Final = "93.184.216.34"


def _fake_getaddrinfo(address: str):
    def getaddrinfo(
        host: object,
        port: object,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> Sequence[
        tuple[
            socket.AddressFamily,
            socket.SocketKind,
            int,
            str,
            tuple[str, int],
        ]
    ]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, 443),
            )
        ]

    return getaddrinfo


@pytest.fixture(autouse=True)
def strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tracecat_config, "TRACECAT__AGENT_ALLOW_PRIVATE_LLM_HOSTS", False
    )


async def test_public_host_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC_IP))
    await validate_llm_provider_url("https://gateway.example/v1")


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "172.17.0.2", "192.168.1.9", "169.254.169.254"],
)
async def test_private_host_is_rejected(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(address))
    with pytest.raises(LLMProviderUrlNotAllowedError) as exc_info:
        await validate_llm_provider_url("http://gateway.example/v1")
    assert address not in str(exc_info.value)


async def test_unresolvable_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise socket.gaierror

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(LLMProviderUrlNotAllowedError):
        await validate_llm_provider_url("http://missing.invalid/v1")


async def test_missing_hostname_is_rejected() -> None:
    with pytest.raises(LLMProviderUrlNotAllowedError):
        await validate_llm_provider_url("http:///v1")


async def test_opt_out_allows_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tracecat_config, "TRACECAT__AGENT_ALLOW_PRIVATE_LLM_HOSTS", True
    )
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    await validate_llm_provider_url("http://localhost:11434/v1")


async def test_fetch_models_validates_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    requested = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        return httpx.Response(200, json={"data": []})

    original_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(provider_service_module.httpx, "AsyncClient", client_factory)
    with pytest.raises(LLMProviderUrlNotAllowedError):
        await fetch_openai_compatible_models(
            base_url="http://metadata.example/latest",
            api_key=None,
            timeout=1.0,
        )
    assert requested is False
