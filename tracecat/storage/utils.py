"""Storage utilities for serialization and hashing."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import orjson
from aiocache import Cache

from tracecat.logger import logger
from tracecat.storage import blob

if TYPE_CHECKING:
    from tracecat.storage.object import InlineObject, StoredObject

# Module-level cache for blob downloads (TTL: 5 minutes)
_blob_cache = Cache(Cache.MEMORY, ttl=300)


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

    await _blob_cache.set(sha256, content)
    logger.debug("Cached blob", sha256=sha256[:16], size_bytes=len(content))
    return content


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

            # Create updated TaskResult with resolved InlineObject
            resolved_task = task_result.model_copy(
                update={
                    "result": InlineObject(data=resolved_data),
                    "result_typename": type(resolved_data).__name__,
                }
            )
            resolved_actions[ref] = resolved_task
        result["ACTIONS"] = resolved_actions

    return result
