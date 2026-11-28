"""Turn an S3 event notification into normalized object events."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import unquote_plus

import orjson
from pydantic import ValidationError

from tracecat.triggers.ingest.object_storage.schemas import (
    S3CreatedRecord,
    S3EventMessage,
)
from tracecat.triggers.ingest.schemas import ObjectCreatedEvent
from tracecat.triggers.ingest.types import (
    IngestError,
    IngestErrorCode,
    ParsedMessage,
    ParsedObjectEvent,
)
from tracecat.triggers.types import build_occurrence_key

# The longest object key S3 allows, in UTF-8 bytes.
MAX_OBJECT_KEY_BYTES = 1024
# Every field is length-bounded, so a stored event stays well under this;
# the check is the backstop for event_outbox.payload.
MAX_EVENT_PAYLOAD_BYTES = 16 * 1024

_CREATED_PREFIX = "ObjectCreated:"
# The source config key of the bucket pin: the one bucket a source accepts.
BUCKET_PIN_KEY = "bucket"


def _malformed(message: str) -> IngestError:
    return IngestError(IngestErrorCode.MALFORMED, message)


def _to_event(
    record: S3CreatedRecord, *, account: str, received_at: datetime
) -> ParsedObjectEvent:
    obj = record.s3.object
    bucket = record.s3.bucket.name
    # S3 form-encodes keys in events: spaces arrive as "+".
    key = unquote_plus(obj.key)
    stored_text = [
        value
        for value in (
            key,
            bucket,
            record.aws_region,
            obj.etag,
            obj.version_id,
            obj.sequencer,
        )
        if value
    ]
    # S3 allows U+0000 in keys, but PostgreSQL JSONB cannot store it. Refusing
    # (4xx) sends the message to the dead-letter queue; a 5xx would only make
    # SNS retry an insert that always fails. (Lone surrogates never get here:
    # both JSON parsers reject them, which is also a 4xx.)
    if any("\x00" in value for value in stored_text):
        raise _malformed("Event contains a NUL character")
    if len(key.encode()) > MAX_OBJECT_KEY_BYTES:
        raise _malformed("Object key is longer than S3 allows")
    occurrence_key = build_occurrence_key(
        bucket=bucket, key=key, version_id=obj.version_id, sequencer=obj.sequencer
    )
    event = ObjectCreatedEvent(
        account=account,
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
    message: str,
    *,
    account: str,
    received_at: datetime,
    source_config: Mapping[str, Any],
) -> ParsedMessage:
    """Read the object-created events from an S3 notification.

    Only ``ObjectCreated:*`` records become events. The ``s3:TestEvent`` that
    S3 sends when notifications are configured, and every other event name,
    count as ignored.

    If the source pins a bucket, a created record for any other bucket refuses
    the whole message, so none of its events is queued. The check reads the
    record's ``s3.bucket.name``. S3 records carry no account ID (the bucket
    owner appears only as a legacy retail customer ID), so there is no account
    pin: the topic ARN already fixes the topic owner's account.

    Args:
        message: The notification text.
        account: The account that owns the topic, stored on each event.
        received_at: When the delivery arrived.
        source_config: The source's stored config, read for the bucket pin.

    Raises:
        IngestError: ``MALFORMED`` if the message is not an S3 notification,
            or ``BUCKET_MISMATCH`` for a record from another bucket than the pin.
            Retrying cannot fix either, so the caller answers 4xx.
    """
    bucket_pin = source_config.get(BUCKET_PIN_KEY)
    try:
        data = orjson.loads(message)
    except orjson.JSONDecodeError:
        raise _malformed("Message is not an S3 event notification") from None

    match data:
        case {"Event": "s3:TestEvent"}:
            return ParsedMessage(events=[], ignored=1)
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
        if bucket_pin is not None and record.s3.bucket.name != bucket_pin:
            raise IngestError(
                IngestErrorCode.BUCKET_MISMATCH,
                "Bucket is not the one this source accepts",
            )
        events.append(_to_event(record, account=account, received_at=received_at))
    return ParsedMessage(events=events, ignored=ignored)
