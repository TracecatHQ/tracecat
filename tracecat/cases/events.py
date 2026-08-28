"""Case event persistence service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.agent_sessions.service import (
    CaseAgentSessionInteractionService,
)
from tracecat.cases.durations.materialization import sync_case_duration
from tracecat.cases.durations.sync_queue import (
    enqueue_case_duration_sync_after_commit,
)
from tracecat.cases.enums import (
    CaseAgentSessionInteractionOperation,
    CaseEventType,
)
from tracecat.cases.schemas import CaseEventVariant, CaseViewedEvent
from tracecat.cases.triggers.publisher import publish_case_event_payload
from tracecat.db.models import Case, CaseEvent
from tracecat.db.session_events import AfterCommitQueue
from tracecat.service import BaseWorkspaceService

CASE_VIEW_EVENT_DEDUP_WINDOW = timedelta(minutes=5)
_CHILD_MUTATION_EVENTS = frozenset(
    {
        CaseEventType.ATTACHMENT_CREATED,
        CaseEventType.ATTACHMENT_DELETED,
        CaseEventType.COMMENT_DELETED,
        CaseEventType.COMMENT_REPLY_DELETED,
        CaseEventType.DROPDOWN_VALUE_CHANGED,
        CaseEventType.TABLE_ROW_LINKED,
        CaseEventType.TABLE_ROW_UNLINKED,
        CaseEventType.TAG_ADDED,
        CaseEventType.TAG_REMOVED,
    }
)


class CaseEventsService(BaseWorkspaceService):
    """Service for managing case events."""

    service_name = "case_events"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.agent_session_interactions = CaseAgentSessionInteractionService(
            session=self.session,
            role=self.role,
        )

    async def list_events(self, case: Case) -> Sequence[CaseEvent]:
        """List all events for a case."""
        statement = (
            select(CaseEvent)
            .where(CaseEvent.case_id == case.id)
            .order_by(CaseEvent.created_at.desc(), CaseEvent.surrogate_id.desc())
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def create_event(
        self,
        case: Case,
        event: CaseEventVariant,
        *,
        publish_case_trigger: bool = True,
        sync_durations: bool = True,
        system_generated: bool = False,
    ) -> CaseEvent:
        """Create a non-committing activity record for a case."""
        db_event = CaseEvent(
            workspace_id=self.workspace_id,
            case_id=case.id,
            type=event.type,
            data=event.model_dump(exclude={"type"}, mode="json"),
            user_id=None if system_generated else self.role.user_id,
        )
        self.session.add(db_event)
        await self.session.flush()

        if not system_generated and event.type in _CHILD_MUTATION_EVENTS:
            await self.agent_session_interactions.record_from_context(
                case_id=case.id,
                operation=CaseAgentSessionInteractionOperation.UPDATE,
            )

        event_id = str(db_event.id)
        event_type = (
            db_event.type.value if hasattr(db_event.type, "value") else db_event.type
        )
        created_at = db_event.created_at or datetime.now(UTC)
        case_id = str(case.id)
        workspace_id = str(case.workspace_id)

        if publish_case_trigger:

            async def _publish_case_event() -> None:
                await publish_case_event_payload(
                    event_id=event_id,
                    case_id=case_id,
                    workspace_id=workspace_id,
                    event_type=event_type,
                    created_at=created_at,
                )

            AfterCommitQueue.of(self.session).add(_publish_case_event)

        if sync_durations:
            enqueue_case_duration_sync_after_commit(
                self.session,
                workspace_id=case.workspace_id,
                case_id=case.id,
                event_type=event_type,
                reason="case_event",
                inline_fallback=partial(
                    sync_case_duration,
                    case.workspace_id,
                    case.id,
                    event_types={event_type},
                ),
            )

        return db_event

    async def create_case_viewed_event(
        self,
        case: Case,
        *,
        dedupe_window: timedelta = CASE_VIEW_EVENT_DEDUP_WINDOW,
    ) -> CaseEvent | None:
        """Record a case viewed event if the current user has not viewed recently."""
        if not self.role.user_id:
            return None

        now_utc = datetime.now(UTC)
        statement = (
            select(CaseEvent)
            .where(
                CaseEvent.workspace_id == self.workspace_id,
                CaseEvent.case_id == case.id,
                CaseEvent.type == CaseEventType.CASE_VIEWED,
                CaseEvent.user_id == self.role.user_id,
            )
            .order_by(CaseEvent.created_at.desc())
            .limit(1)
        )
        last_event = (await self.session.execute(statement)).scalars().first()
        if last_event:
            last_created_at = last_event.created_at
            if last_created_at.tzinfo is None:
                if datetime.now() - last_created_at < dedupe_window:
                    return None
            elif now_utc - last_created_at < dedupe_window:
                return None

        return await self.create_event(case=case, event=CaseViewedEvent())
