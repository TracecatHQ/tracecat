import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.status import HTTP_200_OK, HTTP_429_TOO_MANY_REQUESTS

from tracecat.middleware import RateLimitMiddleware


@pytest.fixture
def app() -> FastAPI:
    """Create a FastAPI app with rate limiting middleware for testing."""
    app = FastAPI()

    # Add rate limiting middleware with a low capacity for testing
    app.add_middleware(
        RateLimitMiddleware,
        rate=2.0,  # 2 tokens per second
        capacity=3.0,  # Maximum of 3 tokens
        window_size=60,
        by_ip=True,
        by_endpoint=True,
    )

    @app.get("/test")
    async def test_endpoint():
        return {"message": "success"}

    @app.get("/another")
    async def another_endpoint():
        return {"message": "another success"}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


class TestRateLimitIntegration:
    """Integration tests for the rate limiting middleware."""

    def test_rate_limit_single_endpoint(self, client: TestClient):
        """Test that requests to a single endpoint are rate limited."""
        # First 3 requests should succeed (capacity = 3)
        for _ in range(3):
            response = client.get("/test")
            assert response.status_code == HTTP_200_OK
            assert response.json() == {"message": "success"}

        # Fourth request should be rate limited
        response = client.get("/test")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS
        assert response.text == "Rate limit exceeded. Please try again later."

    def test_rate_limit_different_endpoints(self, client: TestClient):
        """Test that rate limiting works across different endpoints when by_endpoint=True."""
        # First 2 requests to /test should succeed
        for _ in range(2):
            response = client.get("/test")
            assert response.status_code == HTTP_200_OK

        # First 2 requests to /another should also succeed (different bucket)
        for _ in range(2):
            response = client.get("/another")
            assert response.status_code == HTTP_200_OK

        # Third request to /test should succeed
        response = client.get("/test")
        assert response.status_code == HTTP_200_OK

        # Fourth request to /test should be rate limited
        response = client.get("/test")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS

        # Third request to /another should succeed
        response = client.get("/another")
        assert response.status_code == HTTP_200_OK

        # Fourth request to /another should be rate limited
        response = client.get("/another")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS

    def test_rate_limit_refill(self, client: TestClient, monkeypatch):
        """Test that tokens are refilled over time."""
        # Mock time.time to control the passage of time
        current_time = 0.0

        def mock_time():
            return current_time

        monkeypatch.setattr("time.time", mock_time)

        # First 3 requests should succeed (capacity = 3)
        for _ in range(3):
            response = client.get("/test")
            assert response.status_code == HTTP_200_OK

        # Fourth request should be rate limited
        response = client.get("/test")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS

        # Advance time by 1 second (should refill 2 tokens)
        current_time += 1.0

        # Next 2 requests should succeed
        for _ in range(2):
            response = client.get("/test")
            assert response.status_code == HTTP_200_OK

        # Third request should be rate limited again
        response = client.get("/test")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS

    def test_rate_limit_by_ip(self):
        """Test that rate limiting works by IP address."""
        # Create a new app with rate limiting by IP only
        app = FastAPI()

        # Add rate limiting middleware
        app.add_middleware(
            RateLimitMiddleware,
            rate=2.0,
            capacity=3.0,
            window_size=60,
            by_ip=True,
            by_endpoint=False,  # Don't limit by endpoint
        )

        @app.get("/test")
        async def test_endpoint():
            return {"message": "success"}

        @app.get("/another")
        async def another_endpoint():
            return {"message": "another success"}

        # Create clients with different IP addresses
        client1 = TestClient(app)
        client1.headers.update({"X-Forwarded-For": "192.168.1.1"})

        client2 = TestClient(app)
        client2.headers.update({"X-Forwarded-For": "192.168.1.2"})

        # First 3 requests from client1 should succeed
        for _ in range(3):
            response = client1.get("/test")
            assert response.status_code == HTTP_200_OK

        # Fourth request from client1 should be rate limited
        response = client1.get("/test")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS

        # First 3 requests from client2 should succeed (different IP)
        for _ in range(3):
            response = client2.get("/test")
            assert response.status_code == HTTP_200_OK

        # Fourth request from client2 should be rate limited
        response = client2.get("/test")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS

        # Requests to different endpoints from client1 should still be rate limited
        # (since we're not limiting by endpoint)
        response = client1.get("/another")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS
