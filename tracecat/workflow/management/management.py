from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import yaml
from pydantic import ValidationError
from sqlmodel import and_, col, select
from temporalio import activity

from tracecat.db.schemas import Action, Tag, Webhook, Workflow, WorkflowTag
from tracecat.dsl.common import DSLEntrypoint, DSLInput, build_action_statements
from tracecat.dsl.models import DSLConfig
from tracecat.dsl.view import RFGraph
from tracecat.identifiers import WorkflowID
from tracecat.identifiers.workflow import (
    LEGACY_WF_ID_PATTERN,
    WF_ID_SHORT_PATTERN,
    WorkflowUUID,
)
from tracecat.registry.actions.models import RegistryActionValidateResponse
from tracecat.service import BaseService
from tracecat.types.exceptions import (
    TracecatAuthorizationError,
    TracecatValidationError,
)
from tracecat.validation.service import validate_dsl
from tracecat.workflow.actions.models import ActionControlFlow
from tracecat.workflow.management.models import (
    ExternalWorkflowDefinition,
    GetErrorHandlerWorkflowIDActivityInputs,
    ResolveWorkflowAliasActivityInputs,
    WorkflowCreate,
    WorkflowDSLCreateResponse,
    WorkflowUpdate,
)


class WorkflowsManagementService(BaseService):
    """Manages CRUD operations for Workflows."""

    service_name = "workflows"

    async def list_workflows(
        self, *, tags: list[str] | None = None
    ) -> Sequence[Workflow]:
        """List workflows.

        Args:
            tags: Optional list of tag names to filter workflows by

        Returns:
            Sequence[Workflow]: List of workflows matching the filters
        """
        stmt = select(Workflow).where(Workflow.owner_id == self.role.workspace_id)

        if tags:
            tag_set = set(tags)
            # Join through the WorkflowTag link table to Tag table
            stmt = (
                stmt.join(WorkflowTag, Workflow.id == WorkflowTag.workflow_id)  # type: ignore
                .join(
                    Tag, and_(Tag.id == WorkflowTag.tag_id, col(Tag.name).in_(tag_set))
                )
                # Ensure we get distinct workflows when multiple tags match
                .distinct()
            )

        results = await self.session.exec(stmt)
        workflows = results.all()
        return workflows

    async def get_workflow(self, workflow_id: WorkflowID) -> Workflow | None:
        statement = select(Workflow).where(
            Workflow.owner_id == self.role.workspace_id,
            Workflow.id == workflow_id,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def resolve_workflow_alias(self, alias: str) -> WorkflowID | None:
        statement = select(Workflow.id).where(
            Workflow.owner_id == self.role.workspace_id,
            Workflow.alias == alias,
        )
        result = await self.session.exec(statement)
        res = result.one_or_none()
        return WorkflowUUID.new(res) if res else None

    async def update_workflow(
        self, workflow_id: WorkflowID, params: WorkflowUpdate
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

    async def create_workflow(self, params: WorkflowCreate) -> Workflow:
        """Create a new workflow."""

        if self.role.workspace_id is None:
            raise TracecatAuthorizationError("Workspace ID is required")

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
    ) -> WorkflowDSLCreateResponse:
        """Create a new workflow from a Tracecat DSL data object."""

        construction_errors = []
        try:
            # Convert the workflow into a WorkflowDefinition
            # XXX: When we commit from the workflow, we have action IDs
            dsl = DSLInput.model_validate(dsl_data)
            self.logger.info("Creating workflow from database")
        except* TracecatValidationError as eg:
            self.logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                RegistryActionValidateResponse.from_dsl_validation_error(e)  # type: ignore
                for e in eg.exceptions
            )
        except* ValidationError as eg:
            self.logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                RegistryActionValidateResponse.from_pydantic_validation_error(e)  # type: ignore
                for e in eg.exceptions
            )
        if construction_errors:
            return WorkflowDSLCreateResponse(errors=construction_errors)

        if not skip_secret_validation:
            if val_errors := await validate_dsl(session=self.session, dsl=dsl):
                self.logger.warning("Validation errors", errors=val_errors)
                return WorkflowDSLCreateResponse(
                    errors=[
                        RegistryActionValidateResponse.from_validation_result(val_res)
                        for val_res in val_errors
                    ]
                )

        self.logger.warning("Creating workflow from DSL", dsl=dsl)
        try:
            workflow = await self._create_db_workflow_from_dsl(dsl)
            return WorkflowDSLCreateResponse(workflow=workflow)
        except Exception as e:
            # Rollback the transaction on error
            self.logger.error(f"Error creating workflow: {e}")
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
                "Workflow has no actions. Please add an action to the workflow before saving."
            )
        graph = RFGraph.from_workflow(workflow)
        if not graph.entrypoints:
            raise TracecatValidationError(
                "Workflow has no entrypoints. Please add an action to the workflow before saving."
            )
        graph_actions = graph.action_nodes()
        if len(graph_actions) != len(actions):
            self.logger.warning(
                f"Mismatch between graph actions (view) and workflow actions (model): {len(graph_actions)=} != {len(actions)=}"
            )
            self.logger.debug("Actions", graph_actions=graph_actions, actions=actions)
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
                    "Workflow has no actions. Please add an action to the workflow before saving."
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
            error_handler=workflow.error_handler,
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
        self.logger.info("Creating workflow from DSL", dsl=dsl)
        if self.role.workspace_id is None:
            raise TracecatAuthorizationError("Workspace ID is required")
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
                inputs=yaml.dump(act_stmt.args),
                title=act_stmt.title,
                description=act_stmt.description,
                control_flow=control_flow.model_dump(),
            )
            actions.append(new_action)
            self.session.add(new_action)

        workflow.actions = actions  # Associate actions with the workflow

        # Create and set the graph for the Workflow
        base_graph = RFGraph.with_defaults(workflow)
        self.logger.info("Creating graph for workflow", graph=base_graph)

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
        self.logger.info(f"Deleted orphaned action: {action.title}")

    @staticmethod
    @activity.defn
    async def resolve_workflow_alias_activity(
        input: ResolveWorkflowAliasActivityInputs,
    ) -> WorkflowID | None:
        async with WorkflowsManagementService.with_session(input.role) as service:
            return await service.resolve_workflow_alias(input.workflow_alias)

    @staticmethod
    @activity.defn
    async def get_error_handler_workflow_id(
        input: GetErrorHandlerWorkflowIDActivityInputs,
    ) -> WorkflowID | None:
        args = input.args
        id_or_alias = None
        async with WorkflowsManagementService.with_session(role=args.role) as service:
            if args.dsl:
                # 1. If a DSL was provided, we must use its error handler
                if not args.dsl.error_handler:
                    activity.logger.info("DSL has no error handler")
                    return None
                id_or_alias = args.dsl.error_handler
            else:
                # 2. Otherwise, use the error handler defined in the workflow
                workflow = await service.get_workflow(args.wf_id)
                if not workflow or not workflow.error_handler:
                    activity.logger.info("No workflow or error handler found")
                    return None
                id_or_alias = workflow.error_handler

            # 3. Convert the error handler to an ID
            if re.match(LEGACY_WF_ID_PATTERN, id_or_alias):
                # TODO: Legacy workflow ID for backwards compatibility. Slowly deprecate.
                handler_wf_id = WorkflowUUID.from_legacy(id_or_alias)
            elif re.match(WF_ID_SHORT_PATTERN, id_or_alias):
                # Short workflow ID
                handler_wf_id = WorkflowUUID.new(id_or_alias)
            if re.match(LEGACY_WF_ID_PATTERN, id_or_alias):
                # TODO: Legacy workflow ID for backwards compatibility. Slowly deprecate.
                handler_wf_id = WorkflowUUID.from_legacy(id_or_alias)
            elif re.match(WF_ID_SHORT_PATTERN, id_or_alias):
                # Short workflow ID
                handler_wf_id = WorkflowUUID.new(id_or_alias)
            else:
                handler_wf_id = await service.resolve_workflow_alias(id_or_alias)
                if not handler_wf_id:
                    raise RuntimeError(
                        f"Couldn't find matching workflow for alias {id_or_alias!r}"
                    )
            return handler_wf_id
