import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request, Response
from starlette.status import HTTP_200_OK, HTTP_429_TOO_MANY_REQUESTS

from tracecat.middleware.rate_limit import RateLimitMiddleware, TokenBucket


class TestTokenBucket:
    """Tests for the TokenBucket class."""

    @pytest.mark.anyio
    async def test_init(self):
        """Test that the token bucket is initialized correctly."""
        bucket = TokenBucket(rate=10.0, capacity=20.0)
        assert bucket.rate == 10.0
        assert bucket.capacity == 20.0
        assert bucket.tokens == 20.0
        assert bucket.last_refill <= time.time()

    @pytest.mark.anyio
    async def test_consume_success(self):
        """Test that tokens can be consumed successfully."""
        bucket = TokenBucket(rate=10.0, capacity=20.0)
        result = await bucket.consume(tokens=5.0)
        assert result is True
        assert bucket.tokens == 15.0

    @pytest.mark.anyio
    async def test_consume_failure(self):
        """Test that consumption fails when there are not enough tokens."""
        bucket = TokenBucket(rate=10.0, capacity=20.0)
        result = await bucket.consume(tokens=25.0)
        assert result is False
        assert bucket.tokens == 20.0

    @pytest.mark.anyio
    async def test_refill(self):
        """Test that tokens are refilled based on elapsed time."""
        bucket = TokenBucket(rate=10.0, capacity=20.0)
        # Consume some tokens
        await bucket.consume(tokens=15.0)
        assert bucket.tokens == 5.0

        # Manually set the last refill time to simulate elapsed time
        bucket.last_refill = time.time() - 1.0  # 1 second ago

        # Consume again, which should trigger a refill
        result = await bucket.consume(tokens=1.0)
        assert result is True
        # Should have refilled 10 tokens (rate=10.0 * 1.0 seconds) and then consumed 1
        # Using math.isclose to allow for minor floating point imprecision

        assert math.isclose(bucket.tokens, 14.0, rel_tol=1e-5)

    @pytest.mark.anyio
    async def test_refill_max_capacity(self):
        """Test that refill doesn't exceed the maximum capacity."""
        bucket = TokenBucket(rate=10.0, capacity=20.0)
        # Manually set the last refill time to simulate a long elapsed time
        bucket.last_refill = time.time() - 10.0  # 10 seconds ago

        # Consume, which should trigger a refill but not exceed capacity
        result = await bucket.consume(tokens=5.0)
        assert result is True
        assert bucket.tokens == 15.0  # Started at 20, refilled to max 20, consumed 5

    @pytest.mark.anyio
    async def test_concurrent_consume(self):
        """Test that concurrent consumption is handled correctly."""
        bucket = TokenBucket(rate=10.0, capacity=20.0)

        async def consume_task(amount):
            return await bucket.consume(tokens=amount)

        # Create tasks that will try to consume tokens concurrently
        task1 = asyncio.create_task(consume_task(8.0))
        task2 = asyncio.create_task(consume_task(8.0))
        task3 = asyncio.create_task(consume_task(8.0))

        # Wait for all tasks to complete
        results = await asyncio.gather(task1, task2, task3)

        # Only the first two tasks should succeed (8 + 8 = 16 tokens)
        assert sum(results) == 2  # Two True results
        assert 4.0 <= bucket.tokens <= 4.1  # Started with 20, consumed 16


