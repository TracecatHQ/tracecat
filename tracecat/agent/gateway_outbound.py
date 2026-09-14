"""Guarded HTTP clients for managed providers with configurable destinations."""

from __future__ import annotations

import ssl
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from openai import AsyncAzureOpenAI

from tracecat.outbound import OutboundHTTPTransport, create_outbound_http_client


class OutboundLLMHTTPHandler(AsyncHTTPHandler):
    """Supply the shared egress policy through LiteLLM's HTTP handler interface.

    The callback owns one handler, with request-scoped credentials supplied by
    LiteLLM. AsyncHTTPHandler owns the HTTP client's cleanup. Its constructor
    calls create_client; no unguarded client is created first.
    """

    def azure_cloudflare_client(self, data: Mapping[str, Any]) -> AsyncAzureOpenAI:
        """Guard Azure's special gateway adapter, which skips the shared factory."""
        model = data["model"].removeprefix("azure/")
        return AsyncAzureOpenAI(
            api_key=data.get("api_key"),
            azure_ad_token=data.get("azure_ad_token"),
            api_version=data["api_version"],
            base_url=f"{data['api_base'].rstrip('/')}/{model}",
            http_client=self.client,
            max_retries=0,
        )

    def create_client(
        self,
        timeout: float | httpx.Timeout | None,
        event_hooks: Mapping[str, list[Callable[..., Any]]] | None,
        ssl_verify: Any = None,
        shared_session: Any = None,
    ) -> httpx.AsyncClient:
        # LiteLLM's public constructor options are not exported as a TypedDict.
        # Only timeout and event hooks affect this handler. TLS verification and
        # proxy behavior are owned by the shared outbound factory.
        return create_outbound_http_client(
            timeout=timeout or httpx.Timeout(600.0, connect=5.0),
            event_hooks=event_hooks,
            follow_redirects=True,
        )


def _create_guarded_transport(
    ssl_context: ssl.SSLContext | None = None,
    ssl_verify: bool | None = None,
    shared_session: Any = None,
) -> OutboundHTTPTransport:
    # Never reuse an aiohttp session that could bypass the socket policy.
    del shared_session
    verify = ssl_context if ssl_context is not None else ssl_verify
    return OutboundHTTPTransport(verify=True if verify is None else verify)


def install_outbound_http_policy() -> None:
    """Guard LiteLLM's async HTTP and OpenAI SDK factories before serving.

    LiteLLM's async Chat-to-Responses bridge drops request-scoped clients.
    Its HTTP handlers and OpenAI/Azure SDK factories share this transport
    factory, so installing here also protects protocol switches and streams.
    HTTPX disables environment proxy mounts when given an explicit transport.
    This compatibility shim is covered against the pinned LiteLLM version.
    """
    AsyncHTTPHandler._create_async_transport = staticmethod(_create_guarded_transport)
