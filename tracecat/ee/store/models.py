from __future__ import annotations

from pydantic import BaseModel


class ObjectRef(BaseModel):
    """A reference to an object. This goes into the temporalio.Payload.data field."""

    metadata: dict[str, bytes]
    """Metadata about the object."""

    size: int
    """The size of the object in bytes."""

    digest: str
    """The digest of the object."""

    key: str
    """The key of the object."""
