"""Shared helpers for materializing case durations."""

from __future__ import annotations

import uuid
from time import monotonic

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.enums import CaseEventType
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    try_pg_advisory_xact_lock,
)
from tracecat.db.models import CaseDurationDefinition as CaseDurationDefinitionDB
from tracecat.db.models import Workspace
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

STATUS_CHANGED_ALIASES = frozenset(
    (CaseEventType.CASE_CLOSED, CaseEventType.CASE_REOPENED)
)
_WORKSPACE_ROLE_CACHE_TTL_SECONDS = 60.0
_workspace_role_cache: dict[uuid.UUID, tuple[float, Role]] = {}


async def sync_case_duration(
    workspace_id: uuid.UUID,
    case_id: uuid.UUID,
    *,
    event_types: set[str] | None = None,
) -> bool:
    """Materialize one case's durations in a fresh transaction.

    Returns false only when another transaction holds the case sync lock.
    """
    lock_key = derive_lock_key_from_parts(
        "case-duration-sync",
        str(workspace_id),
        str(case_id),
    )
    async with get_async_session_bypass_rls_context_manager() as session:
        role = await _get_service_role(session, workspace_id)
        if role is None:
            return True

        if not await _event_types_require_sync(
            session,
            workspace_id=workspace_id,
            event_types=event_types or set(),
        ):
            logger.debug(
                "Skipping case duration sync; no definitions use event types",
                workspace_id=str(workspace_id),
                case_id=str(case_id),
                event_types=sorted(event_types or set()),
            )
            return True

        locked = await try_pg_advisory_xact_lock(session, lock_key)
        if not locked:
            await session.rollback()
            logger.debug(
                "Case duration sync already locked",
                workspace_id=str(workspace_id),
                case_id=str(case_id),
            )
            return False

        try:
            await CaseDurationService(session=session, role=role).sync_case_durations(
                case_id
            )
            await session.commit()
            return True
        except TracecatNotFoundError:
            await session.rollback()
            logger.info(
                "Skipping case duration sync for deleted case",
                workspace_id=str(workspace_id),
                case_id=str(case_id),
            )
            return True
        except Exception:
            await session.rollback()
            raise


async def _event_types_require_sync(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    event_types: set[str],
) -> bool:
    if not event_types:
        return True

    parsed_event_types: list[CaseEventType] = []
    for event_type in event_types:
        try:
            parsed_event_types.append(CaseEventType(event_type))
        except ValueError:
            logger.warning(
                "Unknown case event type in duration sync job",
                workspace_id=str(workspace_id),
                event_type=event_type,
            )
            return True

    if CaseEventType.CASE_CREATED in parsed_event_types:
        # Case creation is the initial materialization, so every definition
        # must get its possibly-placeholder row, like case-scoped backfills.
        return True

    matching_event_types = list(parsed_event_types)
    if (
        any(event_type in STATUS_CHANGED_ALIASES for event_type in parsed_event_types)
        and CaseEventType.STATUS_CHANGED not in matching_event_types
    ):
        matching_event_types.append(CaseEventType.STATUS_CHANGED)

    stmt = (
        select(CaseDurationDefinitionDB.id)
        .where(
            CaseDurationDefinitionDB.workspace_id == workspace_id,
            or_(
                CaseDurationDefinitionDB.start_event_type.in_(matching_event_types),
                CaseDurationDefinitionDB.end_event_type.in_(matching_event_types),
            ),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _get_service_role(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Role | None:
    now = monotonic()
    if cached := _workspace_role_cache.get(workspace_id):
        expires_at, role = cached
        if now < expires_at:
            return role
        del _workspace_role_cache[workspace_id]

    result = await session.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalars().first()
    if workspace is None:
        logger.info(
            "Skipping case duration sync for deleted workspace",
            workspace_id=str(workspace_id),
        )
        return None
    role = Role(
        type="service",
        workspace_id=workspace_id,
        organization_id=workspace.organization_id,
        user_id=None,
        service_id="tracecat-case-duration-sync",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-case-duration-sync"],
    )
    # CaseTriggerConsumer also caches positive workspace roles. Bound this cache
    # so a deleted workspace is rechecked rather than retained for process life.
    _workspace_role_cache[workspace_id] = (
        now + _WORKSPACE_ROLE_CACHE_TTL_SECONDS,
        role,
    )
    return role
