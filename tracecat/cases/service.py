from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.models import CaseCreate
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Case
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
    ) -> list[Case]:
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
