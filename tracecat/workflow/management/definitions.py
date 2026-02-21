from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.authz.controls import require_scope
from tracecat.db.models import Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import EntitlementRequired, TracecatException
from tracecat.identifiers.workflow import WorkflowID
from tracecat.logger import logger
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.management.schemas import (
    GetWorkflowDefinitionActivityInputs,
    ResolveRegistryLockActivityInputs,
    WorkflowDefinitionActivityResult,
)


class WorkflowDefinitionsService(BaseWorkspaceService):
    service_name = "workflow_definitions"

    async def get_definition_by_workflow_id(
        self, workflow_id: WorkflowID, *, version: int | None = None
    ) -> WorkflowDefinition | None:
        statement = (
            select(WorkflowDefinition)
            .where(
                WorkflowDefinition.workspace_id == self.workspace_id,
                WorkflowDefinition.workflow_id == workflow_id,
            )
            .options(
                selectinload(WorkflowDefinition.workflow).selectinload(
                    Workflow.case_trigger
                )
            )
        )
        if version:
            statement = statement.where(WorkflowDefinition.version == version)
        else:
            # Get the latest version
            statement = statement.order_by(WorkflowDefinition.version.desc())

        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_workflow_defitinions(
        self, workflow_id: WorkflowID | None = None
    ) -> list[WorkflowDefinition]:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.workspace_id == self.workspace_id,
        )
        if workflow_id:
            statement = statement.where(WorkflowDefinition.workflow_id == workflow_id)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    @require_scope("workflow:update")
    async def create_workflow_definition(
        self,
        workflow_id: WorkflowID,
        dsl: DSLInput,
        *,
        alias: str | None = None,
        registry_lock: RegistryLock | None = None,
        commit: bool = True,
    ) -> WorkflowDefinition:
        """Create a new workflow definition.

        Args:
            workflow_id: The ID of the workflow this definition belongs to.
            dsl: The DSL input for the workflow definition.
            registry_lock: Optional registry version lock to freeze with this definition.
                Maps repository origin to version string.
            commit: Whether to commit the transaction.

        Returns:
            The created WorkflowDefinition.
        """
        statement = (
            select(WorkflowDefinition)
            .where(
                WorkflowDefinition.workspace_id == self.workspace_id,
                WorkflowDefinition.workflow_id == workflow_id,
            )
            .order_by(WorkflowDefinition.version.desc())
        )
        result = await self.session.execute(statement)
        latest_defn = result.scalars().first()

        version = latest_defn.version + 1 if latest_defn else 1
        defn = WorkflowDefinition(
            workspace_id=self.workspace_id,
            workflow_id=workflow_id,
            content=dsl.model_dump(exclude_unset=True),
            version=version,
            alias=alias,
            registry_lock=registry_lock.model_dump() if registry_lock else None,
        )
        self.session.add(defn)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        await self.session.refresh(defn)
        return defn


@activity.defn
async def get_workflow_definition_activity(
    input: GetWorkflowDefinitionActivityInputs,
) -> WorkflowDefinitionActivityResult:
    async with WorkflowDefinitionsService.with_session(role=input.role) as service:
        defn = await service.get_definition_by_workflow_id(
            input.workflow_id, version=input.version
        )
        if not defn:
            msg = f"Workflow definition not found for {input.workflow_id.short()}, version={input.version}"
            logger.error(msg)
            raise TracecatException(msg)
        dsl = DSLInput(**defn.content)
    # Convert from DB dict type to RegistryLock (JSONB deserializes to dict)
    registry_lock = (
        RegistryLock.model_validate(defn.registry_lock) if defn.registry_lock else None
    )
    return WorkflowDefinitionActivityResult(dsl=dsl, registry_lock=registry_lock)


@activity.defn
async def resolve_registry_lock_activity(
    input: ResolveRegistryLockActivityInputs,
) -> RegistryLock:
    """Resolve registry lock with action bindings for a set of actions.

    This activity is called at workflow start if no lock is provided,
    ensuring all trigger paths have a valid registry lock.
    """
    try:
        async with RegistryLockService.with_session(role=input.role) as service:
            lock = await service.resolve_lock_with_bindings(input.action_names)
    except EntitlementRequired as e:
        raise ApplicationError(
            str(e),
            e.detail,
            non_retryable=True,
            type=e.__class__.__name__,
        ) from e
    logger.info(
        "Resolved registry lock",
        num_origins=len(lock.origins),
        num_actions=len(lock.actions),
    )
    return lock
