from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from tracecat.authz.controls import require_scope
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import Workflow, WorkflowDefinition, WorkflowFolder, Workspace
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatSettingsError, TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.git.utils import parse_git_url
from tracecat.identifiers.workflow import WorkflowIDShort, WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.sync import (
    Author,
    PushObject,
    PushObjectResult,
    PushOptions,
    PushStatus,
)
from tracecat.workflow.store.schemas import (
    RemoteCaseTrigger,
    RemoteWebhook,
    RemoteWorkflowDefinition,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
    Status,
    WorkflowBulkPushExcludedWorkflow,
    WorkflowBulkPushExclusionReason,
    WorkflowBulkPushPreviewRequest,
    WorkflowBulkPushPreviewResponse,
    WorkflowBulkPushRequest,
    WorkflowBulkPushResult,
    WorkflowBulkPushWorkflowResult,
    WorkflowBulkPushWorkflowStatus,
    WorkflowBulkPushWorkflowSummary,
    WorkflowDslPublish,
    WorkflowDslPublishResult,
    validate_short_branch_name,
)
from tracecat.workflow.store.sync import WorkflowSyncService
from tracecat.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class PreparedBulkPushItem:
    workflow: Workflow
    definition: WorkflowDefinition
    remote_definition: RemoteWorkflowDefinition
    path: Path


@dataclass(frozen=True)
class BulkPushSelectionResolution:
    prepared_items: list[PreparedBulkPushItem]
    eligible_workflows: list[WorkflowBulkPushWorkflowSummary]
    excluded_workflows: list[WorkflowBulkPushExcludedWorkflow]
    resolved_workflow_ids: list[WorkflowIDShort]


