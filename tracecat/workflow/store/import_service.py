"""Workflow import service for atomic Git sync operations."""

from __future__ import annotations

import uuid

import yaml
from pydantic import ValidationError
from sqlmodel import select

from tracecat.db.schemas import Action, Tag, Webhook, Workflow, WorkflowTag
from tracecat.dsl.common import DSLInput
from tracecat.dsl.view import RFGraph
from tracecat.identifiers.workflow import WorkflowID, WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.sync import ConflictStrategy, PullDiagnostic, PullResult
from tracecat.types.exceptions import TracecatAuthorizationError
from tracecat.workflow.actions.models import ActionControlFlow
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.schedules.models import ScheduleCreate
from tracecat.workflow.schedules.service import WorkflowSchedulesService
from tracecat.workflow.store.models import (
    RemoteWebhook,
    RemoteWorkflowDefinition,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
)


class WorkflowImportService(BaseWorkspaceService):
    """Service for importing workflows from remote definitions with atomic guarantees."""

    service_name = "workflow_import"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wf_mgmt = WorkflowsManagementService(session=self.session, role=self.role)

    async def import_workflows_atomic(
        self,
        remote_workflows: list[RemoteWorkflowDefinition],
        commit_sha: str,
        conflict_strategy: ConflictStrategy = ConflictStrategy.SKIP,
    ) -> PullResult:
        """Import workflows atomically - either all succeed or all fail.

        Args:
            remote_workflows: List of remote workflow definitions to import
            commit_sha: The commit SHA these workflows came from
            conflict_strategy: How to handle conflicts with existing workflows

        Returns:
            PullResult with success status and diagnostics
        """
        if not remote_workflows:
            return PullResult(
                success=True,
                commit_sha=commit_sha,
                workflows_found=0,
                workflows_imported=0,
                diagnostics=[],
                message="No workflows found to import",
            )

        # Phase 1: Validation - check everything before touching database
        diagnostics = await self._validate_all_workflows(
            remote_workflows, conflict_strategy
        )

        if diagnostics:
            return PullResult(
                success=False,
                commit_sha=commit_sha,
                workflows_found=len(remote_workflows),
                workflows_imported=0,
                diagnostics=diagnostics,
                message=f"Import failed: {len(diagnostics)} validation errors found",
            )

        # Phase 2: Atomic import - all operations in single transaction
        try:
            async with self.session.begin_nested():
                for remote_workflow in remote_workflows:
                    await self._import_single_workflow(
                        remote_workflow, conflict_strategy
                    )
                # Show the current state of the database
                await self.session.commit()

            return PullResult(
                success=True,
                commit_sha=commit_sha,
                workflows_found=len(remote_workflows),
                workflows_imported=len(remote_workflows),
                diagnostics=[],
                message=f"Successfully imported {len(remote_workflows)} workflows",
            )

        except Exception as e:
            import traceback

            traceback.print_exc()
            logger.error(f"Failed to import workflows: {e}", exc_info=True)
            return PullResult(
                success=False,
                commit_sha=commit_sha,
                workflows_found=len(remote_workflows),
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path="",
                        workflow_title=None,
                        error_type="transaction",
                        message=f"Transaction failed: {str(e)}",
                        details={"exception": str(e)},
                    )
                ],
                message="Import transaction failed",
            )

    async def _validate_all_workflows(
        self,
        remote_workflows: list[RemoteWorkflowDefinition],
        conflict_strategy: ConflictStrategy,
    ) -> list[PullDiagnostic]:
        """Validate all workflows before import. Returns list of diagnostics."""
        diagnostics: list[PullDiagnostic] = []

        for remote_workflow in remote_workflows:
            workflow_diagnostics = await self._validate_single_workflow(
                remote_workflow, conflict_strategy
            )
            diagnostics.extend(workflow_diagnostics)

        return diagnostics

    async def _validate_single_workflow(
        self,
        remote_workflow: RemoteWorkflowDefinition,
        conflict_strategy: ConflictStrategy,
    ) -> list[PullDiagnostic]:
        """Validate a single workflow. Returns list of diagnostics."""
        diagnostics: list[PullDiagnostic] = []
        workflow_path = f"workflows/{remote_workflow.id}/definition.yml"

        try:
            # Validate DSL structure
            dsl_input = remote_workflow.definition
            if not isinstance(dsl_input, DSLInput):
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=workflow_path,
                        workflow_title=remote_workflow.definition.title,
                        error_type="validation",
                        message="Invalid DSL structure",
                        details={"error": "DSL validation failed"},
                    )
                )
                return diagnostics

            # Check for conflicts
            wf_id = WorkflowUUID.new(remote_workflow.id)
            existing_workflow = await self.wf_mgmt.get_workflow(wf_id)

            if existing_workflow and conflict_strategy == ConflictStrategy.SKIP:
                # This is not an error for SKIP strategy - we'll just skip it
                pass
        except ValidationError as e:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=workflow_path,
                    workflow_title=getattr(remote_workflow.definition, "title", None),
                    error_type="validation",
                    message=f"Validation error: {str(e)}",
                    details={"validation_errors": e.errors()},
                )
            )
        except Exception as e:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=workflow_path,
                    workflow_title=getattr(remote_workflow.definition, "title", None),
                    error_type="validation",
                    message=f"Unexpected validation error: {str(e)}",
                    details={"exception": str(e)},
                )
            )

        return diagnostics

    async def _import_single_workflow(
        self,
        remote_workflow: RemoteWorkflowDefinition,
        conflict_strategy: ConflictStrategy,
    ) -> None:
        """Import a single workflow. Must be called within a transaction."""
        wf_id = WorkflowUUID.new(remote_workflow.id)
        if existing_workflow := await self.wf_mgmt.get_workflow(wf_id):
            if conflict_strategy == ConflictStrategy.SKIP:
                return  # Skip this workflow
            elif conflict_strategy == ConflictStrategy.OVERWRITE:
                await self._update_existing_workflow(existing_workflow, remote_workflow)
            else:
                raise ValueError(f"Conflict strategy {conflict_strategy} not supported")
        else:
            await self._create_new_workflow(remote_workflow)

    async def _update_existing_workflow(
        self, existing_workflow: Workflow, remote_workflow: RemoteWorkflowDefinition
    ) -> None:
        """Update existing workflow with new definition and related entities."""
        # 1. Add new WorkflowDefinition (versioned)
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        wf_id = WorkflowUUID.new(existing_workflow.id)
        defn = await defn_service.create_workflow_definition(
            wf_id, remote_workflow.definition, commit=False
        )

        # 2. Update workflow metadata
        existing_workflow.version = defn.version
        existing_workflow.title = remote_workflow.definition.title
        existing_workflow.description = remote_workflow.definition.description
        existing_workflow.alias = remote_workflow.alias

        # 3. Delete existing actions and recreate from DSL
        # (Actions are tightly coupled to the DSL definition)
        if existing_workflow.actions:
            for action in existing_workflow.actions:
                await self.session.delete(action)
            await self.session.flush()

        # 4. Recreate actions from DSL
        dsl = remote_workflow.definition
        wf_id = WorkflowUUID.new(existing_workflow.id)
        actions = await self._create_actions_from_dsl(dsl, wf_id)
        existing_workflow.actions = actions

        # 5. Regenerate the React Flow graph
        base_graph = RFGraph.with_defaults(existing_workflow)
        ref2id = {act.ref: act.id for act in actions}
        updated_graph = dsl.to_graph(trigger_node=base_graph.trigger, ref2id=ref2id)
        existing_workflow.object = updated_graph.model_dump(by_alias=True, mode="json")

        # 6. Update related entities
        await self._update_schedules(existing_workflow, remote_workflow.schedules)
        await self._update_webhook(existing_workflow.webhook, remote_workflow.webhook)
        await self._update_tags(existing_workflow, remote_workflow.tags)

    async def _create_new_workflow(self, remote_defn: RemoteWorkflowDefinition) -> None:
        """Create a new workflow entity with all related entities."""
        wf_id = WorkflowUUID.new(remote_defn.id)
        dsl = remote_defn.definition

        # Create workflow manually to avoid transaction conflicts
        # Similar to _create_db_workflow_from_dsl but without committing
        workflow = await self.wf_mgmt.create_db_workflow_from_dsl(
            dsl, workflow_id=wf_id, commit=False, workflow_alias=remote_defn.alias
        )
        await self.session.flush()

        # Create WorkflowDefinition (versioned)
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        defn = await defn_service.create_workflow_definition(wf_id, dsl, commit=False)

        # Update workflow version to match definition
        workflow.version = defn.version

        # Handle additional remote-specific entities
        await self._create_schedules(workflow, remote_defn.schedules)
        await self._update_webhook(workflow.webhook, remote_defn.webhook)
        await self._create_tags(workflow, remote_defn.tags)

    async def _update_schedules(
        self, workflow: Workflow, remote_schedules: list[RemoteWorkflowSchedule] | None
    ) -> None:
        """Update workflow schedules - replace existing with new ones using WorkflowSchedulesService."""
        schedule_service = WorkflowSchedulesService(
            session=self.session, role=self.role
        )

        # Delete existing schedules (both DB and Temporal)
        wf_id = WorkflowUUID.new(workflow.id)
        existing_schedules = await schedule_service.list_schedules(wf_id)

        for schedule in existing_schedules:
            await schedule_service.delete_schedule(schedule.id)
        await self.session.flush()

        # Create new schedules
        await self._create_schedules(workflow, remote_schedules)
        await self.session.flush()

    async def _create_schedules(
        self,
        workflow: Workflow,
        remote_schedules: list[RemoteWorkflowSchedule] | None = None,
    ) -> None:
        """Create new schedules for workflow using WorkflowSchedulesService."""
        if not remote_schedules:
            return

        schedule_service = WorkflowSchedulesService(
            session=self.session, role=self.role
        )

        for schedule_data in remote_schedules:
            schedule_create = ScheduleCreate(
                workflow_id=WorkflowUUID.new(workflow.id),
                cron=schedule_data.cron,
                every=schedule_data.every,
                offset=schedule_data.offset,
                start_at=schedule_data.start_at,
                end_at=schedule_data.end_at,
                timeout=schedule_data.timeout or 0,
                status=schedule_data.status,
            )

            # Create schedule using service (handles both DB and Temporal)
            await schedule_service.create_schedule(schedule_create, commit=False)
        await self.session.flush()

    async def _update_webhook(
        self, webhook: Webhook, remote_webhook: RemoteWebhook | None
    ) -> None:
        """Update webhook entity from remote webhook data."""
        if not remote_webhook:
            return
        self.logger.info(f"Updating webhook {webhook.id} from remote {remote_webhook}")

        # The webhook ID doesn't matter
        webhook.methods = remote_webhook.methods
        webhook.status = remote_webhook.status

    async def _update_tags(
        self, workflow: Workflow, remote_tags: list[RemoteWorkflowTag] | None = None
    ) -> None:
        """Update workflow tags - replace existing with new ones."""
        # Delete existing workflow-tag associations
        await self.session.refresh(workflow, ["tags"])
        for workflow_tag in workflow.tags:
            await self.session.delete(workflow_tag)
        await self.session.flush()

        # Create new tag associations
        await self._create_tags(workflow, remote_tags)
        await self.session.flush()

    async def _create_tags(
        self, workflow: Workflow, remote_tags: list[RemoteWorkflowTag] | None = None
    ) -> None:
        """Create new tags and associations for workflow."""
        if not remote_tags or not self.workspace_id:
            return

        for tag_data in remote_tags:
            tag_name = tag_data.name

            # Find or create tag in workspace
            tag = await self._find_or_create_tag(tag_name)

            # Create workflow-tag association
            workflow_tag = WorkflowTag(workflow_id=workflow.id, tag_id=tag.id)
            self.session.add(workflow_tag)

    async def _find_or_create_tag(self, tag_name: str) -> Tag:
        """Find existing tag or create new one in workspace."""
        if not self.workspace_id:
            raise TracecatAuthorizationError("Workspace ID is required")

        stmt = select(Tag).where(
            Tag.owner_id == self.workspace_id, Tag.name == tag_name
        )
        result = await self.session.exec(stmt)
        tag = result.first()

        if not tag:
            tag = Tag(
                id=uuid.uuid4(),
                name=tag_name,
                ref=tag_name.lower().replace(" ", "-"),
                color=self._generate_tag_color(),
                owner_id=self.workspace_id,
            )
            self.session.add(tag)

        return tag

    async def _create_actions_from_dsl(
        self, dsl: DSLInput, workflow_id: WorkflowID
    ) -> list[Action]:
        """Create actions from DSL for a workflow."""
        actions: list[Action] = []
        for act_stmt in dsl.actions:
            control_flow = ActionControlFlow(
                run_if=act_stmt.run_if,
                for_each=act_stmt.for_each,
                retry_policy=act_stmt.retry_policy,
                start_delay=act_stmt.start_delay,
                wait_until=act_stmt.wait_until,
                join_strategy=act_stmt.join_strategy,
            )
            new_action = Action(
                owner_id=self.workspace_id,
                workflow_id=workflow_id,
                type=act_stmt.action,
                inputs=yaml.dump(act_stmt.args),
                title=act_stmt.title,
                description=act_stmt.description,
                control_flow=control_flow.model_dump(),
            )
            actions.append(new_action)
            self.session.add(new_action)
        return actions

    def _generate_tag_color(self) -> str:
        """Generate a default color for new tags."""
        return "#6B7280"  # Default gray color
