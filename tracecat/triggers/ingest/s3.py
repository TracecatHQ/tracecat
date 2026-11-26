"""Turn the S3 event notification inside an SNS message into object events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import unquote_plus

import orjson
from pydantic import ValidationError

from tracecat.triggers.ingest.schemas import (
    ObjectCreatedEvent,
    S3CreatedRecord,
    S3EventMessage,
)
from tracecat.triggers.ingest.types import IngestError, IngestErrorCode, SnsTopic
from tracecat.triggers.types import build_occurrence_key

# The longest object key S3 allows, in UTF-8 bytes.
MAX_OBJECT_KEY_BYTES = 1024
# Every field is length-bounded, so a stored event stays well under this;
# the check is the backstop for event_outbox.payload.
MAX_EVENT_PAYLOAD_BYTES = 16 * 1024

_CREATED_PREFIX = "ObjectCreated:"


@dataclass(frozen=True, slots=True)
class ParsedObjectEvent:
    occurrence_key: str
    payload: dict[str, Any]
    """JSON-ready ``ObjectCreatedEvent``, as stored in ``event_outbox.payload``."""


@dataclass(frozen=True, slots=True)
class ParsedS3Message:
    events: list[ParsedObjectEvent]
    ignored: int


def _malformed(message: str) -> IngestError:
    return IngestError(IngestErrorCode.MALFORMED, message)


def _to_event(
    record: S3CreatedRecord, *, topic: SnsTopic, received_at: datetime
) -> ParsedObjectEvent:
    obj = record.s3.object
    bucket = record.s3.bucket.name
    # S3 form-encodes keys in events: spaces arrive as "+".
    key = unquote_plus(obj.key)
    if len(key.encode()) > MAX_OBJECT_KEY_BYTES:
        raise _malformed("Object key is longer than S3 allows")
    stored_text = (
        key,
        bucket,
        record.aws_region,
        obj.etag,
        obj.version_id,
        obj.sequencer,
    )
    if any("\x00" in value for value in stored_text if value):
        # S3 allows it in keys, but PostgreSQL JSONB cannot store U+0000.
        # Refusing (4xx) sends the message to the dead-letter queue; a 5xx
        # would only make SNS retry an insert that always fails.
        raise _malformed("Event contains a NUL character")
    occurrence_key = build_occurrence_key(
        bucket=bucket, key=key, version_id=obj.version_id, sequencer=obj.sequencer
    )
    event = ObjectCreatedEvent(
        account=topic.account,
        region=record.aws_region,
        bucket=bucket,
        key=key,
        version_id=obj.version_id,
        etag=obj.etag,
        size=obj.size,
        sequencer=obj.sequencer,
        occurred_at=record.event_time,
        received_at=received_at,
        event_id=occurrence_key,
    )
    payload = event.model_dump(mode="json")
    if len(orjson.dumps(payload)) > MAX_EVENT_PAYLOAD_BYTES:
        raise _malformed("Event is too large to store")
    return ParsedObjectEvent(occurrence_key=occurrence_key, payload=payload)


def parse_s3_message(
    message: str, *, topic: SnsTopic, received_at: datetime
) -> ParsedS3Message:
    """Read the object-created events from an S3 notification.

    Only ``ObjectCreated:*`` records become events. The ``s3:TestEvent`` that
    S3 sends when notifications are configured, and every other event name,
    count as ignored.

    Raises:
        IngestError: ``MALFORMED`` if the message is not an S3 notification.
            Retrying cannot fix it, so the caller answers 4xx.
    """
    try:
        data = orjson.loads(message)
    except orjson.JSONDecodeError:
        raise _malformed("Message is not an S3 event notification") from None

    match data:
        case {"Event": "s3:TestEvent"}:
            return ParsedS3Message(events=[], ignored=1)
        case _:
            pass

    try:
        records = S3EventMessage.model_validate(data).records
    except ValidationError:
        raise _malformed("Message is not an S3 event notification") from None
    if not records:
        raise _malformed("S3 event notification has no records")

    events: list[ParsedObjectEvent] = []
    ignored = 0
    for raw in records:
        match raw:
            case {"eventName": str(name)} if name.startswith(_CREATED_PREFIX):
                pass
            case _:
                ignored += 1
                continue
        try:
            record = S3CreatedRecord.model_validate(raw)
        except ValidationError:
            raise _malformed("S3 record is missing object fields") from None
        events.append(_to_event(record, topic=topic, received_at=received_at))
    return ParsedS3Message(events=events, ignored=ignored)
