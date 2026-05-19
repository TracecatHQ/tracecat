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

    This is the normal executor path for freshly loaded SDK code. The client
    keeps `api_url` unchanged for callers, but selects a UDS transport for the
    actual request when `TRACECAT__ACTION_GATEWAY_SOCKET` is set.
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
        api_url="http://api:8000",
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
async def test_empty_action_gateway_socket_env_uses_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty socket env var is treated as disabled and falls back to API URL.

    This keeps local/dev environments from accidentally constructing an invalid
    UDS transport when the socket variable is present but blank.
    """
    monkeypatch.setenv("TRACECAT__ACTION_GATEWAY_SOCKET", "")
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(
            self,
            *,
            transport: object | None,
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

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = TracecatClient(
        api_url="http://api:8000",
        token="executor-token",
        timeout=12.0,
    )

    result = await client.get("/cases/case-id")

    assert result == {"ok": True}
    assert captured["transport"] is None
    assert captured["timeout"] == 12.0
    assert captured["method"] == "GET"
    assert captured["url"] == "http://api:8000/internal/cases/case-id"
    assert captured["headers"]["Authorization"] == "Bearer executor-token"


@pytest.mark.anyio
async def test_action_gateway_socket_keyword_is_supported_for_compat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rc-era explicit socket keyword remains a TracecatClient-only alias.

    The keyword is kept for direct SDK callers from the rc line, but it does not
    flow through `RegistryContext`; transport selection remains private to the
    client/minimal-runner boundary.
    """
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
        api_url="http://api:8000",
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
async def test_request_uses_api_url_without_action_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK requests keep using TRACECAT__API_URL when no gateway socket exists.

    This covers non-executor contexts and disabled gateway deployments, where
    the SDK should behave like the pre-gateway client.
    """
    monkeypatch.delenv("TRACECAT__ACTION_GATEWAY_SOCKET", raising=False)
    captured: dict[str, Any] = {}

    class FakeAsyncClient:
        def __init__(
            self,
            *,
            transport: object | None,
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

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = TracecatClient(
        api_url="http://api:8000",
        token="executor-token",
        timeout=12.0,
    )

    result = await client.get("/cases/case-id")

    assert result == {"ok": True}
    assert captured["transport"] is None
    assert captured["timeout"] == 12.0
    assert captured["method"] == "GET"
    assert captured["url"] == "http://api:8000/internal/cases/case-id"
    assert captured["headers"]["Authorization"] == "Bearer executor-token"
