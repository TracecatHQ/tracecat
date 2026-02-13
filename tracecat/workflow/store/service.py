from pathlib import Path
from typing import cast

from tracecat.authz.controls import require_scope
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatSettingsError, TracecatValidationError
from tracecat.git.utils import parse_git_url
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.sync import Author, PushObject, PushOptions
from tracecat.workflow.store.schemas import (
    RemoteCaseTrigger,
    RemoteWebhook,
    RemoteWorkflowDefinition,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
    Status,
    WorkflowDslPublish,
)
from tracecat.workflow.store.sync import WorkflowSyncService
from tracecat.workspaces.service import WorkspaceService


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
    ) -> None:
        """Take the latest version of the workflow definition and publish it to the store."""
        # Validate that workflow_id matches the provided workflow object
        if workflow.id != workflow_id:
            raise TracecatValidationError(
                f"Workflow ID mismatch: provided {workflow_id} but workflow object has ID {workflow.id}"
            )

        # Get workspace settings for git configuration

        workspace_service = WorkspaceService(session=self.session, role=self.role)
        workspace = await workspace_service.get_workspace(self.workspace_id)

        if not workspace:
            raise TracecatValidationError("Workspace not found")

        # Extract git configuration from workspace settings
        git_repo_url = workspace.settings.get("git_repo_url")
        if not git_repo_url:
            raise TracecatSettingsError(
                "Git repository URL not configured for this workspace. "
                "Please contact your administrator to configure it."
            )

        logger.info(
            "Publishing workflow to store",
            workflow_title=dsl.title,
            repo_url=git_repo_url,
            workspace_id=self.workspace_id,
        )

        # Parse the Git URL using workspace settings
        try:
            git_url = parse_git_url(git_repo_url, allowed_domains={"github.com"})
        except ValueError as e:
            raise TracecatSettingsError(
                f"Invalid Git repository URL configured for this workspace: {e}. "
                "Please contact your administrator to fix the configuration."
            ) from e
        # Note: We could add ref support later if needed via params or workspace settings

        stable_path = get_definition_path(workflow_id)
        webhook = workflow.webhook

        await self.session.refresh(workflow, ["tags", "folder", "case_trigger"])

        # Get folder path if workflow is in a folder
        folder_path = None
        if workflow.folder:
            folder_path = workflow.folder.path

        # Create PushObject with data and stable path
        defn = RemoteWorkflowDefinition(
            id=workflow_id.short(),
            alias=workflow.alias,
            folder_path=folder_path,
            tags=[RemoteWorkflowTag(name=t.name) for t in workflow.tags],
            # Convert Schedule ORM objects to RemoteWorkflowSchedule, handling type conversions and missing fields.
            schedules=[
                RemoteWorkflowSchedule(
                    status=cast(Status, s.status),
                    cron=s.cron,
                    every=s.every,
                    offset=s.offset,
                    start_at=s.start_at,
                    end_at=s.end_at,
                    timeout=s.timeout,
                )
                for s in (workflow.schedules or [])
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
        push_obj = PushObject(data=defn, path=stable_path)

        author = Author(name="Tracecat", email="noreply@tracecat.com")
        push_options = PushOptions(
            message=params.message or f"Publish workflow: {dsl.title}",
            author=author,
            create_pr=True,  # Create PR for review
        )

        # Use WorkflowSyncService to push the workflow with stable path
        sync_service = WorkflowSyncService(session=self.session, role=self.role)
        commit_info = await sync_service.push(
            objects=[push_obj],
            url=git_url,
            options=push_options,
        )

        logger.info(
            "Successfully published workflow",
            workflow_title=dsl.title,
            commit_sha=commit_info.sha,
            ref=commit_info.ref,
        )


def get_definition_path(workflow_id: WorkflowUUID) -> Path:
    """Get the path to the definition file for a workflow."""
    return Path("workflows").joinpath(workflow_id.short(), "definition.yml")
