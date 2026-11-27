"""Wire models for S3 event notifications.

Reference:
- S3 event message structure:
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-content-structure.html
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class S3EventMessage(BaseModel):
    """The ``Message`` of an S3 event notification.

    Records stay raw here because only created events must carry the object
    fields; each one is validated as an ``S3CreatedRecord`` after its event
    name is checked, and other event names are skipped unvalidated.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    records: list[dict[str, Any]] = Field(alias="Records", max_length=100)


class _S3Bucket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(min_length=3, max_length=63)


class _S3Object(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    # URL-encoded as sent. Encoding at most triples the 1024-byte key.
    key: str = Field(min_length=1, max_length=3072)
    # Bounded so a fabricated value cannot fail JSON encoding (a 500 retried).
    size: int | None = Field(default=None, ge=0, le=2**63 - 1)
    etag: str | None = Field(default=None, alias="eTag", max_length=128)
    version_id: str | None = Field(default=None, alias="versionId", max_length=1024)
    sequencer: str | None = Field(default=None, max_length=128)


class _S3Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    bucket: _S3Bucket
    object: _S3Object


class S3CreatedRecord(BaseModel):
    """One ``ObjectCreated:*`` record."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    event_source: Literal["aws:s3"] = Field(alias="eventSource")
    event_name: str = Field(alias="eventName", max_length=128)
    aws_region: str = Field(alias="awsRegion", min_length=1, max_length=32)
    event_time: datetime = Field(alias="eventTime")
    s3: _S3Entity
