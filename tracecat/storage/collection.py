"""Collection storage for huge fanout results.

This module provides chunked storage for large collections, keeping Temporal
workflow history small by storing only a manifest reference.

Usage:
    from tracecat.storage.collection import store_collection, materialize_collection_values

    # Store a large collection
    collection = await store_collection(
        prefix="wf-123/stream-0/action-1/col-abc",
        items=large_list,
        element_kind="value",
    )

    # Later, materialize back to list
    values = await materialize_collection_values(collection)
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from temporalio import activity

from tracecat import config
from tracecat.logger import logger
from tracecat.storage import blob
from tracecat.storage.object import (
    CollectionObject,
    ObjectRef,
    StoredObjectValidator,
    get_object_storage,
)
from tracecat.storage.utils import (
    cached_blob_download,
    compute_sha256,
    deserialize_object,
    serialize_object,
)

if TYPE_CHECKING:
    pass


# === Manifest Schemas === #


class CollectionManifestV1(BaseModel):
    """Manifest stored at {prefix}/manifest.json.

    Contains references to all chunk blobs and collection metadata.
    """

    kind: Literal["tracecat.collection_manifest"] = "tracecat.collection_manifest"
    """Type identifier for manifest blobs."""

    version: int = 1
    """Schema version for forward compatibility."""

    count: int
    """Total number of elements in the collection."""

    chunk_size: int
    """Number of items per chunk."""

    element_kind: Literal["value", "stored_object"]
    """Whether elements are raw values or StoredObject handles."""

    chunks: list[ObjectRef]
    """References to chunk blobs in order."""


class CollectionChunkV1(BaseModel):
    """Chunk stored at {prefix}/chunks/{index}.json.

    Contains a slice of items from the collection.
    """

    kind: Literal["tracecat.collection_chunk"] = "tracecat.collection_chunk"
    """Type identifier for chunk blobs."""

    version: int = 1
    """Schema version for forward compatibility."""

    start: int
    """Global start index for this chunk (0-indexed)."""

    items: list[Any]
    """Items in this chunk. Type depends on manifest's element_kind."""


# === Storage Functions === #


async def store_collection(
    prefix: str,
    items: list[Any],
    element_kind: Literal["value", "stored_object"] = "value",
    chunk_size: int | None = None,
    bucket: str | None = None,
) -> CollectionObject:
    """Store a collection as chunked manifest in blob storage.

    Splits items into chunks, uploads each chunk, then uploads a manifest
    referencing all chunks. Returns a small CollectionObject handle.

    Args:
        prefix: Storage key prefix (e.g., "wf-123/stream-0/action-1/col-abc").
        items: List of items to store.
        element_kind: Whether items are raw values or StoredObject handles.
        chunk_size: Items per chunk (defaults to config).
        bucket: S3/MinIO bucket (defaults to config).

    Returns:
        CollectionObject handle suitable for workflow history.
    """

    chunk_size = chunk_size or config.TRACECAT__COLLECTION_CHUNK_SIZE
    bucket = bucket or config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW

    count = len(items)
    num_chunks = math.ceil(count / chunk_size) if count > 0 else 1

    logger.debug(
        "Storing collection",
        prefix=prefix,
        count=count,
        chunk_size=chunk_size,
        num_chunks=num_chunks,
    )

    await blob.ensure_bucket_exists(bucket)

    # Upload chunks
    chunk_refs: list[ObjectRef] = []
    for i in range(num_chunks):
        start = i * chunk_size
        end = min(start + chunk_size, count)
        chunk_items = items[start:end]

        chunk = CollectionChunkV1(
            start=start,
            items=chunk_items,
        )
        chunk_key = f"{prefix}/chunks/{i}.json"
        chunk_bytes = serialize_object(chunk.model_dump())

        await blob.upload_file(
            content=chunk_bytes,
            key=chunk_key,
            bucket=bucket,
            content_type="application/json",
        )

        chunk_ref = ObjectRef(
            backend="s3",
            bucket=bucket,
            key=chunk_key,
            size_bytes=len(chunk_bytes),
            sha256=compute_sha256(chunk_bytes),
            content_type="application/json",
            encoding="json",
        )
        chunk_refs.append(chunk_ref)

    # Upload manifest
    manifest = CollectionManifestV1(
        count=count,
        chunk_size=chunk_size,
        element_kind=element_kind,
        chunks=chunk_refs,
    )
    manifest_key = f"{prefix}/manifest.json"
    manifest_bytes = serialize_object(manifest.model_dump())

    await blob.upload_file(
        content=manifest_bytes,
        key=manifest_key,
        bucket=bucket,
        content_type="application/json",
    )

    manifest_ref = ObjectRef(
        backend="s3",
        bucket=bucket,
        key=manifest_key,
        size_bytes=len(manifest_bytes),
        sha256=compute_sha256(manifest_bytes),
        content_type="application/json",
        encoding="json",
    )

    logger.info(
        "Stored collection manifest",
        prefix=prefix,
        count=count,
        num_chunks=len(chunk_refs),
    )

    return CollectionObject(
        manifest_ref=manifest_ref,
        count=count,
        chunk_size=chunk_size,
        element_kind=element_kind,
        typename="list",
    )


