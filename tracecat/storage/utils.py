"""Storage utilities for serialization and hashing."""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import orjson
from aiocache import Cache

from tracecat.logger import logger
from tracecat.storage import blob

if TYPE_CHECKING:
    from aiocache.base import BaseCache

    from tracecat.storage.object import InlineObject, StoredObject

# Cache configuration
MAX_CACHEABLE_BLOB_SIZE = 50 * 1024 * 1024  # 50 MB per item
BLOB_CACHE_MAX_BYTES = 500 * 1024 * 1024  # 500 MB total pool
BLOB_CACHE_TTL = 300.0  # 5 minutes


class SizedMemoryCache:
    """Byte-aware wrapper around aiocache SimpleMemoryCache with LRU eviction."""

    def __init__(self, max_bytes: int, ttl: float = 300.0):
        self._cache: BaseCache = Cache(Cache.MEMORY, ttl=ttl)
        self._sizes: OrderedDict[str, int] = OrderedDict()
        self._total_bytes = 0
        self._max_bytes = max_bytes
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> bytes | None:
        value: bytes | None = await self._cache.get(key)
        if value is None and key in self._sizes:
            # TTL expired in underlying cache, sync our tracking
            async with self._lock:
                if key in self._sizes:
                    self._total_bytes -= self._sizes.pop(key)
        elif value is not None:
            # Mark as recently used (LRU)
            async with self._lock:
                if key in self._sizes:
                    self._sizes.move_to_end(key)
        return value

    async def set(self, key: str, value: bytes) -> None:
        size = len(value)
        if size > self._max_bytes:
            logger.debug(
                "Cache entry too large to store",
                key=key,
                size_bytes=size,
                max_bytes=self._max_bytes,
            )
            return
        async with self._lock:
            # Remove old entry if exists
            if key in self._sizes:
                self._total_bytes -= self._sizes.pop(key)

            # Evict LRU items until we have room
            while self._total_bytes + size > self._max_bytes and self._sizes:
                oldest_key, oldest_size = self._sizes.popitem(last=False)
                self._total_bytes -= oldest_size
                await self._cache.delete(oldest_key)

            await self._cache.set(key, value)
            self._sizes[key] = size
            self._total_bytes += size

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def item_count(self) -> int:
        return len(self._sizes)


# Module-level cache for blob downloads and S3 Select results
_blob_cache = SizedMemoryCache(max_bytes=BLOB_CACHE_MAX_BYTES, ttl=BLOB_CACHE_TTL)


def serialize_object(data: Any) -> bytes:
    """Serialize data to JSON bytes using orjson.

    Args:
        data: Any JSON-serializable data

    Returns:
        UTF-8 encoded JSON bytes
    """
    return orjson.dumps(data, default=str)


def deserialize_object(content: bytes) -> Any:
    """Deserialize JSON bytes to Python object using orjson.

    Args:
        content: UTF-8 encoded JSON bytes

    Returns:
        Deserialized Python object
    """
    return orjson.loads(content)


def compute_sha256(content: bytes) -> str:
    """Compute SHA-256 hash of content.

    Args:
        content: Bytes to hash

    Returns:
        Hex-encoded SHA-256 hash
    """
    return hashlib.sha256(content).hexdigest()


async def cached_blob_download(sha256: str, bucket: str, key: str) -> bytes:
    """Download blob with caching by SHA-256 hash.

    Uses content-addressed caching: the SHA-256 hash is the cache key since
    objects are immutable. Cache hits avoid S3 round-trips.

    Blobs larger than MAX_CACHEABLE_BLOB_SIZE (50 MB) are not cached to
    prevent memory bloat.

    Args:
        sha256: SHA-256 hash of the content (cache key).
        bucket: S3/MinIO bucket name.
        key: Object key within the bucket.

    Returns:
        Downloaded blob content as bytes.
    """
    cached = await _blob_cache.get(sha256)
    if cached is not None:
        logger.debug("Blob cache hit", sha256=sha256[:16])
        return cached

    content = await blob.download_file(key=key, bucket=bucket)

    # Skip caching large blobs to prevent memory bloat
    if len(content) <= MAX_CACHEABLE_BLOB_SIZE:
        await _blob_cache.set(sha256, content)
        logger.debug("Cached blob", sha256=sha256[:16], size_bytes=len(content))
    else:
        logger.debug(
            "Blob too large to cache",
            sha256=sha256[:16],
            size_bytes=len(content),
            max_size=MAX_CACHEABLE_BLOB_SIZE,
        )

    return content


