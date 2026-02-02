from __future__ import annotations

from datetime import UTC, datetime

from tracecat import config
from tracecat.db.models import Case, CaseEvent
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client


async def publish_case_event_payload(
    *,
    event_id: str,
    case_id: str,
    workspace_id: str,
    event_type: str,
    created_at: datetime,
) -> None:
    """Publish a case event to the Redis streams pipeline."""
    if not config.TRACECAT__CASE_TRIGGERS_ENABLED:
        return

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    payload = {
        "event_id": event_id,
        "case_id": case_id,
        "workspace_id": workspace_id,
        "event_type": event_type,
        "created_at": created_at.isoformat(),
    }

    client = await get_redis_client()
    await client.xadd(
        stream_key=config.TRACECAT__CASE_TRIGGERS_STREAM_KEY,
        fields=payload,
        maxlen=config.TRACECAT__CASE_TRIGGERS_MAXLEN,
        approximate=True,
    )


async def publish_case_event(event: CaseEvent, case: Case) -> None:
    """Publish a case event to the Redis streams pipeline."""
    if event.id is None:
        logger.warning("Skipping case event publish; event has no id")
        return

    created_at = event.created_at or datetime.now(UTC)
    await publish_case_event_payload(
        event_id=str(event.id),
        case_id=str(case.id),
        workspace_id=str(case.workspace_id),
        event_type=event.type.value if hasattr(event.type, "value") else event.type,
        created_at=created_at,
    )
