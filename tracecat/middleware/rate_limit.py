from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from tracecat.logger import logger


class TokenBucket:
    """Token bucket algorithm implementation for rate limiting."""

    def __init__(self, rate: float, capacity: float):
        """
        Initialize a token bucket.

        Args:
            rate: The rate at which tokens are added to the bucket (tokens per second)
            capacity: The maximum number of tokens the bucket can hold
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.time()
        self._lock = asyncio.Lock()

    async def consume(self, tokens: float = 1.0) -> bool:
        """
        Try to consume tokens from the bucket.

        Args:
            tokens: The number of tokens to consume

        Returns:
            True if tokens were consumed, False otherwise
        """
        async with self._lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def _refill(self) -> None:
        """Refill the bucket based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting requests."""

    def __init__(
        self,
        app,
        rate: float = 10.0,
        capacity: float = 20.0,
        window_size: int = 60,
        by_ip: bool = True,
        by_endpoint: bool = True,
    ):
        """
        Initialize the rate limit middleware.

        Args:
            app: The FastAPI application
            rate: The rate at which tokens are added to the bucket (tokens per second)
            capacity: The maximum number of tokens the bucket can hold
            window_size: The time window in seconds for rate limiting
            by_ip: Whether to rate limit by client IP
            by_endpoint: Whether to rate limit by endpoint
        """
        super().__init__(app)
        self.rate = rate
        self.capacity = capacity
        self.window_size = window_size
        self.by_ip = by_ip
        self.by_endpoint = by_endpoint
        self.buckets: defaultdict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(rate=self.rate, capacity=self.capacity)
        )

    def get_client_ip(self, request: Request) -> str:
        """
        Get the client IP address from the request.

        This method checks for common proxy headers like X-Forwarded-For before
        falling back to the client.host attribute.

        Args:
            request: The FastAPI request

        Returns:
            The client IP address as a string
        """
        # Check for X-Forwarded-For header (common in proxy setups)
        if "X-Forwarded-For" in request.headers:
            # X-Forwarded-For can contain multiple IPs, take the first one (client IP)
            return request.headers["X-Forwarded-For"].split(",")[0].strip()

        # Check for other common proxy headers
        if "X-Real-IP" in request.headers:
            return request.headers["X-Real-IP"]

        # Fall back to the client.host attribute
        return request.client.host if request.client else "unknown"

    def get_bucket_key(self, request: Request) -> str:
        """
        Get the bucket key for the request.

        Args:
            request: The FastAPI request

        Returns:
            A string key for the bucket
        """
        parts = []
        if self.by_ip:
            client_host = self.get_client_ip(request)
            parts.append(f"ip:{client_host}")
        if self.by_endpoint:
            parts.append(f"path:{request.url.path}")
        return ":".join(parts) if parts else "global"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process the request and apply rate limiting.

        Args:
            request: The FastAPI request
            call_next: The next middleware or endpoint handler

        Returns:
            The response from the next handler or a 429 Too Many Requests response
        """
        bucket_key = self.get_bucket_key(request)
        bucket = self.buckets[bucket_key]

        if await bucket.consume():
            return await call_next(request)

        # Rate limit exceeded
        logger.warning(
            "Rate limit exceeded",
            bucket_key=bucket_key,
            path=request.url.path,
            method=request.method,
            client_host=self.get_client_ip(request),
        )

        return Response(
            content="Rate limit exceeded. Please try again later.",
            status_code=HTTP_429_TOO_MANY_REQUESTS,
            media_type="text/plain",
        )
