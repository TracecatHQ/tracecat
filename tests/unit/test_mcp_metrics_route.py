import importlib

import pytest
from starlette import status
from starlette.requests import Request

from tracecat import config


def _request(path: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    return Request(scope)


def _metrics_handler(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "USER_AUTH_SECRET", "test-user-auth-secret")
    return importlib.import_module("tracecat.mcp.server").metrics


@pytest.mark.anyio
async def test_mcp_metrics_allows_direct_scrape_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await _metrics_handler(monkeypatch)(_request("/metrics"))

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_mcp_metrics_rejects_public_root_prefixed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await _metrics_handler(monkeypatch)(_request("/mcp/metrics"))

    assert response.status_code == status.HTTP_404_NOT_FOUND
