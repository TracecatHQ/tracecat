"""S3/MinIO storage backend with threshold-based externalization."""

from __future__ import annotations

from typing import Any

from tracecat.logger import logger
from tracecat.storage import blob
from tracecat.storage.collection import (
    get_collection_item,
    materialize_collection_values,
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
            return InlineObject(data=data)

        # Externalize to S3
        sha256 = compute_sha256(serialized)

        await blob.ensure_bucket_exists(self.bucket)
        await blob.upload_file(
            content=serialized,
            key=key,
            bucket=self.bucket,
            content_type="application/json",
        )

        logger.info(
            "Externalized large object to S3",
            key=key,
            bucket=self.bucket,
            size_bytes=size_bytes,
            threshold_bytes=self.threshold_bytes,
        )

        ref = ObjectRef(
            backend="s3",
            bucket=self.bucket,
            key=key,
            size_bytes=size_bytes,
            sha256=sha256,
            content_type="application/json",
            encoding="json",
        )

        return ExternalObject(ref=ref, typename=type(data).__name__)

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

                # Verify integrity (still needed - cache may return stale data on hash collision)
                actual_sha256 = compute_sha256(content)
                if actual_sha256 != ref.sha256:
                    raise ValueError(
                        f"Integrity check failed for {ref.key}: "
                        f"expected {ref.sha256}, got {actual_sha256}"
                    )

                return deserialize_object(content)
            case CollectionObject() as coll:
                if coll.index is not None:
                    # Retrieve specific item by index
                    return await get_collection_item(coll, coll.index)
                else:
                    # Retrieve and materialize entire collection
                    return await materialize_collection_values(coll)
