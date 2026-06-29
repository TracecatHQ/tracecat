import pytest

from tracecat.api.app import app, check_health


@pytest.mark.anyio
async def test_check_health_returns_ok() -> None:
    health = await check_health()

    assert health.status == "ok"


def test_openapi_includes_health_not_ready() -> None:
    app.openapi_schema = None

    paths = app.openapi()["paths"]

    assert "/health" in paths
    assert "/ready" not in paths
