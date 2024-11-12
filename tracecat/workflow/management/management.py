from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Action, Webhook, Workflow
from tracecat.dsl.common import DSLEntrypoint, DSLInput, build_action_statements
from tracecat.dsl.models import DSLConfig
from tracecat.dsl.view import RFGraph
from tracecat.identifiers import WorkflowID
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.validation.service import validate_dsl
from tracecat.workflow.actions.models import ActionControlFlow
from tracecat.workflow.management.models import (
    CreateWorkflowFromDSLResponse,
    CreateWorkflowParams,
    ExternalWorkflowDefinition,
    UpdateWorkflowParams,
)


class WorkflowsManagementService:
    """Manages CRUD operations for Workflows."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.session = session
        self.logger = logger.bind(service="workflows")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[WorkflowsManagementService, None]:
        async with get_async_session_context_manager() as session:
            yield WorkflowsManagementService(session, role=role)

    async def list_workflows(self) -> Sequence[Workflow]:
        """List workflows."""

        statement = select(Workflow).where(Workflow.owner_id == self.role.workspace_id)
        results = await self.session.exec(statement)
        workflows = results.all()
        return workflows

    async def get_workflow(self, workflow_id: WorkflowID) -> Workflow | None:
        statement = select(Workflow).where(
            Workflow.owner_id == self.role.workspace_id, Workflow.id == workflow_id
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def update_wrkflow(
        self, workflow_id: WorkflowID, params: UpdateWorkflowParams
    ) -> Workflow:
        statement = select(Workflow).where(
            Workflow.owner_id == self.role.workspace_id,
            Workflow.id == workflow_id,
        )
        result = await self.session.exec(statement)
        workflow = result.one()
        for key, value in params.model_dump(exclude_unset=True).items():
            # Safe because params has been validated
            setattr(workflow, key, value)
        self.session.add(workflow)
        await self.session.commit()
        await self.session.refresh(workflow)
        return workflow

    async def delete_workflow(self, workflow_id: WorkflowID) -> None:
        statement = select(Workflow).where(
            Workflow.owner_id == self.role.workspace_id,
            Workflow.id == workflow_id,
        )
        result = await self.session.exec(statement)
        workflow = result.one()
        await self.session.delete(workflow)
        await self.session.commit()

    async def create_workflow(self, params: CreateWorkflowParams) -> Workflow:
        """Create a new workflow."""

        now = datetime.now().strftime("%b %d, %Y, %H:%M:%S")
        title = params.title or now
        description = params.description or f"New workflow created {now}"

        workflow = Workflow(
            title=title, description=description, owner_id=self.role.workspace_id
        )
        # When we create a workflow, we automatically create a webhook
        # Add the Workflow to the session first to generate an ID
        self.session.add(workflow)
        await self.session.flush()  # Flush to generate workflow.id
        await self.session.refresh(workflow)

        # Create and associate Webhook with the Workflow
        webhook = Webhook(
            owner_id=self.role.workspace_id,
            workflow_id=workflow.id,
        )
        self.session.add(webhook)
        workflow.webhook = webhook

        graph = RFGraph.with_defaults(workflow)
        workflow.object = graph.model_dump(by_alias=True, mode="json")
        self.session.add(workflow)
        await self.session.commit()
        await self.session.refresh(workflow)
        return workflow

    async def create_workflow_from_dsl(
        self, dsl_data: dict[str, Any], *, skip_secret_validation: bool = False
    ) -> CreateWorkflowFromDSLResponse:
        """Create a new workflow from a Tracecat DSL data object."""

        construction_errors = []
        try:
            # Convert the workflow into a WorkflowDefinition
            # XXX: When we commit from the workflow, we have action IDs
            dsl = DSLInput.model_validate(dsl_data)
            logger.info("Commiting workflow from database")
        except* TracecatValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                RegistryActionValidateResponse.from_dsl_validation_error(e)
                for e in eg.exceptions
            )
        except* ValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                RegistryActionValidateResponse.from_pydantic_validation_error(e)
                for e in eg.exceptions
            )
        if construction_errors:
            return CreateWorkflowFromDSLResponse(errors=construction_errors)

        if not skip_secret_validation:
            if val_errors := await validate_dsl(session=self.session, dsl=dsl):
                logger.warning("Validation errors", errors=val_errors)
                return CreateWorkflowFromDSLResponse(
                    errors=[
                        RegistryActionValidateResponse.from_validation_result(val_res)
                        for val_res in val_errors
                    ]
                )

        logger.warning("Creating workflow from DSL", dsl=dsl)
        try:
            workflow = await self._create_db_workflow_from_dsl(dsl)
            return CreateWorkflowFromDSLResponse(workflow=workflow)
        except Exception as e:
            # Rollback the transaction on error
            logger.error(f"Error creating workflow: {e}")
            await self.session.rollback()
            raise e

    async def build_dsl_from_workflow(self, workflow: Workflow) -> DSLInput:
        """Build a DSLInput from a Workflow."""

        if not workflow.object:
            raise TracecatValidationError(
                "Empty workflow graph object. Is `workflow.object` set?"
            )
        # XXX: Invoking workflow.actions instantiates the actions relationship
        actions = workflow.actions
        # If it still falsy, raise a user facing error
        if not actions:
            raise TracecatValidationError(
                "Workflow has no actions. Please add an action to the workflow before committing."
            )
        graph = RFGraph.from_workflow(workflow)
        if not graph.entrypoints:
            raise TracecatValidationError(
                "Workflow has no entrypoints. Please add an action to the workflow before committing."
            )
        graph_actions = graph.action_nodes()
        if len(graph_actions) != len(actions):
            logger.warning(
                f"Mismatch between graph actions (view) and workflow actions (model): {len(graph_actions)=} != {len(actions)=}"
            )
            logger.debug("Actions", graph_actions=graph_actions, actions=actions)
            # NOTE: This likely occurs due to race conditions in the FE
            # To recover from this, we will use the RFGraph object (view) as the source
            # of truth, and remove any orphaned `Actions` in the database
            await self._synchronize_graph_with_db_actions(actions, graph)
            # Refetch the actions
            await self.session.refresh(workflow)
            # Check again
            actions = workflow.actions
            if not actions:
                raise TracecatValidationError(
                    "Workflow has no actions. Please add an action to the workflow before committing."
                )
            if len(graph_actions) != len(actions):
                raise TracecatValidationError(
                    "Couldn't synchronize actions between graph and database."
                )
        action_statements = build_action_statements(graph, actions)
        return DSLInput(
            title=workflow.title,
            description=workflow.description,
            entrypoint=DSLEntrypoint(expects=workflow.expects),
            actions=action_statements,
            inputs=workflow.static_inputs,
            config=DSLConfig(**workflow.config),
            returns=workflow.returns,
        )

    async def create_workflow_from_external_definition(
        self, import_data: dict[str, Any]
    ) -> Workflow:
        """Import an external workflow definition into the current workspace.

        Optionally validate the workflow definition before importing. (Default: False)
        """

        external_defn = ExternalWorkflowDefinition.model_validate(import_data)
        # NOTE: We do not support adding invalid workflows

        dsl = external_defn.definition
        self.logger.info("Constructed DSL from external definition", dsl=dsl)
        # We need to be able to control:
        # 1. The workspace the workflow is imported into
        # 2. The owner of the workflow
        # 3. The ID of the workflow

        workflow = await self._create_db_workflow_from_dsl(
            dsl,
            workflow_id=external_defn.workflow_id,
            created_at=external_defn.created_at,
            updated_at=external_defn.updated_at,
        )
        return workflow

    async def _create_db_workflow_from_dsl(
        self,
        dsl: DSLInput,
        *,
        workflow_id: WorkflowID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> Workflow:
        """Create a new workflow and associated actions in the database from a DSLInput."""
        logger.info("Creating workflow from DSL", dsl=dsl)
        entrypoint = dsl.entrypoint.model_dump()
        workflow_kwargs = {
            "title": dsl.title,
            "description": dsl.description,
            "owner_id": self.role.workspace_id,
            "static_inputs": dsl.inputs,
            "returns": dsl.returns,
            "config": dsl.config.model_dump(),
            "expects": entrypoint.get("expects"),
        }
        if workflow_id:
            workflow_kwargs["id"] = workflow_id
        if created_at:
            workflow_kwargs["created_at"] = created_at
        if updated_at:
            workflow_kwargs["updated_at"] = updated_at
        workflow = Workflow(**workflow_kwargs)

        # Add the Workflow to the session first to generate an ID
        self.session.add(workflow)

        # Create and associate Webhook with the Workflow
        webhook = Webhook(
            owner_id=self.role.workspace_id,
            workflow_id=workflow.id,
        )
        self.session.add(webhook)
        workflow.webhook = webhook

        # Create and associate Actions with the Workflow
        actions: list[Action] = []
        for act_stmt in dsl.actions:
            control_flow = ActionControlFlow(
                run_if=act_stmt.run_if,
                for_each=act_stmt.for_each,
                retry_policy=act_stmt.retry_policy,
                start_delay=act_stmt.start_delay,
                join_strategy=act_stmt.join_strategy,
            )
            new_action = Action(
                owner_id=self.role.workspace_id,
                workflow_id=workflow.id,
                type=act_stmt.action,
                inputs=act_stmt.args,
                title=act_stmt.title,
                description=act_stmt.description,
                control_flow=control_flow.model_dump(),
            )
            actions.append(new_action)
            self.session.add(new_action)

        workflow.actions = actions  # Associate actions with the workflow

        # Create and set the graph for the Workflow
        base_graph = RFGraph.with_defaults(workflow)
        logger.info("Creating graph for workflow", graph=base_graph)

        # Add DSL contents to the Workflow
        ref2id = {act.ref: act.id for act in actions}
        updated_graph = dsl.to_graph(trigger_node=base_graph.trigger, ref2id=ref2id)
        workflow.object = updated_graph.model_dump(by_alias=True, mode="json")

        # Commit the transaction
        await self.session.commit()
        await self.session.refresh(workflow)
        return workflow

    async def _synchronize_graph_with_db_actions(
        self, actions: list[Action], graph: RFGraph
    ) -> None:
        """Recover actions based on the action nodes."""
        action_nodes = graph.action_nodes()

        # Set difference of action IDs
        ids_in_graph = {node.id for node in action_nodes}
        ids_in_db = {action.id for action in actions}
        # Delete actions that don't exist in the action_nodes
        orphaned_action_ids = ids_in_db - ids_in_graph
        for action in actions:
            if action.id not in orphaned_action_ids:
                continue
            await self.session.delete(action)
        await self.session.commit()
        logger.info(f"Deleted orphaned action: {action.title}")
