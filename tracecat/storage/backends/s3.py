"""S3/MinIO storage backend with threshold-based externalization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tracecat.async_runtime import run_sync
from tracecat.logger import logger
from tracecat.storage import blob
from tracecat.storage.collection import (
    get_collection_item,
    get_collection_item_sync,
    materialize_collection_values,
    materialize_collection_values_sync,
)
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    ObjectRef,
    ObjectStorage,
    StoredObject,
)
from tracecat.storage.utils import (
    cached_blob_download,
    compute_sha256,
    deserialize_object,
    serialize_object,
)


@dataclass(frozen=True, slots=True)
class _PreparedPayload:
    content: bytes
    sha256: str


def _prepare_payload(data: Any) -> _PreparedPayload:
    content = serialize_object(data)
    return _PreparedPayload(content=content, sha256=compute_sha256(content))


def _verify_and_deserialize(content: bytes, *, expected_sha256: str, key: str) -> Any:
    actual_sha256 = compute_sha256(content)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Integrity check failed for {key}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    return deserialize_object(content)


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

    def _inline_result(
        self,
        *,
        key: str,
        data: Any,
        size_bytes: int,
    ) -> InlineObject | None:
        if size_bytes > self.threshold_bytes:
            return None
        logger.debug(
            "Keeping data inline",
            key=key,
            size_bytes=size_bytes,
            threshold_bytes=self.threshold_bytes,
        )
        return InlineObject(data=data)

    def _external_result(
        self,
        *,
        key: str,
        data: Any,
        prepared: _PreparedPayload,
    ) -> ExternalObject:
        size_bytes = len(prepared.content)
        logger.info(
            "Externalized large object to S3",
            key=key,
            bucket=self.bucket,
            size_bytes=size_bytes,
            threshold_bytes=self.threshold_bytes,
        )
        return ExternalObject(
            ref=ObjectRef(
                backend="s3",
                bucket=self.bucket,
                key=key,
                size_bytes=size_bytes,
                sha256=prepared.sha256,
                content_type="application/json",
                encoding="json",
            ),
            typename=type(data).__name__,
        )

    async def store(
        self,
        key: str,
        data: Any,
    ) -> StoredObject:
        """Store data, externalizing if over threshold."""
        # Serialization and hashing can be substantial for multi-megabyte
        # workflow payloads. Keep that CPU work off both the Temporal loop and
        # the dedicated app I/O loop.
        prepared = await asyncio.to_thread(_prepare_payload, data)
        size_bytes = len(prepared.content)

        if inline := self._inline_result(key=key, data=data, size_bytes=size_bytes):
            return inline

        # Externalize to S3
        await blob.ensure_bucket_exists(self.bucket)
        await blob.upload_file(
            content=prepared.content,
            key=key,
            bucket=self.bucket,
            content_type="application/json",
        )

        return self._external_result(key=key, data=data, prepared=prepared)

    def store_sync(self, key: str, data: Any) -> StoredObject:
        """Store data while keeping CPU work on the activity thread."""
        prepared = _prepare_payload(data)
        size_bytes = len(prepared.content)
        if inline := self._inline_result(key=key, data=data, size_bytes=size_bytes):
            return inline

        run_sync(blob.ensure_bucket_exists(self.bucket))
        run_sync(
            blob.upload_file(
                content=prepared.content,
                key=key,
                bucket=self.bucket,
                content_type="application/json",
            )
        )
        return self._external_result(key=key, data=data, prepared=prepared)

    async def retrieve(self, stored: StoredObject) -> Any:
        """Retrieve data from StoredObject (inline or from S3)."""
        match stored:
            case InlineObject(data=data):
                return data
            case ExternalObject(ref=ref):
                if ref.backend != "s3":
                    raise ValueError(
                        f"S3ObjectStorage cannot retrieve from backend: {ref.backend}"
                    )

                # Use cached download (cache key is SHA-256 hash)
                content = await cached_blob_download(
                    sha256=ref.sha256,
                    bucket=ref.bucket,
                    key=ref.key,
                )

                # Integrity verification and JSON decoding are CPU work, so do
                # not serialize concurrent large payloads on either event loop.
                return await asyncio.to_thread(
                    _verify_and_deserialize,
                    content,
                    expected_sha256=ref.sha256,
                    key=ref.key,
                )
            case CollectionObject() as coll:
                if coll.index is not None:
                    # Retrieve specific item by index
                    return await get_collection_item(coll, coll.index)
                else:
                    # Retrieve and materialize entire collection
                    return await materialize_collection_values(coll)

    def retrieve_sync(self, stored: StoredObject) -> Any:
        """Retrieve data while keeping verification on the activity thread."""
        match stored:
            case InlineObject(data=data):
                return data
            case ExternalObject(ref=ref):
                if ref.backend != "s3":
                    raise ValueError(
                        f"S3ObjectStorage cannot retrieve from backend: {ref.backend}"
                    )
                content = run_sync(
                    cached_blob_download(
                        sha256=ref.sha256,
                        bucket=ref.bucket,
                        key=ref.key,
                    )
                )
                return _verify_and_deserialize(
                    content,
                    expected_sha256=ref.sha256,
                    key=ref.key,
                )
            case CollectionObject() as coll:
                if coll.index is not None:
                    return get_collection_item_sync(coll, coll.index)
                return materialize_collection_values_sync(coll)
