"""Workflow import service for atomic Git sync operations."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlmodel import delete, select

from tracecat.db.schemas import Schedule, Tag, Webhook, Workflow, WorkflowTag
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.sync import ConflictStrategy, PullDiagnostic, PullResult
from tracecat.types.exceptions import TracecatAuthorizationError
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.store.models import RemoteWebhook, RemoteWorkflowDefinition


class WorkflowImportService(BaseWorkspaceService):
    """Service for importing workflows from remote definitions with atomic guarantees."""

    service_name = "workflow_import"

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
            existing_workflow = await self._find_existing_workflow(remote_workflow)

            if existing_workflow and conflict_strategy == ConflictStrategy.SKIP:
                # This is not an error for SKIP strategy - we'll just skip it
                pass
            elif existing_workflow and conflict_strategy == ConflictStrategy.RENAME:
                # Check if we can generate a unique name
                unique_title = await self._generate_unique_title(
                    remote_workflow.definition.title
                )
                if not unique_title:
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=workflow_path,
                            workflow_title=remote_workflow.definition.title,
                            error_type="conflict",
                            message="Cannot generate unique name for workflow",
                            details={"existing_id": str(existing_workflow.id)},
                        )
                    )

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
        existing_workflow = await self._find_existing_workflow(remote_workflow)

        if existing_workflow:
            if conflict_strategy == ConflictStrategy.SKIP:
                return  # Skip this workflow
            elif conflict_strategy == ConflictStrategy.OVERWRITE:
                await self._update_existing_workflow(existing_workflow, remote_workflow)
            elif conflict_strategy == ConflictStrategy.RENAME:
                await self._create_renamed_workflow(remote_workflow)
        else:
            await self._create_new_workflow(remote_workflow)

    async def _find_existing_workflow(
        self, remote_workflow: RemoteWorkflowDefinition
    ) -> Workflow | None:
        """Find existing workflow by alias first, then by title."""
        if not self.workspace_id:
            raise TracecatAuthorizationError("Workspace ID is required")

        # Check by alias if it exists
        if remote_workflow.alias:
            stmt = select(Workflow).where(
                Workflow.owner_id == self.workspace_id,
                Workflow.alias == remote_workflow.alias,
            )
            result = await self.session.exec(stmt)
            workflow = result.first()
            if workflow:
                return workflow

        # Fallback to title matching
        stmt = select(Workflow).where(
            Workflow.owner_id == self.workspace_id,
            Workflow.title == remote_workflow.definition.title,
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def _update_existing_workflow(
        self, existing_workflow: Workflow, remote_workflow: RemoteWorkflowDefinition
    ) -> None:
        """Update existing workflow with new definition and related entities."""
        # 1. Add new WorkflowDefinition (versioned)
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        workflow_id = WorkflowUUID.new(existing_workflow.id)
        await defn_service.create_workflow_definition(
            workflow_id, remote_workflow.definition, commit=False
        )

        # 2. Update workflow metadata
        existing_workflow.title = remote_workflow.definition.title
        existing_workflow.description = remote_workflow.definition.description

        if remote_workflow.alias:
            existing_workflow.alias = remote_workflow.alias

        # 3. Update related entities
        await self._update_schedules(existing_workflow.id, remote_workflow.schedules)
        await self._update_webhook(existing_workflow.id, remote_workflow.webhook)
        await self._update_tags(existing_workflow.id, remote_workflow.tags)

    async def _create_new_workflow(
        self, remote_workflow: RemoteWorkflowDefinition
    ) -> None:
        """Create a new workflow entity with all related entities."""
        if not self.workspace_id:
            raise TracecatAuthorizationError("Workspace ID is required")

        # 1. Create base workflow entity
        workflow_id = uuid4()
        workflow = Workflow(
            id=workflow_id,
            title=remote_workflow.definition.title,
            description=remote_workflow.definition.description,
            alias=remote_workflow.alias,
            status="offline",
            owner_id=self.workspace_id,
        )
        self.session.add(workflow)

        # 2. Create initial workflow definition
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        workflow_id_typed = WorkflowUUID.new(workflow_id)
        await defn_service.create_workflow_definition(
            workflow_id_typed, remote_workflow.definition, commit=False
        )

        # 3. Create webhook (required for all workflows)
        webhook = Webhook(
            owner_id=self.workspace_id, workflow_id=workflow_id, status="offline"
        )
        self.session.add(webhook)

        # 4. Create related entities
        await self._create_schedules(workflow_id, remote_workflow.schedules)
        await self._update_webhook_from_remote(webhook, remote_workflow.webhook)
        await self._create_tags(workflow_id, remote_workflow.tags)

    async def _create_renamed_workflow(
        self, remote_workflow: RemoteWorkflowDefinition
    ) -> None:
        """Create workflow with renamed title to avoid conflicts."""
        unique_title = await self._generate_unique_title(
            remote_workflow.definition.title
        )
        if not unique_title:
            raise ValueError(
                f"Cannot generate unique name for workflow: {remote_workflow.definition.title}"
            )

        # Modify the remote workflow with unique title
        remote_workflow.definition.title = unique_title
        await self._create_new_workflow(remote_workflow)

    async def _generate_unique_title(self, base_title: str) -> str | None:
        """Generate a unique title by appending a number."""
        if not self.workspace_id:
            return None

        for i in range(1, 100):  # Try up to 99 variations
            candidate = f"{base_title} ({i})"
            stmt = select(Workflow).where(
                Workflow.owner_id == self.workspace_id, Workflow.title == candidate
            )
            result = await self.session.execute(stmt)
            if not result.scalar_one_or_none():
                return candidate

        return None  # Could not find unique name

    async def _update_schedules(
        self, workflow_id, remote_schedules: list | None
    ) -> None:
        """Update workflow schedules - replace existing with new ones."""
        # Delete existing schedules
        await self.session.execute(
            delete(Schedule).where(Schedule.workflow_id == workflow_id)
        )

        # Create new schedules
        await self._create_schedules(workflow_id, remote_schedules)

    async def _create_schedules(
        self, workflow_id, remote_schedules: list | None
    ) -> None:
        """Create new schedules for workflow."""
        if not remote_schedules or not self.workspace_id:
            return

        for schedule_data in remote_schedules:
            # Convert RemoteWorkflowSchedule to Schedule entity
            every_td = (
                timedelta(seconds=schedule_data.every)
                if schedule_data.every is not None
                else timedelta(hours=24)
            )
            offset_td = (
                timedelta(seconds=schedule_data.offset)
                if schedule_data.offset is not None
                else None
            )

            schedule = Schedule(
                owner_id=self.workspace_id,
                workflow_id=workflow_id,
                cron=schedule_data.cron,
                status=schedule_data.status,
                every=every_td,
                offset=offset_td,
                start_at=schedule_data.start_at,
                end_at=schedule_data.end_at,
                timeout=schedule_data.timeout,
            )

            self.session.add(schedule)

    async def _update_webhook(
        self, workflow_id: UUID, remote_webhook: RemoteWebhook | None
    ) -> None:
        """Update existing webhook with remote webhook data."""
        if not remote_webhook:
            return

        # Find existing webhook
        stmt = select(Webhook).where(Webhook.workflow_id == workflow_id)
        result = await self.session.execute(stmt)
        webhook = result.scalar_one_or_none()

        if webhook:
            await self._update_webhook_from_remote(webhook, remote_webhook)

    async def _update_webhook_from_remote(
        self, webhook: Webhook, remote_webhook: RemoteWebhook | None
    ) -> None:
        """Update webhook entity from remote webhook data."""
        if not remote_webhook:
            return

        webhook.methods = getattr(remote_webhook, "methods", ["POST"])
        webhook.status = getattr(remote_webhook, "status", "offline")

    async def _update_tags(self, workflow_id, remote_tags: list | None) -> None:
        """Update workflow tags - replace existing with new ones."""
        # Delete existing workflow-tag associations
        await self.session.execute(
            delete(WorkflowTag).where(WorkflowTag.workflow_id == workflow_id)
        )

        # Create new tag associations
        await self._create_tags(workflow_id, remote_tags)

    async def _create_tags(self, workflow_id, remote_tags: list | None) -> None:
        """Create new tags and associations for workflow."""
        if not remote_tags or not self.workspace_id:
            return

        for tag_data in remote_tags:
            tag_name = tag_data.name if hasattr(tag_data, "name") else str(tag_data)

            # Find or create tag in workspace
            tag = await self._find_or_create_tag(tag_name)

            # Create workflow-tag association
            workflow_tag = WorkflowTag(workflow_id=workflow_id, tag_id=tag.id)
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
                id=uuid4(),
                name=tag_name,
                ref=tag_name.lower().replace(" ", "-"),
                color=self._generate_tag_color(),
                owner_id=self.workspace_id,
            )
            self.session.add(tag)

        return tag

    def _generate_tag_color(self) -> str:
        """Generate a default color for new tags."""
        return "#6B7280"  # Default gray color
