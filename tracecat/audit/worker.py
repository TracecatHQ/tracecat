from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence

from tracecat.audit.types import AuditEventPayload
from tracecat.config import TEMPORAL__CLUSTER_QUEUE
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger

_BUFFER: asyncio.Queue[AuditEventPayload] = asyncio.Queue()


async def enqueue_event(payload: AuditEventPayload) -> None:
    await _BUFFER.put(payload)


async def submit_batch(events: Sequence[AuditEventPayload]) -> None:
    if not events:
        return
    client = await get_temporal_client()
    workflow_id = f"audit-persist-{uuid.uuid4()}"
    await client.start_workflow(
        "audit-persist-workflow",
        [event.model_dump() for event in events],
        id=workflow_id,
        task_queue=TEMPORAL__CLUSTER_QUEUE,
    )


async def audit_buffer_worker(
    *,
    interval_seconds: float = 15,
) -> None:
    logger.info(
        "Audit buffer worker started",
        interval_seconds=interval_seconds,
    )
    pending: list[AuditEventPayload] = []
    flush_task: asyncio.Task | None = None

    async def flush():
        nonlocal pending, flush_task
        if not pending:
            return
        batch = pending
        pending = []
        if flush_task:
            flush_task = None
        try:
            await submit_batch(batch)
        except Exception as exc:
            logger.error("Failed to submit audit batch", error=str(exc))
            for event in batch:
                await _BUFFER.put(event)

    async def flush_after_delay():
        try:
            await asyncio.sleep(interval_seconds)
            await flush()
        except asyncio.CancelledError:
            return

    while True:
        event = await _BUFFER.get()
        pending.append(event)
        if flush_task is None:
            flush_task = asyncio.create_task(flush_after_delay())