class WorkflowStoreService(BaseWorkspaceService):
    service_name = "workflow_store"

    @require_scope("workflow:update")
    async def publish_workflow_dsl(
        self,
        *,
        workflow_id: WorkflowUUID,
        dsl: DSLInput,
        params: WorkflowDslPublish,
        workflow: Workflow,
    ) -> WorkflowDslPublishResult:
        """Take the latest version of the workflow definition and publish it to GitHub."""
        if workflow.id != workflow_id:
            raise TracecatValidationError(
                f"Workflow ID mismatch: provided {workflow_id} but workflow object has ID {workflow.id}"
            )

        workspace, git_url = await self._get_workspace_and_git_url()

        logger.info(
            "Publishing workflow to store",
            workflow_title=dsl.title,
            repo_url=workspace.settings.get("git_repo_url"),
            workspace_id=self.workspace_id,
        )

        await self.session.refresh(
            workflow, ["tags", "folder", "case_trigger", "schedules", "webhook"]
        )
        push_obj = PushObject(
            data=self._build_remote_workflow_definition(
                workflow_id=workflow_id, dsl=dsl, workflow=workflow
            ),
            path=get_definition_path(workflow_id),
        )

        author = Author(name="Tracecat", email="noreply@tracecat.com")
        publish_message = params.message or f"Publish workflow: {dsl.title}"
        validated_branch: str | None = None
        validated_pr_base_branch: str | None = None

        if params.branch is not None:
            try:
                validated_branch = validate_short_branch_name(
                    params.branch,
                    field_name="branch",
                )
                if params.pr_base_branch is not None:
                    validated_pr_base_branch = validate_short_branch_name(
                        params.pr_base_branch,
                        field_name="pr_base_branch",
                    )
            except ValueError as e:
                raise TracecatValidationError(str(e)) from e

        if params.branch is None:
            logger.warning(
                "workflow_publish_legacy_mode_used",
                workflow_id=str(workflow_id),
                workspace_id=str(self.workspace_id),
            )
            push_options = PushOptions(
                message=publish_message,
                author=author,
                create_pr=True,
            )
        else:
            push_options = PushOptions(
                message=publish_message,
                author=author,
                create_pr=params.create_pr,
                branch=validated_branch,
                pr_base_branch=validated_pr_base_branch,
            )

        sync_service = WorkflowSyncService(session=self.session, role=self.role)
        commit_info = await sync_service.push(
            objects=[push_obj],
            url=git_url,
            options=push_options,
        )

        if validated_branch is not None and commit_info.status in {
            PushStatus.COMMITTED,
            PushStatus.NO_OP,
        }:
            await self._persist_git_sync_branch([workflow], validated_branch)

        logger.info(
            "Successfully published workflow",
            workflow_title=dsl.title,
            status=commit_info.status.value,
            commit_sha=commit_info.sha,
            ref=commit_info.ref,
            base_ref=commit_info.base_ref,
            pr_url=commit_info.pr_url,
            pr_number=commit_info.pr_number,
            pr_reused=commit_info.pr_reused,
        )

        return WorkflowDslPublishResult(
            status=commit_info.status.value,
            commit_sha=commit_info.sha,
            branch=commit_info.ref,
            base_branch=commit_info.base_ref,
            pr_url=commit_info.pr_url,
            pr_number=commit_info.pr_number,
            pr_reused=commit_info.pr_reused,
            message=commit_info.message,
        )

    @require_scope("workflow:update")
    async def preview_bulk_push(
        self, params: WorkflowBulkPushPreviewRequest
    ) -> WorkflowBulkPushPreviewResponse:
        workspace = await self._get_workspace()
        selection = await self._resolve_bulk_push_selection(
            workflow_ids=self._to_workflow_uuids(params.workflow_ids),
            folder_paths=params.folder_paths,
        )
        branch, commit_message, pr_title, pr_body = self._build_bulk_push_defaults(
            workspace_name=workspace.name,
            eligible_workflows=selection.eligible_workflows,
        )
        return WorkflowBulkPushPreviewResponse(
            eligible_workflows=selection.eligible_workflows,
            excluded_workflows=selection.excluded_workflows,
            resolved_workflow_ids=selection.resolved_workflow_ids,
            branch=branch,
            commit_message=commit_message,
            pr_title=pr_title,
            pr_body=pr_body,
            can_submit=bool(selection.prepared_items),
        )

    @require_scope("workflow:update")
    async def bulk_push(
        self, params: WorkflowBulkPushRequest
    ) -> WorkflowBulkPushResult:
        _, git_url = await self._get_workspace_and_git_url()
        selection = await self._resolve_bulk_push_selection(
            workflow_ids=self._to_workflow_uuids(params.workflow_ids),
            folder_paths=[],
        )
        if not selection.prepared_items:
            raise TracecatValidationError(
                "No published workflows are available to push to GitHub."
            )

        author = Author(name="Tracecat", email="noreply@tracecat.com")
        push_objects = [
            PushObject(data=item.remote_definition, path=item.path)
            for item in selection.prepared_items
        ]
        push_options = PushOptions(
            message=params.commit_message,
            author=author,
            create_pr=True,
            branch=params.branch,
            pr_title=params.pr_title,
            pr_body=params.pr_body,
        )
        sync_service = WorkflowSyncService(session=self.session, role=self.role)
        commit_info = await sync_service.push(
            objects=push_objects,
            url=git_url,
            options=push_options,
        )

        if commit_info.status in {PushStatus.COMMITTED, PushStatus.NO_OP}:
            await self._persist_git_sync_branch(
                [item.workflow for item in selection.prepared_items],
                params.branch,
            )

        object_results = self._build_bulk_push_workflow_results(
            selection=selection,
            object_results=commit_info.object_results,
            default_status=commit_info.status,
        )
        message = commit_info.message
        if selection.excluded_workflows:
            message = (
                f"{message} Excluded {len(selection.excluded_workflows)} workflow(s)."
            )

        return WorkflowBulkPushResult(
            status=commit_info.status.value,
            commit_sha=commit_info.sha,
            branch=commit_info.ref,
            base_branch=commit_info.base_ref,
            pr_url=commit_info.pr_url,
            pr_number=commit_info.pr_number,
            pr_reused=commit_info.pr_reused,
            message=message,
            selected_count=len(params.workflow_ids),
            eligible_count=len(selection.prepared_items),
            excluded_count=len(selection.excluded_workflows),
            workflow_results=object_results,
        )

    async def _get_workspace(self) -> Workspace:
        workspace_service = WorkspaceService(session=self.session, role=self.role)
        workspace = await workspace_service.get_workspace(self.workspace_id)
        if not workspace:
            raise TracecatValidationError("Workspace not found")
        return workspace

    async def _get_workspace_and_git_url(self) -> tuple[Workspace, GitUrl]:
        workspace = await self._get_workspace()
        git_repo_url = workspace.settings.get("git_repo_url")
        if not git_repo_url:
            raise TracecatSettingsError(
                "Git repository URL not configured for this workspace. "
                "Please contact your administrator to configure it."
            )
        try:
            git_url = parse_git_url(git_repo_url, allowed_domains={"github.com"})
        except ValueError as e:
            raise TracecatSettingsError(
                f"Invalid Git repository URL configured for this workspace: {e}. "
                "Please contact your administrator to fix the configuration."
            ) from e
        return workspace, git_url

    def _build_remote_workflow_definition(
        self,
        *,
        workflow_id: WorkflowUUID,
        dsl: DSLInput,
        workflow: Workflow,
    ) -> RemoteWorkflowDefinition:
        webhook = workflow.webhook
        if webhook is None:
            raise TracecatValidationError(
                f"Workflow {workflow_id.short()} is missing a webhook configuration."
            )

        folder_path = workflow.folder.path if workflow.folder else None
        return RemoteWorkflowDefinition(
            id=workflow_id.short(),
            alias=workflow.alias,
            folder_path=folder_path,
            tags=[RemoteWorkflowTag(name=tag.name) for tag in workflow.tags],
            schedules=[
                RemoteWorkflowSchedule(
                    status=cast(Status, schedule.status),
                    cron=schedule.cron,
                    every=schedule.every,
                    offset=schedule.offset,
                    start_at=schedule.start_at,
                    end_at=schedule.end_at,
                    timeout=schedule.timeout,
                )
                for schedule in workflow.schedules or []
            ],
            webhook=RemoteWebhook(
                methods=webhook.methods,
                status=cast(Status, webhook.status),
            ),
            case_trigger=RemoteCaseTrigger(
                status=cast(Status, workflow.case_trigger.status)
                if workflow.case_trigger
                else "offline",
                event_types=[
                    CaseEventType(event_type)
                    for event_type in workflow.case_trigger.event_types
                ]
                if workflow.case_trigger
                else [],
                tag_filters=workflow.case_trigger.tag_filters
                if workflow.case_trigger
                else [],
            )
            if workflow.case_trigger
            else None,
            definition=dsl,
        )

    async def _resolve_bulk_push_selection(
        self,
        *,
        workflow_ids: Sequence[WorkflowUUID],
        folder_paths: Sequence[str],
    ) -> BulkPushSelectionResolution:
        folder_workflow_ids = await self._list_workflow_ids_in_selected_folders(
            folder_paths
        )
        ordered_workflow_ids = self._dedupe_workflow_ids(
            list(workflow_ids) + folder_workflow_ids
        )
        workflows_by_short = await self._get_workflows_by_short_id(ordered_workflow_ids)
        definitions_by_short = await self._get_latest_definitions_by_short_id(
            ordered_workflow_ids
        )

        prepared_items: list[PreparedBulkPushItem] = []
        eligible_workflows: list[WorkflowBulkPushWorkflowSummary] = []
        excluded_workflows: list[WorkflowBulkPushExcludedWorkflow] = []
        resolved_workflow_ids = [
            workflow_id.short() for workflow_id in ordered_workflow_ids
        ]

        for workflow_id in ordered_workflow_ids:
            workflow_short_id = workflow_id.short()
            workflow = workflows_by_short.get(workflow_short_id)
            if workflow is None:
                excluded_workflows.append(
                    WorkflowBulkPushExcludedWorkflow(
                        workflow_id=workflow_short_id,
                        reason=WorkflowBulkPushExclusionReason.NOT_FOUND,
                        message="Workflow not found in this workspace.",
                    )
                )
                continue

            definition = definitions_by_short.get(workflow_short_id)
            if definition is None:
                excluded_workflows.append(
                    WorkflowBulkPushExcludedWorkflow(
                        workflow_id=workflow_short_id,
                        title=workflow.title,
                        reason=WorkflowBulkPushExclusionReason.NOT_PUBLISHED,
                        message="Workflow has no published definition to push.",
                    )
                )
                continue

            dsl = DSLInput.model_validate(definition.content)
            try:
                remote_definition = self._build_remote_workflow_definition(
                    workflow_id=workflow_id,
                    dsl=dsl,
                    workflow=workflow,
                )
            except TracecatValidationError as e:
                excluded_workflows.append(
                    WorkflowBulkPushExcludedWorkflow(
                        workflow_id=workflow_short_id,
                        title=workflow.title,
                        reason=WorkflowBulkPushExclusionReason.INVALID_CONFIGURATION,
                        message=str(e),
                    )
                )
                continue

            prepared_items.append(
                PreparedBulkPushItem(
                    workflow=workflow,
                    definition=definition,
                    remote_definition=remote_definition,
                    path=get_definition_path(workflow_id),
                )
            )
            eligible_workflows.append(
                WorkflowBulkPushWorkflowSummary(
                    workflow_id=workflow_short_id,
                    title=workflow.title,
                    alias=workflow.alias,
                    folder_path=workflow.folder.path if workflow.folder else None,
                    latest_definition_version=definition.version,
                    latest_definition_created_at=definition.created_at,
                )
            )

        return BulkPushSelectionResolution(
            prepared_items=prepared_items,
            eligible_workflows=eligible_workflows,
            excluded_workflows=excluded_workflows,
            resolved_workflow_ids=resolved_workflow_ids,
        )

    async def _list_workflow_ids_in_selected_folders(
        self, folder_paths: Sequence[str]
    ) -> list[WorkflowUUID]:
        normalized_paths = self._normalize_folder_paths(folder_paths)
        if not normalized_paths:
            return []
        if "/" in normalized_paths:
            statement = select(Workflow.id).where(
                Workflow.workspace_id == self.workspace_id
            )
        else:
            path_filters = [
                WorkflowFolder.path.startswith(path, autoescape=True)
                for path in normalized_paths
            ]
            statement = (
                select(Workflow.id)
                .join(WorkflowFolder, Workflow.folder_id == WorkflowFolder.id)
                .where(
                    Workflow.workspace_id == self.workspace_id,
                    or_(*path_filters),
                )
            )
        statement = statement.order_by(Workflow.created_at.desc(), Workflow.id.desc())
        result = await self.session.execute(statement)
        return [WorkflowUUID.new(workflow_id) for workflow_id in result.scalars().all()]

    async def _get_workflows_by_short_id(
        self, workflow_ids: Sequence[WorkflowUUID]
    ) -> dict[WorkflowIDShort, Workflow]:
        if not workflow_ids:
            return {}
        statement = (
            select(Workflow)
            .where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id.in_(workflow_ids),
            )
            .options(
                selectinload(Workflow.tags),
                selectinload(Workflow.folder),
                selectinload(Workflow.schedules),
                selectinload(Workflow.webhook),
                selectinload(Workflow.case_trigger),
            )
        )
        result = await self.session.execute(statement)
        workflows = result.scalars().all()
        return {
            WorkflowUUID.new(workflow.id).short(): workflow for workflow in workflows
        }

    async def _get_latest_definitions_by_short_id(
        self, workflow_ids: Sequence[WorkflowUUID]
    ) -> dict[WorkflowIDShort, WorkflowDefinition]:
        if not workflow_ids:
            return {}
        latest_versions = (
            select(
                WorkflowDefinition.workflow_id,
                func.max(WorkflowDefinition.version).label("latest_version"),
            )
            .where(
                WorkflowDefinition.workspace_id == self.workspace_id,
                WorkflowDefinition.workflow_id.in_(workflow_ids),
            )
            .group_by(WorkflowDefinition.workflow_id)
            .subquery()
        )
        statement = (
            select(WorkflowDefinition)
            .join(
                latest_versions,
                and_(
                    WorkflowDefinition.workflow_id == latest_versions.c.workflow_id,
                    WorkflowDefinition.version == latest_versions.c.latest_version,
                ),
            )
            .where(WorkflowDefinition.workspace_id == self.workspace_id)
        )
        result = await self.session.execute(statement)
        definitions = result.scalars().all()
        return {
            WorkflowUUID.new(definition.workflow_id).short(): definition
            for definition in definitions
            if definition.workflow_id is not None
        }

    def _build_bulk_push_defaults(
        self,
        *,
        workspace_name: str,
        eligible_workflows: Sequence[WorkflowBulkPushWorkflowSummary],
    ) -> tuple[str, str, str, str]:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        branch = f"tracecat/bulk-push-{timestamp}"
        workflow_count = len(eligible_workflows)
        if workflow_count == 1:
            workflow_label = eligible_workflows[0].title
        else:
            workflow_label = f"{workflow_count} workflows"

        commit_message = f"Push {workflow_label} to GitHub"
        pr_title = f"Push {workflow_label} from {workspace_name}"

        workflow_lines = [
            f"- {workflow.title} (`{workflow.workflow_id}`)"
            for workflow in eligible_workflows
        ]
        pr_body = "\n".join(
            [
                "Automated bulk push from Tracecat.",
                "",
                f"**Workspace:** {workspace_name}",
                "",
                "**Workflows:**",
                *workflow_lines,
            ]
        )
        return branch, commit_message, pr_title, pr_body

    async def _persist_git_sync_branch(
        self, workflows: Sequence[Workflow], branch: str
    ) -> None:
        for workflow in workflows:
            workflow.git_sync_branch = branch
            self.session.add(workflow)
        await self.session.commit()

    def _build_bulk_push_workflow_results(
        self,
        *,
        selection: BulkPushSelectionResolution,
        object_results: Sequence[PushObjectResult] | None,
        default_status: PushStatus,
    ) -> list[WorkflowBulkPushWorkflowResult]:
        object_status_by_path = (
            {result.path: result.status for result in object_results}
            if object_results
            else {}
        )
        workflow_results = [
            WorkflowBulkPushWorkflowResult(
                workflow_id=item.remote_definition.id,
                title=item.workflow.title,
                path=str(item.path),
                status=WorkflowBulkPushWorkflowStatus(
                    object_status_by_path.get(str(item.path), default_status).value
                ),
            )
            for item in selection.prepared_items
        ]
        for excluded in selection.excluded_workflows:
            if excluded.workflow_id is None:
                continue
            workflow_results.append(
                WorkflowBulkPushWorkflowResult(
                    workflow_id=excluded.workflow_id,
                    title=excluded.title or excluded.workflow_id,
                    path=str(
                        get_definition_path(WorkflowUUID.new(excluded.workflow_id))
                    ),
                    status=WorkflowBulkPushWorkflowStatus.EXCLUDED,
                    message=excluded.message,
                )
            )
        return workflow_results

    def _dedupe_workflow_ids(
        self, workflow_ids: Sequence[WorkflowUUID]
    ) -> list[WorkflowUUID]:
        unique_ids: dict[WorkflowIDShort, WorkflowUUID] = {}
        for workflow_id in workflow_ids:
            unique_ids.setdefault(workflow_id.short(), workflow_id)
        return list(unique_ids.values())

    def _normalize_folder_paths(self, folder_paths: Sequence[str]) -> list[str]:
        normalized_paths: list[str] = []
        for path in folder_paths:
            stripped = path.strip()
            if not stripped:
                continue
            if stripped == "/":
                normalized = "/"
            else:
                normalized = stripped if stripped.startswith("/") else f"/{stripped}"
                if not normalized.endswith("/"):
                    normalized = f"{normalized}/"
            if normalized not in normalized_paths:
                normalized_paths.append(normalized)
        return normalized_paths

    def _to_workflow_uuids(
        self, workflow_ids: Sequence[WorkflowIDShort]
    ) -> list[WorkflowUUID]:
        return [WorkflowUUID.new(workflow_id) for workflow_id in workflow_ids]


def get_definition_path(workflow_id: WorkflowUUID) -> Path:
    """Get the path to the definition file for a workflow."""
    return Path("workflows").joinpath(workflow_id.short(), "definition.yml")
