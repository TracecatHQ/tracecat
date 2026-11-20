from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
from tracecat_registry.sdk import client as sdk_client
from tracecat_registry.sdk.client import (
    TENANT_BUSY_ERROR_CODE,
    TENANT_BUSY_MAX_RETRIES,
    TracecatClient,
    _retry_after_seconds,
)
from tracecat_registry.sdk.exceptions import TracecatAPIError, TracecatTenantBusyError

from tracecat.executor.action_gateway import db_gate

pytestmark = pytest.mark.anyio


def _tenant_busy() -> httpx.Response:
    return httpx.Response(
        503,
        headers={"Retry-After": "1"},
        json={"detail": {"code": TENANT_BUSY_ERROR_CODE, "message": "busy"}},
    )


def _client_with(
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[httpx.Request], httpx.Response],
) -> tuple[TracecatClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return respond(request)

    client = TracecatClient(
        action_gateway_socket="/tmp/action-gateway.sock", token="t", workspace_id="w"
    )
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        client,
        "_request_url_and_transport",
        lambda path: (f"http://tracecat-action-gateway/internal{path}", transport),
    )
    # Sleep 0 s between retries.
    monkeypatch.setattr(sdk_client, "TENANT_BUSY_MAX_RETRY_AFTER_SECONDS", 0.0)
    return client, requests


def test_sdk_and_gateway_share_the_error_code() -> None:
    assert TENANT_BUSY_ERROR_CODE == db_gate.TENANT_BUSY_ERROR_CODE


async def test_tenant_busy_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [_tenant_busy(), _tenant_busy(), httpx.Response(200, json={"ok": 1})]
    client, requests = _client_with(monkeypatch, lambda _: responses.pop(0))

    result = await client.post("/tables/rows", json={"a": 1})

    assert result == {"ok": 1}
    assert len(requests) == 3
    assert len({request.content for request in requests}) == 1


async def test_tenant_busy_retry_sleeps_for_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def busy(retry_after: str) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"Retry-After": retry_after},
            json={"detail": {"code": TENANT_BUSY_ERROR_CODE}},
        )

    responses = [busy("2"), busy("600"), httpx.Response(200, json={"ok": 1})]
    client, _ = _client_with(monkeypatch, lambda _: responses.pop(0))
    # Restore the real cap; the helper zeroes it.
    monkeypatch.setattr(sdk_client, "TENANT_BUSY_MAX_RETRY_AFTER_SECONDS", 10.0)
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def record_sleep(delay: float) -> None:
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    assert await client.post("/tables/rows", json={"a": 1}) == {"ok": 1}
    # Retry-After as sent, then capped at 10 s.
    assert delays == [2.0, 10.0]


async def test_tenant_busy_raises_after_bounded_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, requests = _client_with(monkeypatch, lambda _: _tenant_busy())

    with pytest.raises(TracecatTenantBusyError) as exc_info:
        await client.post("/tables/rows", json={"a": 1})

    assert len(requests) == TENANT_BUSY_MAX_RETRIES + 1
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {"code": TENANT_BUSY_ERROR_CODE, "message": "busy"}


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            httpx.Response(503, json={"detail": "Service unavailable"}),
            id="plain-503",
        ),
        pytest.param(
            httpx.Response(
                503,
                headers={"Retry-After": "1"},
                json={"detail": {"code": "auth_database_unavailable"}},
            ),
            id="other-coded-503",
        ),
        pytest.param(
            httpx.Response(500, json={"detail": {"code": TENANT_BUSY_ERROR_CODE}}),
            id="code-on-500",
        ),
        pytest.param(httpx.Response(503, text="upstream down"), id="non-json-503"),
    ],
)
async def test_other_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    response: httpx.Response,
) -> None:
    client, requests = _client_with(monkeypatch, lambda _: response)

    with pytest.raises(TracecatAPIError) as exc_info:
        await client.post("/tables/rows", json={"a": 1})

    assert len(requests) == 1
    assert not isinstance(exc_info.value, TracecatTenantBusyError)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        pytest.param("2", 2.0, id="seconds"),
        pytest.param(None, 1.0, id="missing"),
        pytest.param("soon", 1.0, id="unparseable"),
        pytest.param("nan", 1.0, id="nan"),
        pytest.param("inf", 1.0, id="inf"),
        pytest.param("600", 10.0, id="capped"),
        pytest.param("-5", 0.0, id="negative"),
    ],
)
def test_retry_after_is_bounded(header: str | None, expected: float) -> None:
    headers = {} if header is None else {"Retry-After": header}
    assert _retry_after_seconds(httpx.Response(503, headers=headers)) == expected