async def cached_select_item(
    sha256: str, bucket: str, key: str, local_index: int
) -> Any:
    """Select a single item from a chunk using S3 Select with caching.

    Uses (sha256, local_index) as the cache key since chunks are immutable.
    Cache hits avoid S3 Select round-trips. Shares the same cache pool as
    blob downloads for unified memory management.

    Args:
        sha256: SHA-256 hash of the chunk (used for cache key).
        bucket: S3/MinIO bucket name.
        key: Object key within the bucket.
        local_index: Index within the chunk's items array.

    Returns:
        The item at the given index.
    """
    cache_key = f"select:{sha256}:{local_index}"
    cached = await _blob_cache.get(cache_key)
    if cached is not None:
        logger.debug("Select cache hit", sha256=sha256[:16], index=local_index)
        return deserialize_object(cached)

    expression = f"SELECT s.items[{local_index}] FROM s3object s"
    result_bytes = await blob.select_object_content(
        key=key,
        bucket=bucket,
        expression=expression,
    )

    # S3 Select returns {"_1": <item>} for indexed array access
    data = deserialize_object(result_bytes)
    item = data.get("_1")

    # Serialize item for byte-aware cache
    item_bytes = serialize_object(item)
    if len(item_bytes) <= MAX_CACHEABLE_BLOB_SIZE:
        await _blob_cache.set(cache_key, item_bytes)
        logger.debug("Cached select item", sha256=sha256[:16], index=local_index)
    else:
        logger.debug(
            "Select item too large to cache",
            sha256=sha256[:16],
            index=local_index,
            size_bytes=len(item_bytes),
            max_size=MAX_CACHEABLE_BLOB_SIZE,
        )
    return item


async def resolve_to_inline(stored: StoredObject) -> InlineObject:
    """Resolve any StoredObject to InlineObject by fetching external data.

    This provides uniformity when comparing or returning results - all
    StoredObjects become InlineObjects with the actual data.

    Args:
        stored: Any StoredObject variant (InlineObject, ExternalObject, CollectionObject)

    Returns:
        InlineObject containing the resolved data
    """
    from tracecat.storage.object import (
        CollectionObject,
        ExternalObject,
        InlineObject,
        get_object_storage,
    )

    match stored:
        case InlineObject():
            return stored
        case ExternalObject() | CollectionObject():
            storage = get_object_storage()
            data = await storage.retrieve(stored)
            return InlineObject(data=data)
        case _:
            raise TypeError(f"Expected StoredObject, got {type(stored).__name__}")


async def resolve_execution_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Resolve all StoredObjects in an ExecutionContext dict to InlineObjects.

    This normalizes a serialized ExecutionContext by converting any ExternalObject
    references to InlineObjects, making externalization transparent for comparison
    or display purposes.

    The function resolves recursively:
    - TRIGGER: StoredObject -> InlineObject
    - ACTIONS[ref].result: StoredObject -> InlineObject
    - Nested ExecutionContext structures (e.g., child workflow results)

    Args:
        ctx: Serialized ExecutionContext dict

    Returns:
        ExecutionContext dict with all StoredObjects resolved to InlineObjects
    """
    from pydantic import TypeAdapter, ValidationError

    from tracecat.dsl.schemas import ExecutionContext, TaskResult
    from tracecat.storage.object import InlineObject, StoredObject

    ExecutionContextTA = TypeAdapter(ExecutionContext)
    StoredObjectTA = TypeAdapter(StoredObject)

    def is_execution_context(data: Any) -> bool:
        """Check if data validates as an ExecutionContext."""
        if not isinstance(data, dict):
            return False
        try:
            ExecutionContextTA.validate_python(data)
            return True
        except ValidationError as e:
            logger.debug(
                "Not an ExecutionContext",
                data_keys=list(data.keys()) if isinstance(data, dict) else None,
                error=str(e),
            )
            return False

    # Validate input structure
    validated = ExecutionContextTA.validate_python(ctx)
    result: dict[str, Any] = dict(validated)

    # Resolve TRIGGER if present
    if result.get("TRIGGER") is not None:
        trigger_stored = StoredObjectTA.validate_python(result["TRIGGER"])
        trigger_inline = await resolve_to_inline(trigger_stored)
        result["TRIGGER"] = trigger_inline

    # Resolve each action result
    if "ACTIONS" in result:
        resolved_actions: dict[str, TaskResult] = {}
        for ref, task_data in result["ACTIONS"].items():
            task_result = TaskResult.model_validate(task_data)
            result_inline = await resolve_to_inline(task_result.result)

            # Recursively resolve if the result data is a nested ExecutionContext
            resolved_data = result_inline.data
            if is_execution_context(resolved_data):
                resolved_data = await resolve_execution_context(resolved_data)

            # Apply collection_index to extract the actual item for scatter results
            collection_index = task_result.collection_index
            if collection_index is not None and isinstance(resolved_data, list):
                resolved_data = resolved_data[collection_index]

            # Create updated TaskResult with resolved InlineObject
            resolved_task = task_result.model_copy(
                update={
                    "result": InlineObject(data=resolved_data),
                    "result_typename": type(resolved_data).__name__,
                    "collection_index": None,  # Clear since we've extracted the item
                }
            )
            resolved_actions[ref] = resolved_task
        result["ACTIONS"] = resolved_actions

    return result
