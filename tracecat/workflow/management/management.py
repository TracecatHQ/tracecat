from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import validation
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Action, Webhook, Workflow
from tracecat.dsl.common import DSLInput
from tracecat.dsl.graph import RFGraph
from tracecat.logging import logger
from tracecat.types.api import UDFArgsValidationResponse
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.management.models import CreateWorkflowFromDSLResponse


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
    ) -> AsyncGenerator[WorkflowsManagementService, None, None]:
        async with get_async_session_context_manager() as session:
            yield WorkflowsManagementService(session, role=role)

    async def create_workflow(title: str, description: str) -> Workflow:
        """Create a new workflow."""
        workflow = Workflow(
            title=title,
            description=description,
        )
        return workflow

    async def create_workflow_from_dsl(
        self, data: dict[str, Any], *, skip_secret_validation: bool = False
    ) -> CreateWorkflowFromDSLResponse:
        """Create a new workflow from a file."""

        construction_errors = []
        try:
            # Convert the workflow into a WorkflowDefinition
            # XXX: When we commit from the workflow, we have action IDs
            dsl = DSLInput.model_validate(data)
            logger.info("Commiting workflow from database")
        except* TracecatValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_dsl_validation_error(e)
                for e in eg.exceptions
            )
        except* ValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_pydantic_validation_error(e)
                for e in eg.exceptions
            )
        if construction_errors:
            return CreateWorkflowFromDSLResponse(errors=construction_errors)

        if not skip_secret_validation:
            if val_errors := await validation.validate_dsl(dsl):
                logger.warning("Validation errors", errors=val_errors)
                return CreateWorkflowFromDSLResponse(
                    errors=[
                        UDFArgsValidationResponse.from_validation_result(val_res)
                        for val_res in val_errors
                    ]
                )

        logger.warning("Creating workflow from DSL", dsl=dsl)
        try:
            workflow = Workflow(
                title=dsl.title,
                description=dsl.description,
                owner_id=self.role.workspace_id,
                static_inputs=dsl.inputs,
                returns=dsl.returns,
            )

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
                new_action = Action(
                    owner_id=self.role.workspace_id,
                    workflow_id=workflow.id,
                    type=act_stmt.action,
                    inputs=act_stmt.args,
                    title=act_stmt.title,
                    description=act_stmt.description,
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
            workflow.entrypoint = (
                updated_graph.entrypoint.id if updated_graph.entrypoint else None
            )

            # Commit the transaction
            await self.session.commit()
            await self.session.refresh(workflow)

            return CreateWorkflowFromDSLResponse(workflow=workflow)

        except Exception as e:
            # Rollback the transaction on error
            logger.error(f"Error creating workflow: {e}")
            await self.session.rollback()
            raise e
