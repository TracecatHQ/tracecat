from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.models import CaseCreate, CaseEventCreate, CaseUpdate
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Case, CaseEvent
from tracecat.identifiers import CaseID
from tracecat.identifiers.workflow import WorkflowID
from tracecat.logging import logger
from tracecat.types.auth import Role


class CaseManagementService:
    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.session = session
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="case_management")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[CaseManagementService, None]:
        async with get_async_session_context_manager() as session:
            yield CaseManagementService(session, role=role)

    async def create_case(self, params: CaseCreate) -> Case:
        case = Case(
            owner_id=self.role.workspace_id,
            workflow_id=params.workflow_id,
            case_title=params.case_title,
            payload=params.payload,
            malice=params.malice,
            status=params.status,
            priority=params.priority,
            action=params.action,
            context=params.context,
            tags=params.tags,
        )
        self.session.add(case)
        await self.session.commit()
        return case

    async def list_cases(
        self, workflow_id: WorkflowID | None = None, limit: int | None = None
    ) -> Sequence[Case]:
        statement = select(Case).where(Case.owner_id == self.role.workspace_id)

        if workflow_id:
            statement = statement.where(Case.workflow_id == workflow_id)
        if limit:
            statement = statement.limit(limit)

        result = await self.session.exec(statement)
        return result.all()

    async def get_case(self, case_id: CaseID) -> Case:
        statement = select(Case).where(
            Case.owner_id == self.role.workspace_id,
            Case.id == case_id,
        )
        result = await self.session.exec(statement)
        return result.one()

    async def update_case(self, case_id: CaseID, params: CaseUpdate) -> Case:
        case = await self.get_case(case_id)
        for key, value in params.model_dump(exclude_unset=True).items():
            # Safety: params have been validated
            setattr(case, key, value)

        self.session.add(case)
        await self.session.commit()
        await self.session.refresh(case)
        return case


class CaseEventsService:
    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.session = session
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="case_events")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[CaseEventsService, None]:
        async with get_async_session_context_manager() as session:
            yield CaseEventsService(session, role=role)

    async def create_case_event(
        self, case_id: CaseID, params: CaseEventCreate
    ) -> CaseEvent:
        case_event = CaseEvent(
            owner_id=self.role.workspace_id,
            case_id=case_id,
            initiator_role=self.role.type,
            **params.model_dump(),
        )
        self.session.add(case_event)
        await self.session.commit()
        return case_event

    async def list_case_events(self, case_id: CaseID) -> Sequence[CaseEvent]:
        statement = select(CaseEvent).where(
            CaseEvent.owner_id == self.role.workspace_id,
            CaseEvent.case_id == case_id,
        )
        result = await self.session.exec(statement)
        return result.all()

    async def get_case_event(self, case_id: CaseID, event_id: CaseID) -> CaseEvent:
        statement = select(CaseEvent).where(
            CaseEvent.owner_id == self.role.workspace_id,
            CaseEvent.case_id == case_id,  # Defensive
            CaseEvent.id == event_id,
        )
        result = await self.session.exec(statement)
        return result.one()
