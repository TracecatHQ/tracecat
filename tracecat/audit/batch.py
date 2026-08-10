"""Bounded, best-effort delivery for related audit events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from tracecat.audit.types import AuditAction, AuditResourceType
from tracecat.logger import logger

MAX_PENDING_BATCH_DELIVERIES = 64
MAX_CONCURRENT_BATCH_POSTS = 4
BATCH_DELIVERY_DEADLINE_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class AuditBatchEvent:
    """One already-enriched event ready for webhook delivery."""

    request_payload: dict[str, Any]
    resource_type: AuditResourceType
    action: AuditAction


@dataclass(frozen=True, slots=True)
class AuditBatchDelivery:
    """A group of audit events sharing one webhook configuration."""

    webhook_url: str
    events: tuple[AuditBatchEvent, ...]
    headers: dict[str, str] | None
    verify_ssl: bool


# Strong refs to in-flight batches; done callbacks release them.
_batch_delivery_tasks: set[asyncio.Task[None]] = set()


def spawn_audit_batch(delivery: AuditBatchDelivery) -> None:
    """Post a resolved audit batch on a bounded background task."""
    for stranded in [
        task for task in _batch_delivery_tasks if task.get_loop().is_closed()
    ]:
        _batch_delivery_tasks.discard(stranded)
    if len(_batch_delivery_tasks) >= MAX_PENDING_BATCH_DELIVERIES:
        logger.warning(
            "Dropped audit webhook batch; pending limit reached",
            event_count=len(delivery.events),
            max_pending=MAX_PENDING_BATCH_DELIVERIES,
        )
        return
    task = asyncio.get_running_loop().create_task(deliver_audit_batch(delivery))
    _batch_delivery_tasks.add(task)
    task.add_done_callback(_batch_delivery_tasks.discard)


async def deliver_audit_batch(delivery: AuditBatchDelivery) -> None:
    """Deliver a batch with one client, bounded concurrency, and one deadline."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCH_POSTS)

    async def post_event(client: httpx.AsyncClient, event: AuditBatchEvent) -> None:
        response: httpx.Response | None = None
        try:
            async with semaphore:
                response = await client.post(
                    delivery.webhook_url,
                    json=event.request_payload,
                    headers=delivery.headers,
                )
                response.raise_for_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Failed to deliver audit webhook batch event",
                error_type=type(exc).__name__,
                status_code=response.status_code if response is not None else None,
                resource_type=event.resource_type,
                action=event.action,
            )

    try:
        async with asyncio.timeout(BATCH_DELIVERY_DEADLINE_SECONDS):
            async with httpx.AsyncClient(
                timeout=BATCH_DELIVERY_DEADLINE_SECONDS,
                verify=delivery.verify_ssl,
            ) as client:
                await asyncio.gather(
                    *(post_event(client, event) for event in delivery.events)
                )
    except TimeoutError:
        logger.warning(
            "Audit webhook batch exceeded delivery deadline",
            event_count=len(delivery.events),
            deadline_seconds=BATCH_DELIVERY_DEADLINE_SECONDS,
        )
