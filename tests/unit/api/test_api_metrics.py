import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from tracecat.api.app import metrics


def _request(path: str, root_path: str = "/api") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "root_path": root_path,
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


@pytest.mark.anyio
async def test_metrics_allows_direct_scrape_path() -> None:
    response = await metrics(_request("/metrics"))

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_metrics_rejects_root_prefixed_public_path() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await metrics(_request("/api/metrics"))

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_metrics_rejects_custom_root_prefixed_public_path() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await metrics(_request("/custom/metrics", root_path="/custom"))

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
