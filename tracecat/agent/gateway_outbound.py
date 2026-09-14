"""Guarded HTTP handler for managed custom OpenAI-compatible providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

from tracecat.outbound import create_outbound_http_client


class OutboundLLMHTTPHandler(AsyncHTTPHandler):
    """Supply the shared egress policy through LiteLLM's HTTP handler interface.

    The callback owns one handler, with request-scoped credentials supplied by
    LiteLLM. AsyncHTTPHandler owns the HTTP client's cleanup. Its constructor
    calls create_client; no unguarded client is created first.
    """

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
        )
