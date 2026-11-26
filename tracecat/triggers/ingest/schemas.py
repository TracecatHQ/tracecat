"""Wire models for SNS ingest: the SNS envelope, S3 records, the stored event.

References:
- SNS message and JSON formats:
  https://docs.aws.amazon.com/sns/latest/dg/sns-message-and-json-formats.html
- S3 event message structure:
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-content-structure.html
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SnsMessageType(StrEnum):
    NOTIFICATION = "Notification"
    SUBSCRIPTION_CONFIRMATION = "SubscriptionConfirmation"
    UNSUBSCRIBE_CONFIRMATION = "UnsubscribeConfirmation"


class SnsEnvelope(BaseModel):
    """A signed SNS message as POSTed to an HTTPS subscription.

    ``timestamp`` stays the raw string: the signature covers it as sent.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    type: SnsMessageType = Field(alias="Type")
    message_id: str = Field(alias="MessageId", min_length=1, max_length=128)
    topic_arn: str = Field(alias="TopicArn", min_length=1, max_length=1024)
    message: str = Field(alias="Message")
    timestamp: str = Field(alias="Timestamp", min_length=1, max_length=64)
    signature_version: Literal["1", "2"] = Field(alias="SignatureVersion")
    signature: str = Field(alias="Signature", min_length=1, max_length=4096)
    signing_cert_url: str = Field(alias="SigningCertURL", min_length=1, max_length=2048)
    subject: str | None = Field(default=None, alias="Subject", max_length=1024)
    # Confirmation messages only. ``subscribe_url`` is signed, so it is read
    # for the signature, and never fetched.
    token: str | None = Field(default=None, alias="Token", max_length=2048)
    subscribe_url: str | None = Field(
        default=None, alias="SubscribeURL", max_length=4096
    )

    @model_validator(mode="after")
    def _confirmation_fields(self) -> Self:
        if self.type != SnsMessageType.NOTIFICATION and (
            not self.token or not self.subscribe_url
        ):
            raise ValueError("Confirmation messages carry Token and SubscribeURL")
        return self

    def string_to_sign(self) -> str:
        """The canonical text SNS signed: ``name\\nvalue\\n`` per signed field."""
        if self.type == SnsMessageType.NOTIFICATION:
            fields: list[tuple[str, str | None]] = [
                ("Message", self.message),
                ("MessageId", self.message_id),
                ("Subject", self.subject),
                ("Timestamp", self.timestamp),
                ("TopicArn", self.topic_arn),
                ("Type", self.type.value),
            ]
        else:
            fields = [
                ("Message", self.message),
                ("MessageId", self.message_id),
                ("SubscribeURL", self.subscribe_url),
                ("Timestamp", self.timestamp),
                ("Token", self.token),
                ("TopicArn", self.topic_arn),
                ("Type", self.type.value),
            ]
        return "".join(
            f"{name}\n{value}\n" for name, value in fields if value is not None
        )


# ----- S3 event notifications -----


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
    size: int | None = Field(default=None, ge=0)
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


# ----- Stored event -----


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
