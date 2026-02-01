"""Workflow import service for atomic Git sync operations."""

from __future__ import annotations

import uuid

from pydantic import ValidationError
from sqlalchemy import select

from tracecat.db.models import Tag, Webhook, Workflow, WorkflowTag
from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullDiagnostic, PullResult
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.folders.service import WorkflowFolderService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig
from tracecat.workflow.case_triggers.service import CaseTriggersService
from tracecat.workflow.schedules.schemas import ScheduleCreate
from tracecat.workflow.schedules.service import WorkflowSchedulesService
from tracecat.workflow.store.schemas import (
    RemoteWebhook,
    RemoteCaseTrigger,
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
        self.folder_service = WorkflowFolderService(
            session=self.session, role=self.role
        )

    async def import_workflows_atomic(
        self,
        remote_workflows: list[RemoteWorkflowDefinition],
        commit_sha: str,
    ) -> PullResult:
        """Import workflows atomically - either all succeed or all fail.

        Existing workflows will be overwritten with new definitions.

        Args:
            remote_workflows: List of remote workflow definitions to import
            commit_sha: The commit SHA these workflows came from

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
        diagnostics = await self._validate_all_workflows(remote_workflows)

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
                    await self._import_single_workflow(remote_workflow)
                # XXX: We need to commit here to ensure that the transaction is committed
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
            logger.error(f"Failed to import workflows: {e}")
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
    ) -> list[PullDiagnostic]:
        """Validate all workflows before import. Returns list of diagnostics."""
        diagnostics: list[PullDiagnostic] = []

        for remote_workflow in remote_workflows:
            workflow_diagnostics = await self._validate_single_workflow(remote_workflow)
            diagnostics.extend(workflow_diagnostics)

        # Cross-workflow integrity: validate alias references in child workflow actions
        cross_diagnostics = await self._validate_cross_workflow_integrity(
            remote_workflows
        )
        diagnostics.extend(cross_diagnostics)

        return diagnostics

    async def _validate_single_workflow(
        self,
        remote_workflow: RemoteWorkflowDefinition,
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
            await self.wf_mgmt.get_workflow(wf_id)

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

    async def _validate_cross_workflow_integrity(
        self, remote_workflows: list[RemoteWorkflowDefinition]
    ) -> list[PullDiagnostic]:
        """Validate that alias-based child workflow calls reference valid workflows.

        Rules
        -----
        - For actions with `action == "core.workflow.execute"`, if `workflow_alias` is
          provided in `args`, it must resolve to an existing workflow in the current
          workspace OR match the alias of a workflow included in this import batch.
        - If the action uses `workflow_id`, skip alias validation.
        """
        diagnostics: list[PullDiagnostic] = []

        # Gather aliases from remote workflows
        remote_aliases: set[str] = {wf.alias for wf in remote_workflows if wf.alias}

        # Cache for database alias resolutions
        resolved_db_aliases: dict[str, bool] = {}

        async def _is_alias_valid(alias: str) -> bool:
            """Check if alias is valid (exists in remote set or database)."""
            if alias in remote_aliases:
                return True
            if alias in resolved_db_aliases:
                return resolved_db_aliases[alias]

            workflow_id = await self.wf_mgmt.resolve_workflow_alias(alias)
            is_valid = workflow_id is not None
            resolved_db_aliases[alias] = is_valid
            return is_valid

        # Validate each workflow's child workflow references
        for remote_workflow in remote_workflows:
            workflow_path = f"workflows/{remote_workflow.id}/definition.yml"

            for action in remote_workflow.definition.actions:
                try:
                    if action.action != PlatformAction.CHILD_WORKFLOW_EXECUTE:
                        continue

                    # Only validate alias-based references
                    alias = (
                        action.args.get("workflow_alias")
                        if isinstance(action.args, dict)
                        else None
                    )
                    if not alias:
                        continue

                    if await _is_alias_valid(alias):
                        continue

                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=workflow_path,
                            workflow_title=remote_workflow.definition.title,
                            error_type="validation",
                            message=(
                                f"Unknown workflow alias {alias!r} referenced by action"
                                f" {action.ref!r} (core.workflow.execute)."
                                " Alias must reference a workflow in this workspace or the import set."
                            ),
                            details={
                                "action": action.action,
                                "action_ref": action.ref,
                                "workflow_alias": alias,
                            },
                        )
                    )
                except Exception as e:
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=workflow_path,
                            workflow_title=remote_workflow.definition.title,
                            error_type="validation",
                            message="Error validating child workflow alias reference",
                            details={"exception": str(e)},
                        )
                    )

        self.logger.debug(f"Cross-workflow integrity diagnostics: {diagnostics}")
        return diagnostics

    async def _import_single_workflow(
        self,
        remote_workflow: RemoteWorkflowDefinition,
    ) -> None:
        """Import a single workflow. Must be called within a transaction.

        Existing workflows will be overwritten with new definitions.
        """
        wf_id = WorkflowUUID.new(remote_workflow.id)
        if existing_workflow := await self.wf_mgmt.get_workflow(wf_id):
            await self._update_existing_workflow(existing_workflow, remote_workflow)
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
            wf_id, remote_workflow.definition, alias=remote_workflow.alias, commit=False
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
        actions = await self.wf_mgmt.create_actions_from_dsl(dsl, existing_workflow.id)
        existing_workflow.actions = actions

        # 5. Update folder if specified
        if remote_workflow.folder_path:
            folder_id = await self._ensure_folder_exists(remote_workflow.folder_path)
            existing_workflow.folder_id = folder_id
        elif remote_workflow.folder_path is None:
            # If folder_path is explicitly None, remove from folder
            existing_workflow.folder_id = None

        # 6. Update related entities
        await self._update_schedules(existing_workflow, remote_workflow.schedules)
        await self._update_webhook(existing_workflow.webhook, remote_workflow.webhook)
        await self._update_case_trigger(
            existing_workflow, remote_workflow.case_trigger
        )
        await self._update_tags(existing_workflow, remote_workflow.tags)

    async def _create_new_workflow(self, remote_defn: RemoteWorkflowDefinition) -> None:
        """Create a new workflow entity with all related entities."""
        wf_id = WorkflowUUID.new(remote_defn.id)
        dsl = remote_defn.definition

        # Ensure folder exists if folder_path is specified
        folder_id = None
        if remote_defn.folder_path:
            folder_id = await self._ensure_folder_exists(remote_defn.folder_path)

        # Create workflow manually to avoid transaction conflicts
        # Similar to _create_db_workflow_from_dsl but without committing
        workflow = await self.wf_mgmt.create_db_workflow_from_dsl(
            dsl, workflow_id=wf_id, commit=False, workflow_alias=remote_defn.alias
        )

        # Set folder if specified
        if folder_id:
            workflow.folder_id = folder_id

        await self.session.flush()

        # Create WorkflowDefinition (versioned)
        defn_service = WorkflowDefinitionsService(session=self.session, role=self.role)
        defn = await defn_service.create_workflow_definition(
            wf_id, dsl, alias=remote_defn.alias, commit=False
        )

        # Update workflow version to match definition
        workflow.version = defn.version

        # Handle additional remote-specific entities
        await self._create_schedules(workflow, remote_defn.schedules)
        await self._update_webhook(workflow.webhook, remote_defn.webhook)
        await self._update_case_trigger(workflow, remote_defn.case_trigger)
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
            await schedule_service.delete_schedule(schedule.id, commit=False)
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

    async def _update_case_trigger(
        self, workflow: Workflow, remote_case_trigger: RemoteCaseTrigger | None
    ) -> None:
        if not remote_case_trigger:
            return
        service = CaseTriggersService(session=self.session, role=self.role)
        config = CaseTriggerConfig.model_validate(remote_case_trigger)
        await service.upsert_case_trigger(
            workflow.id,
            config,
            create_missing_tags=True,
            commit=False,
        )

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
            Tag.workspace_id == self.workspace_id, Tag.name == tag_name
        )
        result = await self.session.execute(stmt)
        tag = result.scalars().first()

        if not tag:
            tag = Tag(
                id=uuid.uuid4(),
                name=tag_name,
                ref=tag_name.lower().replace(" ", "-"),
                color=self._generate_tag_color(),
                workspace_id=self.workspace_id,
            )
            self.session.add(tag)

        return tag

    def _generate_tag_color(self) -> str:
        """Generate a default color for new tags."""
        return "#6B7280"  # Default gray color

    async def _ensure_folder_exists(self, folder_path: str) -> uuid.UUID:
        """Ensure folder path exists, creating any missing folders.

        Args:
            folder_path: Materialized path format, e.g. '/security/detections/'

        Returns:
            UUID of the folder at the specified path
        """
        if not folder_path or folder_path == "/":
            raise ValueError("Invalid folder path")

        # Remove leading/trailing slashes and split into segments
        path_segments = folder_path.strip("/").split("/")
        current_path = "/"

        for segment in path_segments:
            if not segment:  # Skip empty segments
                continue

            parent_path = current_path
            current_path = (
                f"{current_path}{segment}/"
                if current_path == "/"
                else f"{current_path}{segment}/"
            )

            # Check if folder exists at current path
            existing_folder = await self.folder_service.get_folder_by_path(current_path)

            if not existing_folder:
                # Create the folder
                existing_folder = await self.folder_service.create_folder(
                    name=segment, parent_path=parent_path, commit=False
                )

        # Return the final folder's ID
        final_folder = await self.folder_service.get_folder_by_path(folder_path)
        if not final_folder:
            raise ValueError(f"Failed to create or find folder at path: {folder_path}")

        return final_folder.id
