"""Redis client with connection pooling and retry logic for streaming."""

import asyncio
import os
from typing import Any

import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError
from redis.typing import KeyT, StreamIdT
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.config import REDIS_CHAT_TTL_SECONDS
from tracecat.logger import logger


class RedisClient:
    """Singleton Redis client with connection pooling and retry logic."""

    _instance: "RedisClient | None" = None
    _pool: ConnectionPool | None = None
    _client: redis.Redis | None = None
    _loop: asyncio.AbstractEventLoop | None = None

    def __new__(cls) -> "RedisClient":
        """Create a singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the Redis client."""
        self._init_pool()

    def _init_pool(self) -> None:
        """Ensure the Redis connection pool is initialized."""
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

    async def _get_client(self) -> redis.Redis:
        """Get a Redis client bound to the current event loop."""
        self._init_pool()
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        loop_changed = (
            current_loop is not None
            and RedisClient._loop is not None
            and RedisClient._loop is not current_loop
        )

        if loop_changed:
            logger.debug(
                "Detected event loop change for Redis client, resetting connection",
                loop_id=id(RedisClient._loop),
                new_loop_id=id(current_loop),
            )
            await self.close()
            self._init_pool()

        if RedisClient._client is None:
            if RedisClient._pool is None:
                raise RuntimeError("Redis connection pool not initialized")
            RedisClient._client = redis.Redis(connection_pool=RedisClient._pool)
            RedisClient._loop = current_loop

        return RedisClient._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((RedisError, RuntimeError)),
    )
    async def xadd(
        self,
        stream_key: str,
        fields: dict[str, Any],
        maxlen: int | None = None,
        approximate: bool = True,
        expire_seconds: int | None = None,
    ) -> str:
        """Add an entry to a Redis stream with retry logic.

        Args:
            stream_key: The Redis stream key
            fields: Dictionary of field-value pairs to add
            maxlen: Maximum stream length (approximate if approximate=True)
            approximate: Whether to use approximate trimming for better performance
            expire_seconds: TTL in seconds for the stream key (None for no expiration)

        Returns:
            The ID of the added entry
        """
        expire_seconds = expire_seconds or REDIS_CHAT_TTL_SECONDS
        try:
            client = await self._get_client()
            kwargs: dict[str, Any] = {"fields": fields}
            if maxlen is not None:
                kwargs["maxlen"] = maxlen
                kwargs["approximate"] = approximate

            message_id = await client.xadd(name=stream_key, **kwargs)

            # Set TTL if specified
            if expire_seconds is not None:
                await client.expire(name=stream_key, time=expire_seconds)
                logger.trace(
                    "Set TTL for Redis stream",
                    stream_key=stream_key,
                    expire_seconds=expire_seconds,
                )

            logger.trace(
                "Added entry to Redis stream",
                stream_key=stream_key,
                message_id=message_id,
            )
            return message_id
        except (RedisError, RuntimeError) as e:
            logger.error(
                "Failed to add entry to Redis stream",
                stream_key=stream_key,
                error=str(e),
            )
            await self._reset_connection()
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((RedisError, RuntimeError)),
    )
    async def xread(
        self,
        streams: dict[KeyT, StreamIdT],
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
            client = await self._get_client()
            return await client.xread(streams=streams, count=count, block=block)
        except (RedisError, RuntimeError) as e:
            logger.error(
                "Failed to read from Redis stream",
                streams=list(streams.keys()),
                error=str(e),
            )
            await self._reset_connection()
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((RedisError, RuntimeError)),
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
            client = await self._get_client()
            result = await client.xrange(
                name=stream_key, min=min_id, max=max_id, count=count
            )
            return result
        except (RedisError, RuntimeError) as e:
            logger.error(
                "Failed to read range from Redis stream",
                stream_key=stream_key,
                error=str(e),
            )
            await self._reset_connection()
            raise

    async def ping(self) -> bool:
        """Check if Redis connection is alive."""
        try:
            client = await self._get_client()
            await client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close the Redis connection."""
        closed = False
        if RedisClient._client:
            await RedisClient._client.close()
            RedisClient._client = None
            closed = True
        if RedisClient._pool:
            await RedisClient._pool.disconnect()
            RedisClient._pool = None
            closed = True
        RedisClient._loop = None
        if closed:
            logger.info("Redis connection closed")

    async def _reset_connection(self) -> None:
        """Reset the Redis client and pool so the next attempt reinitializes them."""
        logger.warning("Resetting Redis connection after transport error")
        await self.close()
        # Re-initialize the pool for subsequent calls
        self._init_pool()


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
