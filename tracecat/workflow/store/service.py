import uuid
from datetime import UTC, datetime
from pathlib import Path

from tracecat.authz.controls import require_scope
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.store.schemas import (
    WorkflowDslPublish,
    WorkflowDslPublishResult,
    validate_short_branch_name,
)
from tracecat.workspace_sync.constants import WORKSPACE_SYNC_SCOPE
from tracecat.workspace_sync.schemas import WorkspaceSyncExportRequest
from tracecat.workspace_sync.service import WorkspaceSyncService


class WorkflowStoreService(BaseWorkspaceService):
    service_name = "workflow_store"

    @require_scope("workflow:update", WORKSPACE_SYNC_SCOPE)
    async def publish_workflow_dsl(
        self,
        *,
        workflow_id: WorkflowUUID,
        dsl: DSLInput,
        params: WorkflowDslPublish,
        workflow: Workflow,
    ) -> WorkflowDslPublishResult:
        """Take the latest version of the workflow definition and publish it to the store."""
        # Validate that workflow_id matches the provided workflow object
        if workflow.id != workflow_id:
            raise TracecatValidationError(
                f"Workflow ID mismatch: provided {workflow_id} but workflow object has ID {workflow.id}"
            )

        logger.info(
            "Publishing workflow to store",
            workflow_title=dsl.title,
            workspace_id=self.workspace_id,
        )

        publish_message = params.message or f"Publish workflow: {dsl.title}"
        validated_branch: str
        validated_pr_base_branch: str | None = None
        create_pr = params.create_pr

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
        else:
            logger.warning(
                "workflow_publish_legacy_mode_used",
                workflow_id=str(workflow_id),
                workspace_id=str(self.workspace_id),
            )
            validated_branch = validate_short_branch_name(
                f"tracecat-sync-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
                field_name="branch",
            )
            create_pr = True

        export_params = WorkspaceSyncExportRequest(
            message=publish_message,
            create_pr=create_pr,
            branch=validated_branch,
            pr_base_branch=validated_pr_base_branch,
            include_schedules=False,
        )

        sync_service = WorkspaceSyncService(session=self.session, role=self.role)
        export_result = await sync_service.export_workflow(
            workflow=workflow,
            dsl=dsl,
            params=export_params,
        )
        result = export_result.as_workflow_publish_result()

        logger.info(
            "Successfully published workflow",
            workflow_title=dsl.title,
            status=result.status,
            commit_sha=result.commit_sha,
            ref=result.branch,
            base_ref=result.base_branch,
            pr_url=result.pr_url,
            pr_number=result.pr_number,
            pr_reused=result.pr_reused,
        )
        return result


def get_definition_path(workflow_id: WorkflowUUID) -> Path:
    """Get the path to the definition file for a workflow."""
    return Path("workflows").joinpath(workflow_id.short(), "definition.yml")
