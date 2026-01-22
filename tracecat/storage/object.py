"""Object storage abstraction for externalizing large payloads.

This module provides a pluggable storage backend for workflow data that may be too
large to store directly in Temporal workflow history. Large payloads (action results,
triggers, etc.) are stored in S3/MinIO with only a small reference kept in history.

Usage:
    # Get the configured storage backend
    storage = get_object_storage()

    # Store data (may externalize if over threshold)
    stored = await storage.store("results/my-key", large_data)

    # Pattern match on the result
    match stored:
        case InlineObject(data=data):
            # Data was kept inline
            pass
        case ExternalObject(ref=ref):
            # Data was externalized to blob storage
            pass

    # Or simply retrieve (works for both variants)
    data = await storage.retrieve(stored)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    Discriminator,
    Field,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from tracecat import config
from tracecat.logger import logger

# === Types === #


class ObjectRef(BaseModel):
    """Reference to an externalized object in blob storage.

    This is a typed reference that replaces large payloads in workflow context.
    Only dereference ObjectRef instances created by Tracecat - never arbitrary URIs.
    """

    backend: Literal["s3"] = "s3"
    """Storage backend type."""

    bucket: str
    """S3/MinIO bucket name."""

    key: str
    """Object key within the bucket."""

    size_bytes: int
    """Size of the serialized data in bytes."""

    sha256: str
    """SHA-256 hash for integrity verification."""

    content_type: str = "application/json"
    """MIME type of the stored content."""

    encoding: Literal["json", "json+zstd", "json+gzip"] = "json"
    """Encoding/compression applied to the data."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    """When the object was stored."""


class _StoredObjectBase(BaseModel):
    """Base class for stored object types with shared serialization behavior.

    Subclasses must define a `type` field as the discriminator.
    """

    typename: str | None = None
    """Optional type name of the original data (e.g., 'str', 'list')."""

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        """Always include type field for discriminated union."""
        result: dict[str, Any] = handler(self)
        # Access type from subclass (InlineObject or ExternalObject)
        result["type"] = self.type  # pyright: ignore[reportAttributeAccessIssue]
        return result


class InlineObject[T: Any](_StoredObjectBase):
    """Data stored inline (not externalized)."""

    type: Literal["inline"] = "inline"
    """Discriminator for tagged union."""

    data: T
    """The inline data. Can be any JSON-serializable value, including None."""

    @model_validator(mode="after")
    def _set_typename(self) -> InlineObject[T]:
        if self.typename is None:
            self.typename = type(self.data).__name__
        return self


class ExternalObject(_StoredObjectBase):
    """Data externalized to blob storage."""

    type: Literal["external"] = "external"
    """Discriminator for tagged union."""

    ref: ObjectRef
    """Reference to the externalized data in blob storage."""


class CollectionObject(_StoredObjectBase):
    """Handle to a collection manifest stored in blob storage.

    Represents huge collections without embedding list[StoredObject] in history.
    The manifest contains chunk references for paged access.

    This is a small, history-safe handle. The actual data lives in chunked
    blobs referenced by the manifest.

    Attributes:
        manifest_ref: Reference to the manifest blob containing chunk refs.
        count: Total number of elements in the collection.
        chunk_size: Number of items per chunk.
        element_kind: Whether elements are raw values or StoredObject handles.
        schema_version: Manifest schema version for forward compatibility.
    """

    type: Literal["collection"] = "collection"
    """Discriminator for tagged union."""

    manifest_ref: ObjectRef
    """Reference to the manifest blob in blob storage."""

    count: int
    """Total number of elements in the collection."""

    chunk_size: int
    """Number of items per chunk."""

    element_kind: Literal["value", "stored_object"]
    """Whether elements are raw values or StoredObject handles.

    - 'value': Collection is list[Any] (pure JSON values).
    - 'stored_object': Collection is list[StoredObject] (handles to retrieve).
    """

    schema_version: int = 1
    """Manifest schema version for forward compatibility."""

    index: int | None = None
    """Optional index into the collection.

    When set, indicates this handle refers to a specific item in the collection
    rather than the entire collection. Downstream consumers should retrieve
    just that item using get_collection_item(collection, index).
    """

    def __len__(self) -> int:
        return self.count

    def at(self, index: int) -> CollectionObject:
        """Return a copy of this collection handle pointing to a specific index.

        This creates a new handle that refers to a single item in the collection.
        The manifest_ref remains the same, but the index field is set.
        """
        return self.model_copy(update={"index": index})

    @model_validator(mode="after")
    def _set_typename(self) -> CollectionObject:
        if self.typename is None:
            self.typename = "list"
        return self


StoredObject = Annotated[
    InlineObject | ExternalObject | CollectionObject,
    Discriminator("type"),
]
"""Result of a store operation.

