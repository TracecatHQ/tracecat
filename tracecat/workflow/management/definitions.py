from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.exc import MultipleResultsFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from temporalio import activity

from tracecat import identifiers
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.logger import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatException
from tracecat.workflow.management.models import GetWorkflowDefinitionActivityInputs


class WorkflowDefinitionsService:
    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._session = session
        self.logger = logger.bind(service="workflow_definitions")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role,
    ) -> AsyncGenerator[WorkflowDefinitionsService, None]:
        async with get_async_session_context_manager() as session:
            yield WorkflowDefinitionsService(session, role)

    async def get_definition_by_workflow_id(
        self, workflow_id: identifiers.WorkflowID, *, version: int | None = None
    ) -> WorkflowDefinition | None:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.workspace_id,
            WorkflowDefinition.workflow_id == workflow_id,
        )
        if version:
            statement = statement.where(WorkflowDefinition.version == version)
        else:
            # Get the latest version
            statement = statement.order_by(WorkflowDefinition.version.desc())  # type: ignore

        result = await self._session.exec(statement)
        return result.first()

    async def get_definition_by_workflow_title(
        self, workflow_title: str, *, version: int | None = None
    ) -> WorkflowDefinition | None:
        self.logger.warning(
            "Getting workflow definition by ref",
            workflow_title=workflow_title,
            role=self.role,
        )
        wf_statement = select(Workflow.id).where(
            Workflow.owner_id == self.role.workspace_id,
            Workflow.title == workflow_title,
        )

        try:
            result = await self._session.exec(wf_statement)
            wf_id = result.one_or_none()
        except MultipleResultsFound as e:
            self.logger.error(
                "Multiple workflows found with the same title. Please ensure that the workflow title is unique.",
                workflow_title=workflow_title,
            )
            raise e
        self.logger.warning("Workflow ID", wf_id=wf_id)
        if not wf_id:
            self.logger.error("Workflow name not found", workflow_title=workflow_title)
            return None

        wf_defn_statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.workspace_id,
            WorkflowDefinition.workflow_id == wf_id,
        )

        if version:
            wf_defn_statement = wf_defn_statement.where(
                WorkflowDefinition.version == version
            )
        else:
            # Get the latest version
            wf_defn_statement = wf_defn_statement.order_by(
                WorkflowDefinition.version.desc()  # type: ignore
            )

        result = await self._session.exec(wf_defn_statement)
        return result.first()

    async def list_workflow_defitinions(
        self, workflow_id: identifiers.WorkflowID | None = None
    ) -> list[WorkflowDefinition]:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.workspace_id,
        )
        if workflow_id:
            statement = statement.where(WorkflowDefinition.workflow_id == workflow_id)
        result = await self._session.exec(statement)
        return list(result.all())

    async def create_workflow_definition(
        self,
        workflow_id: identifiers.WorkflowID,
        dsl: DSLInput,
        *,
        commit: bool = True,
    ) -> WorkflowDefinition:
        statement = (
            select(WorkflowDefinition)
            .where(
                WorkflowDefinition.owner_id == self.role.workspace_id,
                WorkflowDefinition.workflow_id == workflow_id,
            )
            .order_by(WorkflowDefinition.version.desc())  # type: ignore
        )
        result = await self._session.exec(statement)
        latest_defn = result.first()

        version = latest_defn.version + 1 if latest_defn else 1
        defn = WorkflowDefinition(
            owner_id=self.role.workspace_id,
            workflow_id=workflow_id,
            content=dsl.model_dump(exclude_unset=True),
            version=version,
        )
        if commit:
            self._session.add(defn)
            await self._session.commit()
            await self._session.refresh(defn)
        return defn


@activity.defn
async def get_workflow_definition_activity(
    input: GetWorkflowDefinitionActivityInputs,
) -> DSLInput:
    async with WorkflowDefinitionsService.with_session(role=input.role) as service:
        defn = await service.get_definition_by_workflow_id(
            input.workflow_id, version=input.version
        )
    if not defn:
        msg = f"Workflow definition not found for {input.workflow_id!r}, version={input.version}"
        logger.error(msg)
        raise TracecatException(msg)
    dsl = DSLInput(**defn.content)
    return dsl
