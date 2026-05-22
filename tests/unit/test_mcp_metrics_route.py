import importlib

import pytest
from starlette import status
from starlette.requests import Request

from tracecat import config


def _request(path: str, authorization: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    return Request(scope)


def _metrics_handler(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "USER_AUTH_SECRET", "test-user-auth-secret")
    return importlib.import_module("tracecat.mcp.server").metrics


@pytest.mark.anyio
async def test_mcp_metrics_allows_when_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", None)

    response = await _metrics_handler(monkeypatch)(_request("/metrics"))

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_mcp_metrics_requires_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", "test-metrics-token")

    response = await _metrics_handler(monkeypatch)(_request("/metrics"))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    response = await _metrics_handler(monkeypatch)(
        _request("/metrics", authorization="Bearer wrong-token")
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    response = await _metrics_handler(monkeypatch)(
        _request("/metrics", authorization="Bearer test-metrics-token")
    )

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_mcp_metrics_rejects_non_ascii_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", "test-metrics-token")

    response = await _metrics_handler(monkeypatch)(
        _request("/metrics", authorization="Bearer \u00e9")
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_mcp_metrics_rejects_public_root_prefixed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", "test-metrics-token")

    response = await _metrics_handler(monkeypatch)(
        _request("/mcp/metrics", authorization="Bearer test-metrics-token")
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
