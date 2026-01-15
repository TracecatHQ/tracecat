"""Inline storage backend - no externalization."""

from __future__ import annotations

from typing import Any

from tracecat.storage.object import (
    InlineObject,
    ObjectStorage,
    StoredObject,
)


class InlineObjectStorage(ObjectStorage):
    """In-memory storage - no externalization, results always inline.

    This is the default implementation that maintains current behavior.
    Use this for testing or when S3/MinIO is not available.
    """

    async def store(
        self,
        key: str,
        data: Any,
    ) -> StoredObject:
        """Store data inline (never externalizes)."""
        del key  # Unused in inline storage
        return InlineObject(data=data)

    async def retrieve(self, stored: StoredObject) -> Any:
        """Return inline data from StoredObject."""
        match stored:
            case InlineObject(data=data):
                return data
            case _:
                raise NotImplementedError(
                    f"InlineObjectStorage does not support {stored.type} references. "
                    "This ref was created by a different storage backend."
                )