async def _fetch_manifest(collection: CollectionObject) -> CollectionManifestV1:
    """Fetch and parse the manifest for a collection."""
    content = await cached_blob_download(
        sha256=collection.manifest_ref.sha256,
        bucket=collection.manifest_ref.bucket,
        key=collection.manifest_ref.key,
    )

    # Verify integrity
    actual_sha256 = compute_sha256(content)
    if actual_sha256 != collection.manifest_ref.sha256:
        raise ValueError(
            f"Manifest integrity check failed: expected {collection.manifest_ref.sha256}, "
            f"got {actual_sha256}"
        )

    data = deserialize_object(content)
    return CollectionManifestV1.model_validate(data)


async def _fetch_chunk(ref: ObjectRef) -> CollectionChunkV1:
    """Fetch and parse a single chunk."""
    content = await cached_blob_download(
        sha256=ref.sha256,
        bucket=ref.bucket,
        key=ref.key,
    )

    # Verify integrity
    actual_sha256 = compute_sha256(content)
    if actual_sha256 != ref.sha256:
        raise ValueError(
            f"Chunk integrity check failed: expected {ref.sha256}, got {actual_sha256}"
        )

    data = deserialize_object(content)
    return CollectionChunkV1.model_validate(data)


async def _fetch_chunk_item(ref: ObjectRef, local_index: int) -> Any:
    """Fetch a single item from a chunk.

    Args:
        ref: ObjectRef to the chunk.
        local_index: Index within the chunk's items array.

    Returns:
        The item at the given index.
    """
    chunk = await _fetch_chunk(ref)
    if local_index < 0 or local_index >= len(chunk.items):
        raise IndexError(
            f"Chunk index {local_index} out of range for chunk with {len(chunk.items)} items"
        )
    return chunk.items[local_index]


async def get_collection_page(
    collection: CollectionObject,
    offset: int = 0,
    limit: int | None = None,
) -> list[Any]:
    """Get a page of items from a collection.

    Args:
        collection: CollectionObject handle.
        offset: Starting index (0-indexed).
        limit: Maximum items to return (None = all remaining).

    Returns:
        List of items (raw values or StoredObject dicts depending on element_kind).
    """
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if offset >= collection.count:
        return []
    if limit == 0:
        return []

    # None means all remaining items; explicit 0 handled above
    if limit is None:
        limit = collection.count - offset
    end = min(offset + limit, collection.count)

    manifest = await _fetch_manifest(collection)

    # Calculate which chunks we need
    start_chunk = offset // collection.chunk_size
    end_chunk = (end - 1) // collection.chunk_size

    items: list[Any] = []
    for chunk_idx in range(start_chunk, end_chunk + 1):
        chunk = await _fetch_chunk(manifest.chunks[chunk_idx])

        # Calculate slice within this chunk
        chunk_start = chunk_idx * collection.chunk_size
        local_start = max(0, offset - chunk_start)
        local_end = min(len(chunk.items), end - chunk_start)

        items.extend(chunk.items[local_start:local_end])

    return items


