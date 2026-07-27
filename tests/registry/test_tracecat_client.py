"""Tests for the base Tracecat SDK client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tracecat_registry.sdk.client import TracecatClient


@pytest.mark.anyio
async def test_request_uses_action_gateway_unix_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current SDKs route internal requests through the gateway socket from env.

    This is the only executor path for freshly loaded SDK code.
    """
    monkeypatch.setenv(
        "TRACECAT__ACTION_GATEWAY_SOCKET",
        "/var/run/tracecat/action-gateway.sock",
    )
    captured: dict[str, Any] = {}

    class FakeTransport:
        def __init__(self, *, uds: str) -> None:
            captured["uds"] = uds

    class FakeAsyncClient:
        def __init__(
            self,
            *,
            transport: FakeTransport | None,
            timeout: float,
        ) -> None:
            captured["transport"] = transport
            captured["timeout"] = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(
            self,
            method: str,
            url: str,
            *,
            params: dict[str, Any] | None,
            json: Any | None,
            headers: dict[str, str],
        ) -> httpx.Response:
            captured["method"] = method
            captured["url"] = url
            captured["params"] = params
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", FakeTransport)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = TracecatClient(
        token="executor-token",
        timeout=12.0,
    )

    result = await client.post("/cases/metrics", json={"case_ids": []})

    assert result == {"ok": True}
    assert captured["uds"] == "/var/run/tracecat/action-gateway.sock"
    assert captured["timeout"] == 12.0
    assert captured["method"] == "POST"
    # With a UDS transport the host is only a placeholder; the path is what the
    # action gateway FastAPI app receives.
    assert captured["url"] == "http://tracecat-action-gateway/internal/cases/metrics"
    assert captured["json"] == {"case_ids": []}
    assert captured["headers"]["Authorization"] == "Bearer executor-token"


@pytest.mark.anyio
async def test_empty_action_gateway_socket_env_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty socket cannot silently route SDK traffic to the API service."""
    monkeypatch.setenv("TRACECAT__ACTION_GATEWAY_SOCKET", "")

    with pytest.raises(RuntimeError, match="ACTION_GATEWAY_SOCKET is required"):
        TracecatClient(token="executor-token")


@pytest.mark.anyio
async def test_action_gateway_socket_keyword_selects_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct SDK callers can provide the mandatory gateway socket explicitly."""
    monkeypatch.delenv("TRACECAT__ACTION_GATEWAY_SOCKET", raising=False)
    captured: dict[str, Any] = {}

    class FakeTransport:
        def __init__(self, *, uds: str) -> None:
            captured["uds"] = uds

    class FakeAsyncClient:
        def __init__(
            self,
            *,
            transport: FakeTransport | None,
            timeout: float,
        ) -> None:
            captured["transport"] = transport
            captured["timeout"] = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(
            self,
            method: str,
            url: str,
            *,
            params: dict[str, Any] | None,
            json: Any | None,
            headers: dict[str, str],
        ) -> httpx.Response:
            captured["method"] = method
            captured["url"] = url
            captured["params"] = params
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", FakeTransport)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = TracecatClient(
        action_gateway_socket="/var/run/tracecat/action-gateway.sock",
        token="executor-token",
        timeout=12.0,
    )

    result = await client.get("/cases/case-id")

    assert result == {"ok": True}
    assert captured["uds"] == "/var/run/tracecat/action-gateway.sock"
    assert captured["timeout"] == 12.0
    assert captured["method"] == "GET"
    assert captured["url"] == "http://tracecat-action-gateway/internal/cases/case-id"
    assert captured["headers"]["Authorization"] == "Bearer executor-token"


@pytest.mark.anyio
async def test_missing_action_gateway_socket_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing gateway configuration fails before any SDK request is attempted."""
    monkeypatch.delenv("TRACECAT__ACTION_GATEWAY_SOCKET", raising=False)

    with pytest.raises(RuntimeError, match="ACTION_GATEWAY_SOCKET is required"):
        TracecatClient(token="executor-token")
