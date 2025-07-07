"""Redis client with connection pooling and retry logic for streaming."""

import asyncio
import os
from typing import Any

import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.logger import logger


class RedisClient:
    """Singleton Redis client with connection pooling and retry logic."""

    _instance: "RedisClient | None" = None
    _pool: ConnectionPool | None = None
    _client: redis.Redis | None = None

    def __new__(cls) -> "RedisClient":
        """Create a singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the Redis client."""
        if RedisClient._pool is None:
            # Get Redis URL from environment
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            logger.info("Initializing Redis connection pool", url=redis_url)

            # Create connection pool
            RedisClient._pool = redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                health_check_interval=30,
            ).connection_pool

    @property
    def client(self) -> redis.Redis:
        """Get the Redis client instance."""
        if RedisClient._client is None:
            if RedisClient._pool is None:
                raise RuntimeError("Redis connection pool not initialized")
            RedisClient._client = redis.Redis(connection_pool=RedisClient._pool)
        return RedisClient._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(RedisError),
    )
    async def xadd(
        self,
        stream_key: str,
        fields: dict[str, Any],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        """Add an entry to a Redis stream with retry logic.

        Args:
            stream_key: The Redis stream key
            fields: Dictionary of field-value pairs to add
            maxlen: Maximum stream length (approximate if approximate=True)
            approximate: Whether to use approximate trimming for better performance

        Returns:
            The ID of the added entry
        """
        try:
            kwargs: dict[str, Any] = {"fields": fields}
            if maxlen is not None:
                kwargs["maxlen"] = maxlen
                kwargs["approximate"] = approximate

            message_id = await self.client.xadd(name=stream_key, **kwargs)
            logger.debug(
                "Added entry to Redis stream",
                stream_key=stream_key,
                message_id=message_id,
            )
            return message_id
        except RedisError as e:
            logger.error(
                "Failed to add entry to Redis stream",
                stream_key=stream_key,
                error=str(e),
            )
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(RedisError),
    )
    async def xread(
        self,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        """Read from one or more Redis streams with retry logic.

        Args:
            streams: Dictionary mapping stream keys to starting IDs
            count: Maximum number of entries to read per stream
            block: Block for this many milliseconds if no data available

        Returns:
            List of tuples (stream_key, [(message_id, fields)])
        """
        try:
            result = await self.client.xread(streams=streams, count=count, block=block)  # type: ignore
            return result
        except RedisError as e:
            logger.error(
                "Failed to read from Redis stream",
                streams=list(streams.keys()),
                error=str(e),
            )
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(RedisError),
    )
    async def xrange(
        self,
        stream_key: str,
        min_id: str = "-",
        max_id: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        """Read a range of entries from a Redis stream.

        Args:
            stream_key: The Redis stream key
            min_id: Minimum ID (inclusive), "-" for beginning
            max_id: Maximum ID (inclusive), "+" for end
            count: Maximum number of entries to return

        Returns:
            List of tuples (message_id, fields)
        """
        try:
            result = await self.client.xrange(
                name=stream_key, min=min_id, max=max_id, count=count
            )
            return result
        except RedisError as e:
            logger.error(
                "Failed to read range from Redis stream",
                stream_key=stream_key,
                error=str(e),
            )
            raise

    async def ping(self) -> bool:
        """Check if Redis connection is alive."""
        try:
            await self.client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close the Redis connection."""
        if RedisClient._client:
            await RedisClient._client.close()
            RedisClient._client = None
        if RedisClient._pool:
            await RedisClient._pool.disconnect()
            RedisClient._pool = None
        logger.info("Redis connection closed")


# Global singleton instance
_redis_client: RedisClient | None = None
_lock = asyncio.Lock()


async def get_redis_client() -> RedisClient:
    """Get the global Redis client instance."""
    global _redis_client
    if _redis_client is None:
        async with _lock:
            if _redis_client is None:
                _redis_client = RedisClient()
    return _redis_client
