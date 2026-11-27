"""Provider-neutral steps of the public ingest routes.

A provider module (``aws_sns``) verifies a delivery and turns it into
normalized events. It calls this module to find the source behind the URL
token and to queue the events: dedupe, rate cap, outbox insert.

Plain functions, not a ``BaseService``: the routes are public and have no
caller role, every query bypasses RLS (the URL token is the authority, as for
incoming webhooks), and each step opens its own short session, so the insert
rechecks the source in a transaction of its own.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import EventOutbox, EventSource, Workspace
from tracecat.tiers.access import is_org_entitled
from tracecat.triggers.dependencies import TRIGGERS_ENTITLEMENT
from tracecat.triggers.enums import EventSourceKind, TriggerKind, TriggerStatus
from tracecat.triggers.ingest.rate_limit import check_source_rate
from tracecat.triggers.ingest.types import (
    IngestError,
    IngestErrorCode,
    IngestResult,
    IngestSource,
    ParsedMessage,
    ParsedObjectEvent,
)


async def load_source(
    kind: EventSourceKind, endpoint_token: str, *, pin_key: str
) -> IngestSource:
    """Find the source behind an ingest URL and require the entitlement.

    Args:
        kind: The source kind the route serves.
        endpoint_token: The token from the URL.
        pin_key: The config key whose value every message is checked against.

    Raises:
        IngestError: ``SOURCE_NOT_FOUND`` or ``NOT_ENTITLED``.
    """
    stmt = (
        select(
            EventSource.id,
            EventSource.workspace_id,
            Workspace.organization_id,
            EventSource.config[pin_key].astext,
            EventSource.config,
            EventSource.status,
        )
        .join(Workspace, Workspace.id == EventSource.workspace_id)
        .where(
            EventSource.endpoint_token == endpoint_token,
            EventSource.kind == kind.value,
        )
    )
    # Public route: no caller role, so the token lookup bypasses RLS, the
    # same way incoming webhooks are found.
    async with get_async_session_bypass_rls_context_manager() as session:
        row = (await session.execute(stmt)).tuples().one_or_none()
        if row is None:
            raise IngestError(IngestErrorCode.SOURCE_NOT_FOUND, "Not found")
        source_id, workspace_id, organization_id, pin_value, config, status = row
        if not await is_org_entitled(session, organization_id, TRIGGERS_ENTITLEMENT):
            raise IngestError(
                IngestErrorCode.NOT_ENTITLED,
                "Object storage triggers are not enabled for this organization",
            )
    return IngestSource(
        id=source_id,
        workspace_id=workspace_id,
        organization_id=organization_id,
        status=TriggerStatus(status),
        pin_key=pin_key,
        pin_value=pin_value,
        config=config,
    )


def require_online(source: IngestSource) -> None:
    """Refuse events for a source that is offline.

    Raises:
        IngestError: ``SOURCE_OFFLINE``.
    """
    if source.status != TriggerStatus.ONLINE:
        # Refused with a 4xx, which the provider treats as final: SNS moves
        # the message to the subscription's dead-letter queue instead of
        # retrying it for an hour.
        raise IngestError(IngestErrorCode.SOURCE_OFFLINE, "Event source is offline")


async def ingest_events(source: IngestSource, parsed: ParsedMessage) -> IngestResult:
    """Queue a verified message's events: skip queued ones, charge the cap, insert.

    Call only after the provider verified the message against
    ``source.pin_value`` and ``require_online`` passed.

    Raises:
        IngestError: ``RATE_LIMITED``, or ``TOPIC_MISMATCH`` / ``SOURCE_OFFLINE``
            if the source changed after the message was verified.
    """
    if not parsed.events:
        return IngestResult(ignored=parsed.ignored)
    # Redeliveries of events still queued cost nothing. Once a row is
    # dispatched and deleted, a replay counts again; the 24 h age bound limits
    # how long a captured message can be replayed.
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


async def refuse_if_over(retry_after: int | None) -> None:
    """Refuse the request if a rate window answered with a retry delay.

    Raises:
        IngestError: ``RATE_LIMITED`` with ``retry_after_seconds``.
    """
    if retry_after is not None:
        raise IngestError(
            IngestErrorCode.RATE_LIMITED,
            "Event source is over its rate cap",
            retry_after_seconds=retry_after,
        )


async def _queue_events(source: IngestSource, events: list[ParsedObjectEvent]) -> int:
    """Insert one outbox row per event and commit. Returns the rows added.

    A redelivered event hits ``(source_id, occurrence_key)`` and adds nothing.

    Raises:
        IngestError: ``TOPIC_MISMATCH`` or ``SOURCE_OFFLINE`` if the source was
            repointed or taken offline after the message was verified, or
            ``UNAVAILABLE`` if its config changed otherwise (for example a
            new bucket pin), so the provider retries and the message is
            checked against the new config.
    """
    # Recheck the source in the insert's transaction. FOR SHARE waits for,
    # and blocks, a concurrent source update, so no event checked against an
    # older config (the old topic, or no bucket pin) is queued after the
    # update commits.
    still_pinned = (
        select(EventSource.status, EventSource.config == dict(source.config))
        .where(
            EventSource.id == source.id,
            EventSource.workspace_id == source.workspace_id,
            EventSource.config[source.pin_key].astext == source.pin_value,
        )
        .with_for_update(read=True)
    )
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
        row = (await session.execute(still_pinned)).tuples().one_or_none()
        if row is None:
            raise IngestError(
                IngestErrorCode.TOPIC_MISMATCH, "Source no longer accepts this topic"
            )
        status, same_config = row
        if status != TriggerStatus.ONLINE.value:
            raise IngestError(IngestErrorCode.SOURCE_OFFLINE, "Event source is offline")
        if not same_config:
            raise IngestError(
                IngestErrorCode.UNAVAILABLE,
                "Event source changed while the message was checked",
            )
        inserted = (await session.execute(stmt)).scalars().all()
        await session.commit()
    return len(inserted)


async def _already_queued(
    source: IngestSource, events: list[ParsedObjectEvent]
) -> set[str]:
    """Occurrence keys of these events that the source still has queued."""
    stmt = select(EventOutbox.occurrence_key).where(
        EventOutbox.source_id == source.id,
        EventOutbox.occurrence_key.in_([event.occurrence_key for event in events]),
    )
    async with get_async_session_bypass_rls_context_manager() as session:
        return set((await session.execute(stmt)).scalars().all())


async def _charge_rate_cap(source: IngestSource) -> None:
    await refuse_if_over(await check_source_rate(source.id))
