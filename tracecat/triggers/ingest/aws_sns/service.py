"""Handle one SNS delivery to an event source's ingest URL.

Checks run cheapest first, and nothing is written before the message is
known to be a genuine SNS message from the source's pinned topic:

1. Parse the envelope (the router has already capped the body size). Raw
   message delivery is refused: it carries no signature.
2. Look the source up by its URL token (404 if unknown) and require the
   organization's ``object_triggers`` entitlement. The URL a rotation
   replaced, during its overlap, takes notifications only (404 otherwise).
3. The signed ``TopicArn`` must equal the pinned topic. A valid signature
   only proves the message came from SNS, not from this topic.
4. Verify the signature, the certificate origin and the message's age.
5. Act on the message type. Notifications go to the generic ingest service;
   subscription confirmations are SNS only and handled here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import func, update

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import EventSource
from tracecat.logger import logger
from tracecat.triggers.enums import EventSourceKind, SubscriptionStatus
from tracecat.triggers.ingest.aws_sns.confirm import confirm_subscription
from tracecat.triggers.ingest.aws_sns.rate_limit import check_confirmation_rate
from tracecat.triggers.ingest.aws_sns.schemas import SnsEnvelope, SnsMessageType
from tracecat.triggers.ingest.aws_sns.signature import verify_envelope
from tracecat.triggers.ingest.aws_sns.types import SnsTopic
from tracecat.triggers.ingest.object_storage.s3 import parse_s3_message
from tracecat.triggers.ingest.service import (
    ingest_events,
    load_source,
    refuse_if_over,
    require_online,
)
from tracecat.triggers.ingest.types import (
    IngestError,
    IngestErrorCode,
    IngestResult,
    IngestSource,
)

# The source config key that holds the pinned topic ARN.
TOPIC_ARN_KEY = "topic_arn"


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


async def _handle_notification(
    envelope: SnsEnvelope,
    *,
    source: IngestSource,
    topic: SnsTopic,
    received_at: datetime,
) -> IngestResult:
    require_online(source)
    parsed = parse_s3_message(
        envelope.message, account=topic.account, received_at=received_at
    )
    return await ingest_events(source, parsed)


async def _handle_subscription_confirmation(
    envelope: SnsEnvelope,
    *,
    source: IngestSource,
    topic: SnsTopic,
    endpoint_token: str,
) -> IngestResult:
    # Each confirmation calls the SNS API, so it has a cap of its own, apart
    # from notifications: replayed confirmations cannot crowd out events.
    await refuse_if_over(await check_confirmation_rate(source.id))
    # The model guarantees a token on confirmation messages.
    token = envelope.token or ""
    subscription_arn = await confirm_subscription(topic=topic, token=token)
    stmt = (
        update(EventSource)
        .where(
            EventSource.id == source.id,
            EventSource.workspace_id == source.workspace_id,
            # Only while the source is still pinned to the confirmed topic,
            # and the URL confirmed is still its current one: a rotation in
            # between leaves the new URL pending.
            EventSource.config[TOPIC_ARN_KEY].astext == topic.arn,
            EventSource.endpoint_token == endpoint_token,
        )
        .values(
            subscription_status=SubscriptionStatus.CONFIRMED.value,
            subscription_ref=subscription_arn,
            # The new URL is live: the URL a rotation replaced stops now.
            previous_endpoint_token=None,
            previous_endpoint_token_expires_at=None,
        )
        .returning(EventSource.id)
    )
    async with get_async_session_bypass_rls_context_manager() as session:
        updated = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()
    confirmed = updated is not None
    if not confirmed:
        logger.warning(
            "Event source changed topic or URL during subscription confirmation",
            source_id=str(source.id),
        )
    return IngestResult(confirmed=confirmed)


async def _handle_unsubscribe_confirmation(
    envelope: SnsEnvelope, *, source: IngestSource, topic: SnsTopic
) -> IngestResult:
    # The signed message names the subscription that was removed. Reset the
    # source only if that is its current subscription, so a late notice for
    # an older one leaves a newer subscription's state alone.
    stmt = (
        update(EventSource)
        .where(
            EventSource.id == source.id,
            EventSource.workspace_id == source.workspace_id,
            EventSource.config[TOPIC_ARN_KEY].astext == topic.arn,
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
    source = await load_source(
        EventSourceKind.AWS_SNS, endpoint_token, pin_key=TOPIC_ARN_KEY
    )
    if source.previous_url and envelope.type != SnsMessageType.NOTIFICATION:
        # The replaced URL can no longer change the subscription state.
        raise IngestError(IngestErrorCode.SOURCE_NOT_FOUND, "Not found")
    if envelope.topic_arn != source.pin_value:
        raise IngestError(
            IngestErrorCode.TOPIC_MISMATCH, "Topic is not the one this source accepts"
        )
    topic = SnsTopic.parse(source.pin_value)
    await verify_envelope(envelope, topic=topic, now=received_at)

    match envelope.type:
        case SnsMessageType.NOTIFICATION:
            result = await _handle_notification(
                envelope, source=source, topic=topic, received_at=received_at
            )
        case SnsMessageType.SUBSCRIPTION_CONFIRMATION:
            result = await _handle_subscription_confirmation(
                envelope, source=source, topic=topic, endpoint_token=endpoint_token
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
        previous_url=source.previous_url,
        queued=result.queued,
        duplicates=result.duplicates,
        ignored=result.ignored,
        confirmed=result.confirmed,
    )
    return result
