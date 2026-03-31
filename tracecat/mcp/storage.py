"""Shared MCP OAuth storage helpers."""

from __future__ import annotations

from cryptography.fernet import Fernet
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper
from redis.asyncio import Redis as AsyncRedis

from tracecat.config import REDIS_URL, TRACECAT__DB_ENCRYPTION_KEY
from tracecat.logger import logger


def create_mcp_redis_client() -> AsyncRedis:
    return AsyncRedis.from_url(REDIS_URL, decode_responses=True)


def create_mcp_client_storage() -> PrefixCollectionsWrapper | FernetEncryptionWrapper:
    """Build encrypted Redis-backed storage for MCP auth state."""
    redis_store = RedisStore(client=create_mcp_redis_client())
    prefixed_store = PrefixCollectionsWrapper(redis_store, prefix="mcp")
    if TRACECAT__DB_ENCRYPTION_KEY:
        return FernetEncryptionWrapper(
            prefixed_store, fernet=Fernet(TRACECAT__DB_ENCRYPTION_KEY)
        )

    logger.warning(
        "TRACECAT__DB_ENCRYPTION_KEY is not set; "
        "MCP OAuth state will be stored unencrypted in Redis"
    )
    return prefixed_store
