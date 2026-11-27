"""The normalized object event that every provider's ingest stores."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ObjectCreatedEvent(BaseModel):
    """The normalized event stored in ``event_outbox.payload``.

    The same shape for every cloud. ``event_id`` is the occurrence key, so it
    stays the same when the provider delivers the event again.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["object.created"] = "object.created"
    provider: Literal["aws"] = "aws"
    account: str = Field(description="Account that owns the SNS topic")
    region: str
    bucket: str
    key: str = Field(description="Object key, URL-decoded")
    version_id: str | None
    etag: str | None
    size: int | None
    sequencer: str | None
    occurred_at: datetime
    received_at: datetime
    event_id: str