With uniform envelope design, all results are wrapped in StoredObject:
- InlineObject: Data kept in workflow history (small payloads).
- ExternalObject: Single blob externalized to S3/MinIO (large payloads).
- CollectionObject: Chunked manifest for huge collections (fanout outputs).

Uses 'type' field as discriminator for Pydantic validation with TypeAdapter.
"""
StoredObjectValidator: TypeAdapter[StoredObject] = TypeAdapter(StoredObject)


# === Interface === #


class ObjectStorage(ABC):
    """Pluggable storage backend for externalized objects.

    Implementations decide whether to externalize based on data size.
    """

    @abstractmethod
    async def store(
        self,
        key: str,
        data: Any,
    ) -> StoredObject:
        """Store data, potentially externalizing if over threshold.

        Args:
            key: Storage key (e.g., "results/wf-123/action-1/uuid.json")
            data: The data to store (must be JSON-serializable)

        Returns:
            StoredObject with either inline data or an ObjectRef
        """
        raise NotImplementedError

    @abstractmethod
    async def retrieve(self, stored: StoredObject) -> Any:
        """Retrieve data from a StoredObject.

        Args:
            stored: StoredObject with either inline data or an ObjectRef

        Returns:
            The data (either inline or fetched from storage)

        Raises:
            ValueError: If integrity check fails (for externalized data)
            FileNotFoundError: If the object doesn't exist (for externalized data)
        """
        raise NotImplementedError


# === Dependency Injection === #

_object_storage: ObjectStorage | None = None


def get_object_storage() -> ObjectStorage:
    """Get the configured object storage backend.

    Returns InlineObjectStorage by default (no externalization).
    Returns S3ObjectStorage when TRACECAT__RESULT_EXTERNALIZATION_ENABLED=true.
    """
    global _object_storage
    if _object_storage is not None:
        return _object_storage

    # Import here to avoid circular imports
    from tracecat.storage.backends import InlineObjectStorage, S3ObjectStorage

    if config.TRACECAT__RESULT_EXTERNALIZATION_ENABLED:
        storage = S3ObjectStorage(
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
            threshold_bytes=config.TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES,
        )
        logger.info(
            "Using S3ObjectStorage for result externalization",
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
            threshold_bytes=config.TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES,
        )
    else:
        storage = InlineObjectStorage()
        logger.debug("Using InlineObjectStorage (externalization disabled)")

    _object_storage = storage
    return storage


def set_object_storage(storage: ObjectStorage | None) -> None:
    """Set the object storage backend (for testing).

    Args:
        storage: Storage backend to use, or None to reset to default
    """
    global _object_storage
    _object_storage = storage


def reset_object_storage() -> None:
    """Reset object storage to None, forcing re-initialization on next get."""
    global _object_storage
    _object_storage = None


# === Object Key Helpers === #


def trigger_key(workspace_id: str, wf_exec_id: str) -> str:
    """Generate S3 key for workflow trigger inputs.

    Format: {workspace_id}/{wf_exec_id}/trigger.json
    """
    return f"{workspace_id}/{wf_exec_id}/trigger.json"


def return_key(workspace_id: str, wf_exec_id: str) -> str:
    """Generate S3 key for workflow return value.

    Format: {workspace_id}/{wf_exec_id}/return.json
    """
    return f"{workspace_id}/{wf_exec_id}/return.json"


def action_key(workspace_id: str, wf_exec_id: str, stream_id: str, ref: str) -> str:
    """Generate S3 key for action result.

    Format: {workspace_id}/{wf_exec_id}/actions/{stream_id}/{ref}.json
    """
    return f"{workspace_id}/{wf_exec_id}/actions/{stream_id}/{ref}.json"


def action_collection_prefix(
    workspace_id: str, wf_exec_id: str, stream_id: str, ref: str
) -> str:
    """Generate S3 key prefix for action collection (manifest + chunks).

    Format: {workspace_id}/{wf_exec_id}/actions/{stream_id}/{ref}
    """
    return f"{workspace_id}/{wf_exec_id}/actions/{stream_id}/{ref}"


__all__ = [
    # Types
    "CollectionObject",
    "ExternalObject",
    "InlineObject",
    "ObjectRef",
    "ObjectStorage",
    "StoredObject",
    "StoredObjectValidator",
    # DI
    "get_object_storage",
    "reset_object_storage",
    "set_object_storage",
    # Key helpers
    "action_collection_prefix",
    "action_key",
    "return_key",
    "trigger_key",
]
