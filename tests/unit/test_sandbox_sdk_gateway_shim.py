"""The Python-script sandboxes add the gateway's capacity retry to old SDKs.

Workflows locked to an older registry keep its SDK, which doesn't retry the
Action Gateway's pre-route ``tenant_db_busy`` refusal. Both script wrappers
patch such clients so a capacity refusal waits instead of failing the script.
"""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tracecat.sandbox.unsafe_pid_executor import SAFE_WRAPPER_SCRIPT
from tracecat.sandbox.wrapper import WRAPPER_SCRIPT

_SCRIPTS = [
    pytest.param(WRAPPER_SCRIPT, id="nsjail-wrapper"),
    pytest.param(SAFE_WRAPPER_SCRIPT.format(work_dir="/tmp/work"), id="unsafe-pid"),
]


def _load(script: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "sandbox_wrapper_under_test"}
    exec(compile(script, "<wrapper>", "exec"), namespace)
    return namespace


def _busy() -> httpx.Response:
    return httpx.Response(
        503,
        headers={"Retry-After": "1"},
        json={"detail": {"code": "tenant_db_busy"}},
    )


def _install_locked_sdk(
    monkeypatch: pytest.MonkeyPatch,
    wrapper: dict[str, Any],
    respond: Callable[[], httpx.Response],
    *,
    sdk_retries_natively: bool = False,
) -> tuple[type, list[str], list[httpx.Response]]:
    sent: list[str] = []
    errors: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return respond()

    transport = httpx.MockTransport(handler)

    class LockedTracecatClient:
        _timeout = 5.0

        def _request_url_and_transport(
            self, path: str
        ) -> tuple[str, httpx.AsyncBaseTransport]:
            return f"http://tracecat-action-gateway/internal{path}", transport

        def _get_headers(self) -> dict[str, str]:
            return {"Authorization": "Bearer executor-token"}

        def _handle_error_response(self, response: httpx.Response) -> None:
            errors.append(response)
            raise RuntimeError(f"gateway error {response.status_code}")

        async def request(self, *args: object, **kwargs: object) -> str:
            return "original single-shot request"

    sdk_client: Any = types.ModuleType("tracecat_registry.sdk.client")
    sdk_client.TracecatClient = LockedTracecatClient
    if sdk_retries_natively:
        sdk_client.TENANT_BUSY_ERROR_CODE = "tenant_db_busy"
    monkeypatch.setitem(sys.modules, "tracecat_registry.sdk.client", sdk_client)
    monkeypatch.setenv(
        "TRACECAT__ACTION_GATEWAY_SOCKET", "/var/run/tracecat/action-gateway.sock"
    )
    # No sleeping between retries.
    wrapper["_TENANT_BUSY_MIN_RETRY_AFTER_SECONDS"] = 0.0
    wrapper["_TENANT_BUSY_MAX_RETRY_AFTER_SECONDS"] = 0.0
    return LockedTracecatClient, sent, errors


@pytest.mark.parametrize("script", _SCRIPTS)
def test_locked_sdk_waits_out_capacity_refusals(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    wrapper = _load(script)
    responses = [_busy() for _ in range(20)]
    responses.append(httpx.Response(200, json={"ok": True}))
    client_cls, sent, errors = _install_locked_sdk(
        monkeypatch, wrapper, lambda: responses.pop(0)
    )

    wrapper["_install_action_gateway_sdk_transport"]()
    result = asyncio.run(client_cls().request("POST", "/tables/rows", json={"a": 1}))

    assert result == {"ok": True}
    assert len(sent) == 21
    assert set(sent) == {"http://tracecat-action-gateway/internal/tables/rows"}
    assert errors == []


@pytest.mark.parametrize("script", _SCRIPTS)
def test_other_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    wrapper = _load(script)
    client_cls, sent, errors = _install_locked_sdk(
        monkeypatch,
        wrapper,
        lambda: httpx.Response(503, json={"detail": "Service unavailable"}),
    )

    wrapper["_install_action_gateway_sdk_transport"]()
    with pytest.raises(RuntimeError, match="gateway error 503"):
        asyncio.run(client_cls().request("POST", "/tables/rows", json={"a": 1}))

    assert len(sent) == 1
    assert len(errors) == 1


@pytest.mark.parametrize("script", _SCRIPTS)
def test_current_sdk_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    wrapper = _load(script)
    client_cls, sent, _ = _install_locked_sdk(
        monkeypatch, wrapper, _busy, sdk_retries_natively=True
    )

    wrapper["_install_action_gateway_sdk_transport"]()

    assert asyncio.run(client_cls().request()) == "original single-shot request"
    assert sent == []


@pytest.mark.parametrize("script", _SCRIPTS)
@pytest.mark.parametrize(
    ("header", "low", "high"),
    [
        pytest.param("2", 2.0, 2.0, id="seconds"),
        pytest.param("nan", 1.0, 1.0, id="nan"),
        pytest.param("soon", 1.0, 1.0, id="unparseable"),
        pytest.param("600", 10.0, 10.0, id="capped"),
    ],
)
def test_backoff_bounds(script: str, header: str, low: float, high: float) -> None:
    wrapper = _load(script)
    response = httpx.Response(503, headers={"Retry-After": header})
    assert low <= wrapper["_tenant_busy_delay"](response, 0) <= high
