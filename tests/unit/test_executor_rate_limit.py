import pytest
from fastapi.testclient import TestClient
from starlette.status import HTTP_200_OK, HTTP_429_TOO_MANY_REQUESTS

from tracecat import config
from tracecat.api.executor import create_app


@pytest.fixture
def patched_config(monkeypatch):
    """Patch the config to enable rate limiting with a low capacity."""
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_RATE", 1.0)  # 1 token per second
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_CAPACITY", 2.0)  # Max 2 tokens
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_WINDOW_SIZE", 60)
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_BY_IP", True)
    monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_BY_ENDPOINT", True)


@pytest.fixture
def client(patched_config):
    """Create a test client for the executor service with rate limiting enabled."""
    # Create the app with rate limiting enabled
    app = create_app(lifespan=None)  # Disable lifespan to avoid starting Ray
    return TestClient(app)


class TestExecutorRateLimit:
    """Tests for the executor service with rate limiting enabled."""

    def test_rate_limit_root_endpoint(self, client):
        """Test that the root endpoint is rate limited."""
        # First 2 requests should succeed (capacity = 2)
        for _ in range(2):
            response = client.get("/")
            assert response.status_code == HTTP_200_OK
            assert response.json() == {"message": "Hello world. I am the executor."}

        # Third request should be rate limited
        response = client.get("/")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS
        assert response.text == "Rate limit exceeded. Please try again later."

    def test_rate_limit_disabled(self, monkeypatch):
        """Test that rate limiting can be disabled."""
        # Disable rate limiting
        monkeypatch.setattr(config, "TRACECAT__RATE_LIMIT_ENABLED", False)

        # Create the app with rate limiting disabled
        app = create_app(lifespan=None)  # Disable lifespan to avoid starting Ray
        client = TestClient(app)

        # All requests should succeed
        for _ in range(5):  # More than the capacity
            response = client.get("/")
            assert response.status_code == HTTP_200_OK
            assert response.json() == {"message": "Hello world. I am the executor."}
