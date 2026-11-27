"""Provider-neutral domain types for the public ingest endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tracecat.exceptions import TracecatException
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.triggers.enums import TriggerStatus


class IngestErrorCode(StrEnum):
    """Why an ingest request was refused. A provider's router maps each to a status.

    One enum for every provider, since the value is part of the response body.
    Some codes (``RAW_DELIVERY``, ``CONFIRMATION_REJECTED``) only arise for
    SNS today. Each provider's router documents which answers its sender
    retries.
    """

    PAYLOAD_TOO_LARGE = "payload_too_large"
    MALFORMED = "malformed"
    RAW_DELIVERY = "raw_delivery"
    SOURCE_NOT_FOUND = "source_not_found"
    NOT_ENTITLED = "not_entitled"
    TOPIC_MISMATCH = "topic_mismatch"
    BUCKET_MISMATCH = "bucket_mismatch"
    SIGNATURE_INVALID = "signature_invalid"
    STALE_MESSAGE = "stale_message"
    SOURCE_OFFLINE = "source_offline"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


class IngestError(TracecatException):
    """An ingest request that cannot be served. ``code`` selects the response."""

    def __init__(
        self,
        code: IngestErrorCode,
        message: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class IngestSource:
    """An event source found by its ingest URL token.

    ``pin_value`` is the source config value at ``pin_key`` that a provider
    checks every message against (for SNS, the topic ARN). Events are queued
    only while the source still holds that value. ``config`` is the whole
    stored config, for the checks of the event format the source carries
    (for S3, the bucket pin).
    """

    id: uuid.UUID
    workspace_id: WorkspaceID
    organization_id: OrganizationID
    status: TriggerStatus
    pin_key: str
    pin_value: str
    # The JSONB column as stored; each layer reads only its own keys, so it
    # stays a plain mapping rather than a per-kind model.
    config: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ParsedObjectEvent:
    """One normalized object event read from a provider message, ready to queue."""

    occurrence_key: str
    # JSON-ready ``ObjectCreatedEvent``, as stored in ``event_outbox.payload``
    # (a JSONB column), so a plain dict rather than the model.
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    """The object events in one provider message, and how many records were skipped."""

    events: list[ParsedObjectEvent]
    ignored: int


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What a handled message did, for the response and the logs."""

    queued: int = 0
    duplicates: int = 0
    ignored: int = 0
    confirmed: bool = False
