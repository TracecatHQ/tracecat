"""Object storage abstraction for externalizing large payloads.

This module provides a pluggable storage backend for workflow data that may be too
large to store directly in Temporal workflow history. Large payloads (action results,
triggers, etc.) are stored in S3/MinIO with only a small reference kept in history.

Usage:
    # Get the configured storage backend
    storage = get_object_storage()

    # Store data (may externalize if over threshold)
    stored = await storage.store("results/my-key", large_data)
    if stored.is_externalized:
        # Data was externalized, use stored.ref
        pass
    else:
        # Data was kept inline, use stored.data
        pass

    # Retrieve externalized data
    if stored.ref:
        data = await storage.retrieve(stored.ref)
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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

    kind: (
        Literal["action_result", "trigger", "scatter_manifest", "return_value"] | None
    ) = None
    """Optional classification of the externalized data."""


class StoredObject(BaseModel):
    """Result of a store operation.

    Either data is inline (ref=None, data=value) or externalized (ref=ObjectRef, data=None).
    Exactly one of data or ref must be set.
    """

    data: Any | None = None
    """The data if kept inline, None if externalized."""

    ref: ObjectRef | None = None
    """Reference to externalized data, None if inline."""

    @property
    def is_externalized(self) -> bool:
        """Whether the data was externalized to blob storage."""
        return self.ref is not None

    @model_validator(mode="after")
    def _validate_data_or_ref(self) -> StoredObject:
        """Ensure exactly one of data or ref is set."""
        if self.data is None and self.ref is None:
            raise ValueError("Either data or ref must be set")
        if self.data is not None and self.ref is not None:
            raise ValueError("Only one of data or ref can be set, not both")
        return self


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
        *,
        kind: Literal["action_result", "trigger", "scatter_manifest", "return_value"]
        | None = None,
    ) -> StoredObject:
        """Store data, potentially externalizing if over threshold.

        Args:
            key: Storage key (e.g., "results/wf-123/action-1/uuid.json")
            data: The data to store (must be JSON-serializable)
            kind: Optional classification of the data

        Returns:
            StoredObject with either inline data or an ObjectRef
        """
        raise NotImplementedError

    @abstractmethod
    async def retrieve(self, ref: ObjectRef) -> Any:
        """Retrieve data from an ObjectRef.

        Args:
            ref: Reference to the externalized data

        Returns:
            The deserialized data

        Raises:
            ValueError: If integrity check fails
            FileNotFoundError: If the object doesn't exist
        """
        raise NotImplementedError


# === Implementations === #


class InMemoryObjectStorage(ObjectStorage):
    """In-memory storage - no externalization, results always inline.

    This is the default implementation that maintains current behavior.
    Use this for testing or when S3/MinIO is not available.
    """

    async def store(
        self,
        key: str,
        data: Any,
        *,
        kind: Literal["action_result", "trigger", "scatter_manifest", "return_value"]
        | None = None,
    ) -> StoredObject:
        """Store data inline (never externalizes)."""
        del key, kind  # Unused in inline storage
        return StoredObject(data=data, ref=None)

    async def retrieve(self, ref: ObjectRef) -> Any:
        """Not supported - InMemoryObjectStorage never creates refs."""
        del ref  # Unused - we always raise
        raise NotImplementedError(
            "InMemoryObjectStorage does not support externalized references. "
            "This ref was created by a different storage backend."
        )


class S3ObjectStorage(ObjectStorage):
    """S3/MinIO storage with threshold-based externalization.

    Data below the threshold is kept inline. Data above the threshold
    is serialized to JSON and uploaded to S3/MinIO.
    """

    def __init__(
        self,
        bucket: str,
        threshold_bytes: int = 256 * 1024,  # 256 KB default
    ) -> None:
        """Initialize S3 storage backend.

        Args:
            bucket: S3/MinIO bucket name
            threshold_bytes: Externalize data larger than this (default 256 KB)
        """
        self.bucket = bucket
        self.threshold_bytes = threshold_bytes

    async def store(
        self,
        key: str,
        data: Any,
        *,
        kind: Literal["action_result", "trigger", "scatter_manifest", "return_value"]
        | None = None,
    ) -> StoredObject:
        """Store data, externalizing if over threshold."""
        # Serialize to JSON
        serialized = serialize_object(data)
        size_bytes = len(serialized)

        # Keep inline if under threshold
        if size_bytes <= self.threshold_bytes:
            logger.debug(
                "Keeping data inline",
                key=key,
                size_bytes=size_bytes,
                threshold_bytes=self.threshold_bytes,
            )
            return StoredObject(data=data, ref=None)

        # Externalize to S3
        sha256 = compute_sha256(serialized)

        # Import here to avoid circular imports and allow testing without blob module
        from tracecat.storage import blob

        await blob.ensure_bucket_exists(self.bucket)
        await blob.upload_file(
            content=serialized,
            key=key,
            bucket=self.bucket,
            content_type="application/json",
        )

        logger.info(
            "Externalized large payload to S3",
            key=key,
            bucket=self.bucket,
            size_bytes=size_bytes,
            threshold_bytes=self.threshold_bytes,
            kind=kind,
        )

        ref = ObjectRef(
            backend="s3",
            bucket=self.bucket,
            key=key,
            size_bytes=size_bytes,
            sha256=sha256,
            content_type="application/json",
            encoding="json",
            kind=kind,
        )

        return StoredObject(data=None, ref=ref)

    async def retrieve(self, ref: ObjectRef) -> Any:
        """Retrieve and deserialize data from S3."""
        if ref.backend != "s3":
            raise ValueError(
                f"S3ObjectStorage cannot retrieve from backend: {ref.backend}"
            )

        from tracecat.storage import blob

        content = await blob.download_file(key=ref.key, bucket=ref.bucket)

        # Verify integrity
        actual_sha256 = compute_sha256(content)
        if actual_sha256 != ref.sha256:
            raise ValueError(
                f"Integrity check failed for {ref.key}: "
                f"expected {ref.sha256}, got {actual_sha256}"
            )

        logger.debug(
            "Retrieved externalized payload from S3",
            key=ref.key,
            bucket=ref.bucket,
            size_bytes=len(content),
        )

        return deserialize_object(content)


# === Serialization Helpers === #


def serialize_object(data: Any) -> bytes:
    """Serialize data to JSON bytes.

    Args:
        data: Any JSON-serializable data

    Returns:
        UTF-8 encoded JSON bytes
    """
    return json.dumps(data, default=str, separators=(",", ":")).encode("utf-8")


def deserialize_object(content: bytes) -> Any:
    """Deserialize JSON bytes to Python object.

    Args:
        content: UTF-8 encoded JSON bytes

    Returns:
        Deserialized Python object
    """
    return json.loads(content.decode("utf-8"))


def compute_sha256(content: bytes) -> str:
    """Compute SHA-256 hash of content.

    Args:
        content: Bytes to hash

    Returns:
        Hex-encoded SHA-256 hash
    """
    return hashlib.sha256(content).hexdigest()


# === Dependency Injection === #

_object_storage: ObjectStorage | None = None


def get_object_storage() -> ObjectStorage:
    """Get the configured object storage backend.

    Returns InMemoryObjectStorage by default (no externalization).
    Returns S3ObjectStorage when TRACECAT__RESULT_EXTERNALIZATION_ENABLED=true.
    """
    global _object_storage
    if _object_storage is not None:
        return _object_storage

    if config.TRACECAT__RESULT_EXTERNALIZATION_ENABLED:
        storage: ObjectStorage = S3ObjectStorage(
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
            threshold_bytes=config.TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES,
        )
        logger.info(
            "Using S3ObjectStorage for result externalization",
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
            threshold_bytes=config.TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES,
        )
    else:
        storage = InMemoryObjectStorage()
        logger.debug("Using InMemoryObjectStorage (externalization disabled)")

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
