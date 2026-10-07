"""Recover operation dispatch after API crashes or Temporal unavailability."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import WorkspaceSyncOperation
from tracecat.logger import logger
from tracecat.workspace_sync.operations.service import SyncOperationService


async def dispatch_pending_operations() -> None:
    """Drain a bounded batch from the durable database outbox."""
    # Cross-tenant access is confined to finding outbox records. Activities
    # always restore tenant RLS and revalidate the initiating identity.
    async with get_async_session_bypass_rls_context_manager() as session:
        operations = (
            await session.scalars(
                select(WorkspaceSyncOperation)
                .where(
                    WorkspaceSyncOperation.dispatched.is_(False),
                    WorkspaceSyncOperation.next_dispatch_at <= func.now(),
                    WorkspaceSyncOperation.status.in_(
                        ["queued", "running", "applying"]
                    ),
                )
                .order_by(
                    WorkspaceSyncOperation.next_dispatch_at, WorkspaceSyncOperation.id
                )
                .limit(100)
            )
        ).all()
    for operation in operations:
        try:
            role = Role.model_validate(operation.actor)
            async with SyncOperationService.with_session(role) as service:
                await service.dispatch(operation)
                await service.session.execute(
                    update(WorkspaceSyncOperation)
                    .where(
                        WorkspaceSyncOperation.id == operation.id,
                        WorkspaceSyncOperation.workspace_id == role.workspace_id,
                        WorkspaceSyncOperation.attempt == operation.attempt,
                        WorkspaceSyncOperation.status == operation.status,
                    )
                    .values(dispatched=True)
                )
                await service.session.commit()
        except Exception:
            # Persist backoff even for malformed actor payloads. A poison row
            # must not occupy the first batch forever or starve newer work.
            delay = min(300, 5 * 2 ** min(operation.dispatch_attempts, 6))
            async with get_async_session_bypass_rls_context_manager() as session:
                await session.execute(
                    update(WorkspaceSyncOperation)
                    .where(
                        WorkspaceSyncOperation.id == operation.id,
                        WorkspaceSyncOperation.workspace_id == operation.workspace_id,
                        WorkspaceSyncOperation.attempt == operation.attempt,
                        WorkspaceSyncOperation.status == operation.status,
                        WorkspaceSyncOperation.dispatched.is_(False),
                    )
                    .values(
                        dispatch_attempts=operation.dispatch_attempts + 1,
                        next_dispatch_at=datetime.now(UTC) + timedelta(seconds=delay),
                    )
                )
                await session.commit()
            logger.warning("Background operation dispatch failed; will retry")


async def run_dispatcher(shutdown_event: asyncio.Event) -> None:
    """Keep accepted operations moving even after their browser or API disappears."""
    while not shutdown_event.is_set():
        try:
            await dispatch_pending_operations()
        except Exception:
            logger.warning("Background operation outbox unavailable; will retry")
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=5)
        except TimeoutError:
            pass
