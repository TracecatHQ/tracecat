"""Install Tracecat's outbound-network policy in managed LiteLLM clients."""

from __future__ import annotations

import os
import ssl
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import litellm
from aiohttp import ClientSession
from litellm.llms.custom_httpx.http_handler import (
    _DEFAULT_TIMEOUT,
    AsyncHTTPHandler,
    get_default_headers,
    get_ssl_configuration,
)

from tracecat.network import (
    HttpEgressPolicy,
    HttpEgressPurpose,
    configured_http_egress_policy,
)
from tracecat.outbound_http import GuardedAsyncHTTPTransport, guarded_async_client

_installed = False
_policy: HttpEgressPolicy | None = None


def _required_policy() -> HttpEgressPolicy:
    if _policy is None:
        raise RuntimeError("LiteLLM outbound HTTP guard is not installed")
    return _policy


def _guarded_transport(
    ssl_context: ssl.SSLContext | None = None,
    ssl_verify: bool | None = None,
    shared_session: ClientSession | None = None,
) -> httpx.AsyncHTTPTransport:
    """Build the transport used by LiteLLM's provider HTTP clients."""
    del shared_session
    verify: ssl.SSLContext | bool = (
        ssl_context if ssl_context is not None else ssl_verify is not False
    )
    return GuardedAsyncHTTPTransport(
        _required_policy(),
        verify=verify,
        ca_from_env=False,
    )


def _guarded_client(
    self: AsyncHTTPHandler,
    timeout: float | httpx.Timeout | None,
    event_hooks: Mapping[str, list[Callable[..., Any]]] | None,
    ssl_verify: str | bool | ssl.SSLContext | None = None,
    shared_session: ClientSession | None = None,
) -> httpx.AsyncClient:
    """Build a proxy-free, redirect-free LiteLLM provider client."""
    del self, shared_session
    ssl_config = get_ssl_configuration(ssl_verify)
    cert = os.getenv("SSL_CERTIFICATE") or litellm.ssl_certificate
    transport = GuardedAsyncHTTPTransport(
        _required_policy(),
        verify=ssl_config,
        cert=cert,
        ca_from_env=False,
    )
    return httpx.AsyncClient(
        transport=transport,
        event_hooks=event_hooks,
        timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
        headers=get_default_headers(),
        follow_redirects=False,
        trust_env=False,
    )


def _flush_preconfigured_clients() -> None:
    """Drop HTTP clients LiteLLM creates while importing provider modules."""
    cache = litellm.__dict__.get("in_memory_llm_clients_cache")
    if cache is not None:
        cache.flush_cache()
    litellm.__dict__.pop("module_level_aclient", None)


def install_litellm_egress_guard() -> None:
    """Route every managed LiteLLM async provider request through the guard."""
    global _installed, _policy
    if _installed:
        return

    _policy = configured_http_egress_policy(HttpEgressPurpose.LLM)
    litellm.disable_aiohttp_transport = True
    AsyncHTTPHandler._create_async_transport = staticmethod(_guarded_transport)
    AsyncHTTPHandler.create_client = _guarded_client
    _flush_preconfigured_clients()

    ssl_config = get_ssl_configuration()
    cert = os.getenv("SSL_CERTIFICATE") or litellm.ssl_certificate
    litellm.aclient_session = guarded_async_client(
        _policy,
        timeout=_DEFAULT_TIMEOUT,
        headers=get_default_headers(),
        verify=ssl_config,
        cert=cert,
        ca_from_env=False,
    )
    _installed = True