async def get_collection_item(collection: CollectionObject, index: int) -> Any:
    """Get a single item from a collection by index.

    Args:
        collection: CollectionObject handle.
        index: Item index (0-indexed, supports negative indexing).

    Returns:
        The item at the given index.

    Raises:
        IndexError: If index is out of bounds.
    """
    # Handle negative indexing
    if index < 0:
        index = collection.count + index
    if index < 0 or index >= collection.count:
        raise IndexError(
            f"Collection index {index} out of range [0, {collection.count})"
        )

    manifest = await _fetch_manifest(collection)

    chunk_idx = index // collection.chunk_size
    local_idx = index % collection.chunk_size

    item = await _fetch_chunk_item(manifest.chunks[chunk_idx], local_idx)

    if collection.element_kind == "value":
        return item

    stored = StoredObjectValidator.validate_python(item)
    storage = get_object_storage()
    return await storage.retrieve(stored)


async def materialize_collection_values(
    collection: CollectionObject,
    offset: int = 0,
    limit: int | None = None,
) -> list[Any]:
    """Materialize collection items to raw values.

    For element_kind="value", this is equivalent to get_collection_page.
    For element_kind="stored_object", this retrieves each StoredObject to its value.

    WARNING: This can be expensive for large collections. Use paging when possible.

    Args:
        collection: CollectionObject handle.
        offset: Starting index (0-indexed).
        limit: Maximum items to return (None = all remaining).

    Returns:
        List of raw values.
    """
    items = await get_collection_page(collection, offset, limit)

    if collection.element_kind == "value":
        return items

    # element_kind == "stored_object": retrieve each handle
    storage = get_object_storage()
    values: list[Any] = []
    for item in items:
        stored = StoredObjectValidator.validate_python(item)
        value = await storage.retrieve(stored)
        values.append(value)

    return values


# === Temporal Activities === #


class CollectionActivities:
    """Container for collection-related Temporal activities."""

    def __new__(cls) -> None:
        raise RuntimeError("This class should not be instantiated")

    @classmethod
    def get_activities(cls) -> list[Callable[..., Any]]:
        """Load and return all activities in the class."""
        return [
            fn
            for method_name in dir(cls)
            if hasattr(
                fn := getattr(cls, method_name),
                "__temporal_activity_definition",
            )
        ]

    @staticmethod
    @activity.defn
    async def store_collection_activity(
        prefix: str,
        items: list[Any],
        element_kind: Literal["value", "stored_object"] = "value",
        chunk_size: int | None = None,
    ) -> CollectionObject:
        """Store a collection as chunked manifest.

        Temporal activity wrapper for store_collection().
        """
        return await store_collection(
            prefix=prefix,
            items=items,
            element_kind=element_kind,
            chunk_size=chunk_size,
        )

    @staticmethod
    @activity.defn
    async def get_collection_page_activity(
        collection: CollectionObject,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[Any]:
        """Get a page of items from a collection.

        Temporal activity wrapper for get_collection_page().
        """
        return await get_collection_page(collection, offset, limit)

    @staticmethod
    @activity.defn
    async def get_collection_item_activity(
        collection: CollectionObject,
        index: int,
    ) -> Any:
        """Get a single item from a collection by index.

        Temporal activity wrapper for get_collection_item().
        """
        return await get_collection_item(collection, index)

    @staticmethod
    @activity.defn
    async def materialize_collection_values_activity(
        collection: CollectionObject,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[Any]:
        """Materialize collection items to raw values.

        Temporal activity wrapper for materialize_collection_values().
        """
        return await materialize_collection_values(collection, offset, limit)
