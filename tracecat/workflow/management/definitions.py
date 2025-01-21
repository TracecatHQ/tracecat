from __future__ import annotations

from sqlmodel import select
from temporalio import activity
from tenacity import retry, stop_after_attempt, wait_fixed

from tracecat import identifiers
from tracecat.db.schemas import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.logger import logger
from tracecat.service import BaseService
from tracecat.types.exceptions import TracecatException
from tracecat.workflow.management.models import GetWorkflowDefinitionActivityInputs


class WorkflowDefinitionsService(BaseService):
    service_name = "workflow_definitions"

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

        result = await self.session.exec(statement)
        return result.first()

    async def list_workflow_defitinions(
        self, workflow_id: identifiers.WorkflowID | None = None
    ) -> list[WorkflowDefinition]:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.owner_id == self.role.workspace_id,
        )
        if workflow_id:
            statement = statement.where(WorkflowDefinition.workflow_id == workflow_id)
        result = await self.session.exec(statement)
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
        result = await self.session.exec(statement)
        latest_defn = result.first()

        version = latest_defn.version + 1 if latest_defn else 1
        defn = WorkflowDefinition(
            owner_id=self.role.workspace_id,
            workflow_id=workflow_id,
            content=dsl.model_dump(exclude_unset=True),
            version=version,
        )
        if commit:
            self.session.add(defn)
            await self.session.commit()
            await self.session.refresh(defn)
        return defn


@activity.defn
@retry(wait=wait_fixed(3), stop=stop_after_attempt(3))
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
