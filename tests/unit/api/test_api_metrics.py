import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from tracecat import config
from tracecat.api.app import metrics


def _request(
    path: str,
    root_path: str = "/api",
    authorization: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "root_path": root_path,
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


@pytest.mark.anyio
async def test_metrics_allows_when_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", None)

    response = await metrics(_request("/metrics"))

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_metrics_requires_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", "test-metrics-token")

    with pytest.raises(HTTPException) as exc_info:
        await metrics(_request("/metrics"))

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    with pytest.raises(HTTPException) as exc_info:
        await metrics(_request("/metrics", authorization="Bearer wrong-token"))

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    response = await metrics(
        _request("/metrics", authorization="Bearer test-metrics-token")
    )

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_metrics_rejects_non_ascii_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", "test-metrics-token")

    with pytest.raises(HTTPException) as exc_info:
        await metrics(_request("/metrics", authorization="Bearer \u00e9"))

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_metrics_rejects_root_prefixed_public_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", "test-metrics-token")

    with pytest.raises(HTTPException) as exc_info:
        await metrics(
            _request("/api/metrics", authorization="Bearer test-metrics-token")
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_metrics_rejects_custom_root_prefixed_public_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__METRICS_TOKEN", "test-metrics-token")

    with pytest.raises(HTTPException) as exc_info:
        await metrics(
            _request(
                "/custom/metrics",
                root_path="/custom",
                authorization="Bearer test-metrics-token",
            )
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
