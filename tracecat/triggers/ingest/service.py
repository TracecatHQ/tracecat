"""Handle one SNS delivery to an event source's ingest URL.

Checks run cheapest first, and nothing is written before the message is
known to be a genuine SNS message from the source's pinned topic:

1. Parse the envelope (the router has already capped the body size). Raw
   message delivery is refused: it carries no signature.
2. Look the source up by its URL token (404 if unknown) and require the
   organization's ``object_triggers`` entitlement.
3. The signed ``TopicArn`` must equal the pinned topic. A valid signature
   only proves the message came from SNS, not from this topic.
4. Verify the signature, the certificate origin and the message's age.
5. Act on the message type.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import EventOutbox, EventSource, Workspace
from tracecat.logger import logger
from tracecat.tiers.access import is_org_entitled
from tracecat.triggers.dependencies import TRIGGERS_ENTITLEMENT
from tracecat.triggers.enums import (
    EventSourceKind,
    SubscriptionStatus,
    TriggerKind,
    TriggerStatus,
)
from tracecat.triggers.ingest.rate_limit import check_source_rate
from tracecat.triggers.ingest.s3 import ParsedObjectEvent, parse_s3_message
from tracecat.triggers.ingest.schemas import SnsEnvelope, SnsMessageType
from tracecat.triggers.ingest.sns import confirm_subscription, verify_envelope
from tracecat.triggers.ingest.types import (
    IngestError,
    IngestErrorCode,
    IngestResult,
    SnsIngestSource,
    SnsTopic,
)


def parse_envelope(
    body: bytes, *, raw_delivery: str | None, message_type: str | None
) -> SnsEnvelope:
    """Parse the SNS JSON envelope.

    Args:
        body: The request body.
        raw_delivery: The ``x-amz-sns-rawdelivery`` header.
        message_type: The ``x-amz-sns-message-type`` header.

    Raises:
        IngestError: ``RAW_DELIVERY`` or ``MALFORMED``.
    """
    if raw_delivery is not None and raw_delivery.strip().lower() == "true":
        raise IngestError(
            IngestErrorCode.RAW_DELIVERY,
            "Raw message delivery is not supported. Turn it off on the subscription.",
        )
    try:
        envelope = SnsEnvelope.model_validate_json(body)
    except ValidationError:
        raise IngestError(
            IngestErrorCode.MALFORMED,
            "Not a signed SNS message. Raw message delivery must be off.",
        ) from None
    if message_type is not None and message_type != envelope.type.value:
        raise IngestError(
            IngestErrorCode.MALFORMED, "Message type header does not match the body"
        )
    return envelope


async def load_source(endpoint_token: str) -> SnsIngestSource:
    """Find the SNS source behind an ingest URL and require the entitlement.

    Raises:
        IngestError: ``SOURCE_NOT_FOUND`` or ``NOT_ENTITLED``.
    """
    stmt = (
        select(
            EventSource.id,
            EventSource.workspace_id,
            Workspace.organization_id,
            EventSource.config["topic_arn"].astext,
            EventSource.status,
        )
        .join(Workspace, Workspace.id == EventSource.workspace_id)
        .where(
            EventSource.endpoint_token == endpoint_token,
            EventSource.kind == EventSourceKind.AWS_SNS.value,
        )
    )
    # Public route: no caller role, so the token lookup bypasses RLS, the
    # same way incoming webhooks are found.
    async with get_async_session_bypass_rls_context_manager() as session:
        row = (await session.execute(stmt)).tuples().one_or_none()
        if row is None:
            raise IngestError(IngestErrorCode.SOURCE_NOT_FOUND, "Not found")
        source_id, workspace_id, organization_id, topic_arn, status = row
        if not await is_org_entitled(session, organization_id, TRIGGERS_ENTITLEMENT):
            raise IngestError(
                IngestErrorCode.NOT_ENTITLED,
                "Object storage triggers are not enabled for this organization",
            )
    return SnsIngestSource(
        id=source_id,
        workspace_id=workspace_id,
        organization_id=organization_id,
        topic_arn=topic_arn,
        status=TriggerStatus(status),
    )


async def _queue_events(
    source: SnsIngestSource, events: list[ParsedObjectEvent]
) -> int:
    """Insert one outbox row per event and commit. Returns the rows added.

    A redelivered event hits ``(source_id, occurrence_key)`` and adds nothing.
    """
    stmt = (
        pg_insert(EventOutbox)
        .values(
            [
                {
                    "workspace_id": source.workspace_id,
                    "kind": TriggerKind.OBJECT_STORAGE.value,
                    "source_id": source.id,
                    "occurrence_key": event.occurrence_key,
                    "payload": event.payload,
                }
                for event in events
            ]
        )
        .on_conflict_do_nothing(index_elements=["source_id", "occurrence_key"])
        .returning(EventOutbox.id)
    )
    async with get_async_session_bypass_rls_context_manager() as session:
        inserted = (await session.execute(stmt)).scalars().all()
        await session.commit()
    return len(inserted)


async def _already_queued(
    source: SnsIngestSource, events: list[ParsedObjectEvent]
) -> set[str]:
    """Occurrence keys of these events that the source still has queued."""
    stmt = select(EventOutbox.occurrence_key).where(
        EventOutbox.source_id == source.id,
        EventOutbox.occurrence_key.in_([event.occurrence_key for event in events]),
    )
    async with get_async_session_bypass_rls_context_manager() as session:
        return set((await session.execute(stmt)).scalars().all())


async def _charge_rate_cap(source: SnsIngestSource) -> None:
    if (retry_after := await check_source_rate(source.id)) is not None:
        raise IngestError(
            IngestErrorCode.RATE_LIMITED,
            "Event source is over its rate cap",
            retry_after_seconds=retry_after,
        )


async def _handle_notification(
    envelope: SnsEnvelope,
    *,
    source: SnsIngestSource,
    topic: SnsTopic,
    received_at: datetime,
) -> IngestResult:
    if source.status != TriggerStatus.ONLINE:
        # Refused with a 4xx, so SNS moves the message to the subscription's
        # dead-letter queue instead of retrying it for an hour.
        raise IngestError(IngestErrorCode.SOURCE_OFFLINE, "Event source is offline")
    parsed = parse_s3_message(envelope.message, topic=topic, received_at=received_at)
    if not parsed.events:
        return IngestResult(ignored=parsed.ignored)
    # Redeliveries of queued events cost nothing, so replaying one message
    # cannot use up the source's cap.
    queued_keys = await _already_queued(source, parsed.events)
    new_events = [e for e in parsed.events if e.occurrence_key not in queued_keys]
    if not new_events:
        return IngestResult(duplicates=len(parsed.events), ignored=parsed.ignored)
    await _charge_rate_cap(source)
    queued = await _queue_events(source, new_events)
    return IngestResult(
        queued=queued,
        duplicates=len(parsed.events) - queued,
        ignored=parsed.ignored,
    )


async def _handle_subscription_confirmation(
    envelope: SnsEnvelope, *, source: SnsIngestSource, topic: SnsTopic
) -> IngestResult:
    # Each confirmation calls the SNS API, so it counts against the cap.
    await _charge_rate_cap(source)
    # The model guarantees a token on confirmation messages.
    token = envelope.token or ""
    subscription_arn = await confirm_subscription(topic=topic, token=token)
    stmt = (
        update(EventSource)
        .where(
            EventSource.id == source.id,
            EventSource.workspace_id == source.workspace_id,
            # Only while the source is still pinned to the confirmed topic.
            EventSource.config["topic_arn"].astext == topic.arn,
        )
        .values(
            subscription_status=SubscriptionStatus.CONFIRMED.value,
            subscription_ref=subscription_arn,
        )
        .returning(EventSource.id)
    )
    async with get_async_session_bypass_rls_context_manager() as session:
        updated = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()
    confirmed = updated is not None
    if not confirmed:
        logger.warning(
            "Event source changed topic during subscription confirmation",
            source_id=str(source.id),
        )
    return IngestResult(confirmed=confirmed)


async def _handle_unsubscribe_confirmation(
    envelope: SnsEnvelope, *, source: SnsIngestSource, topic: SnsTopic
) -> IngestResult:
    # The signed message names the subscription that was removed. Reset the
    # source only if that is its current subscription, so a late notice for
    # an older one leaves a newer subscription's state alone.
    stmt = (
        update(EventSource)
        .where(
            EventSource.id == source.id,
            EventSource.workspace_id == source.workspace_id,
            EventSource.config["topic_arn"].astext == topic.arn,
            EventSource.subscription_ref.is_not(None),
            func.strpos(envelope.message, EventSource.subscription_ref) > 0,
        )
        .values(
            subscription_status=SubscriptionStatus.PENDING.value,
            subscription_ref=None,
        )
    )
    async with get_async_session_bypass_rls_context_manager() as session:
        await session.execute(stmt)
        await session.commit()
    return IngestResult(ignored=1)


async def handle_sns_message(
    *,
    endpoint_token: str,
    body: bytes,
    raw_delivery: str | None,
    message_type: str | None,
    received_at: datetime,
) -> IngestResult:
    """Run every check on one SNS delivery, then act on it.

    Raises:
        IngestError: The request is refused; ``code`` selects the status.
    """
    envelope = parse_envelope(
        body, raw_delivery=raw_delivery, message_type=message_type
    )
    source = await load_source(endpoint_token)
    if envelope.topic_arn != source.topic_arn:
        raise IngestError(
            IngestErrorCode.TOPIC_MISMATCH, "Topic is not the one this source accepts"
        )
    topic = SnsTopic.parse(source.topic_arn)
    await verify_envelope(envelope, topic=topic, now=received_at)

    match envelope.type:
        case SnsMessageType.NOTIFICATION:
            result = await _handle_notification(
                envelope, source=source, topic=topic, received_at=received_at
            )
        case SnsMessageType.SUBSCRIPTION_CONFIRMATION:
            result = await _handle_subscription_confirmation(
                envelope, source=source, topic=topic
            )
        case SnsMessageType.UNSUBSCRIBE_CONFIRMATION:
            result = await _handle_unsubscribe_confirmation(
                envelope, source=source, topic=topic
            )

    logger.info(
        "SNS message ingested",
        source_id=str(source.id),
        workspace_id=str(source.workspace_id),
        message_type=envelope.type.value,
        message_id=envelope.message_id,
        queued=result.queued,
        duplicates=result.duplicates,
        ignored=result.ignored,
        confirmed=result.confirmed,
    )
    return result
