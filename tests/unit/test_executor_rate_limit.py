from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from starlette.status import HTTP_200_OK, HTTP_429_TOO_MANY_REQUESTS

from tracecat import config
from tracecat.api.executor import create_app


async def lifespan(*args, **kwargs) -> AsyncIterator[None]:
    yield


@pytest.fixture
def rate_limited_client(monkeypatch):
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_RATE", 1.0)  # 1 token per second
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_CAPACITY", 2.0)  # Max 2 tokens
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_WINDOW_SIZE", 60)
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_BY_IP", True)
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_BY_ENDPOINT", True)

    app = create_app()
    app.router.lifespan = lifespan  # type: ignore

    @app.get("/")
    def root():
        return {"message": "Hello world. I am the executor."}

    return TestClient(app)


@pytest.fixture
def disabled_rate_limit_client(monkeypatch):
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_ENABLED", False)
    app = create_app()
    app.router.lifespan = lifespan  # type: ignore

    @app.get("/")
    def root():
        return {"message": "Hello world. I am the executor."}

    return TestClient(app)


class TestExecutorRateLimit:
    """Tests for the executor service with rate limiting enabled."""

    def test_rate_limit_root_endpoint(self, rate_limited_client):
        """Test that the root endpoint is rate limited."""
        client = rate_limited_client

        # Capacity is 2 tokens, so we should be able to make 2 requests
        for _ in range(2):
            response = client.get("/")
            assert response.status_code == HTTP_200_OK
            assert response.json() == {"message": "Hello world. I am the executor."}

        # The third request should be rate limited
        response = client.get("/")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS
        assert response.text == "Rate limit exceeded. Please try again later."

    def test_rate_limit_disabled(self, disabled_rate_limit_client):
        """Test that rate limiting can be disabled."""
        client = disabled_rate_limit_client

        # All requests should succeed
        for _ in range(5):  # More than the capacity
            response = client.get("/")
            assert response.status_code == HTTP_200_OK
            assert response.json() == {"message": "Hello world. I am the executor."}