class TestRateLimitMiddleware:
    """Tests for the RateLimitMiddleware class."""

    @pytest.fixture
    def app(self):
        """Create a FastAPI app for testing."""
        return FastAPI()

    @pytest.fixture
    def middleware(self, app):
        """Create a RateLimitMiddleware instance for testing."""
        return RateLimitMiddleware(
            app=app,
            rate=10.0,
            capacity=20.0,
            window_size=60,
            by_ip=True,
            by_endpoint=True,
        )

    @pytest.fixture
    def mock_request(self):
        """Create a mock request for testing."""
        request = MagicMock(spec=Request)
        request.url.path = "/test/path"
        request.method = "GET"
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        return request

    @pytest.fixture
    def mock_call_next(self):
        """Create a mock call_next function for testing."""

        async def call_next(_):
            return Response(status_code=HTTP_200_OK)

        return AsyncMock(side_effect=call_next)

    def test_init(self, app):
        """Test that the middleware is initialized correctly."""
        middleware = RateLimitMiddleware(
            app=app,
            rate=10.0,
            capacity=20.0,
            window_size=60,
            by_ip=True,
            by_endpoint=True,
        )
        assert middleware.rate == 10.0
        assert middleware.capacity == 20.0
        assert middleware.window_size == 60
        assert middleware.by_ip is True
        assert middleware.by_endpoint is True
        assert isinstance(middleware.buckets, dict)

    def test_get_bucket_key_with_ip_and_endpoint(self, middleware, mock_request):
        """Test that the bucket key is generated correctly with IP and endpoint."""
        key = middleware.get_bucket_key(mock_request)
        assert key == "ip:127.0.0.1:path:/test/path"

    def test_get_bucket_key_with_ip_only(self, app, mock_request):
        """Test that the bucket key is generated correctly with IP only."""
        middleware = RateLimitMiddleware(
            app=app,
            rate=10.0,
            capacity=20.0,
            window_size=60,
            by_ip=True,
            by_endpoint=False,
        )
        key = middleware.get_bucket_key(mock_request)
        assert key == "ip:127.0.0.1"

    def test_get_bucket_key_with_endpoint_only(self, app, mock_request):
        """Test that the bucket key is generated correctly with endpoint only."""
        middleware = RateLimitMiddleware(
            app=app,
            rate=10.0,
            capacity=20.0,
            window_size=60,
            by_ip=False,
            by_endpoint=True,
        )
        key = middleware.get_bucket_key(mock_request)
        assert key == "path:/test/path"

    def test_get_bucket_key_with_neither(self, app, mock_request):
        """Test that the bucket key is generated correctly with neither IP nor endpoint."""
        middleware = RateLimitMiddleware(
            app=app,
            rate=10.0,
            capacity=20.0,
            window_size=60,
            by_ip=False,
            by_endpoint=False,
        )
        key = middleware.get_bucket_key(mock_request)
        assert key == "global"

    def test_get_bucket_key_with_no_client(self, middleware):
        """Test that the bucket key is generated correctly when there is no client."""
        request = MagicMock(spec=Request)
        request.url.path = "/test/path"
        request.method = "GET"
        request.client = None
        key = middleware.get_bucket_key(request)
        assert key == "ip:unknown:path:/test/path"

    @pytest.mark.anyio
    async def test_dispatch_success(self, middleware, mock_request, mock_call_next):
        """Test that the request is processed successfully when there are enough tokens."""
        with patch.object(TokenBucket, "consume", return_value=True):
            response = await middleware.dispatch(mock_request, mock_call_next)
            assert response.status_code == HTTP_200_OK
            mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.anyio
    async def test_dispatch_rate_limited(
        self, middleware, mock_request, mock_call_next
    ):
        """Test that the request is rate limited when there are not enough tokens."""
        with patch.object(TokenBucket, "consume", return_value=False):
            response = await middleware.dispatch(mock_request, mock_call_next)
            assert response.status_code == HTTP_429_TOO_MANY_REQUESTS
            assert response.body == b"Rate limit exceeded. Please try again later."
            mock_call_next.assert_not_called()

    @pytest.mark.anyio
    async def test_integration(self, app, mock_request, mock_call_next):
        """Test the middleware in an integrated way."""
        middleware = RateLimitMiddleware(
            app=app,
            rate=2.0,  # 2 tokens per second
            capacity=2.0,  # Maximum of 2 tokens
            window_size=60,
            by_ip=True,
            by_endpoint=True,
        )

        # First request should succeed
        response1 = await middleware.dispatch(mock_request, mock_call_next)
        assert response1.status_code == HTTP_200_OK

        # Second request should succeed
        response2 = await middleware.dispatch(mock_request, mock_call_next)
        assert response2.status_code == HTTP_200_OK

        # Third request should be rate limited
        response3 = await middleware.dispatch(mock_request, mock_call_next)
        assert response3.status_code == HTTP_429_TOO_MANY_REQUESTS

        # Wait for tokens to refill
        await asyncio.sleep(1.1)  # Wait for at least 1 token to be refilled

        # Fourth request should succeed after waiting
        response4 = await middleware.dispatch(mock_request, mock_call_next)
        assert response4.status_code == HTTP_200_OK
