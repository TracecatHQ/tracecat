"""Build safe, authorized diagnostics for Git push validation failures."""

from pydantic import ValidationError
from sqlalchemy import func, select

from tracecat.db.models import Workflow, Workspace, WorkspaceSyncResourceMapping
from tracecat.exceptions import TracecatValidationError
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.exceptions import WorkflowExportValidationError
from tracecat.workspace_sync.schemas import WorkflowExportDiagnostic
from tracecat.workspace_sync.types import WorkflowExportCode, WorkflowExportStage


class WorkflowExportDiagnosticsService(BaseWorkspaceService):
    """Identify failed workflows without changing either push surface’s policy."""

    service_name = "workflow_export_diagnostics"

    async def validation_error(
        self,
        workflow: Workflow,
        *,
        code: WorkflowExportCode = "invalid_definition",
        error: ValidationError | TracecatValidationError | None = None,
        stage: WorkflowExportStage = "definition",
        provider: VcsProvider | None = None,
    ) -> WorkflowExportValidationError:
        """Return a safe failure; callers raise it outside exception handlers."""
        messages: dict[WorkflowExportCode, str] = {
            "publish_required": "Publish this workflow before pushing it to Git.",
            "missing_definition": "The published version is missing. Publish the workflow again before pushing.",
            "no_actions": "Add an action before pushing this workflow.",
            "invalid_definition": "Check the workflow steps and configuration before pushing.",
            "missing_child_workflow": "A referenced child workflow could not be found. Check the child workflow reference.",
        }
        message = messages[code]
        if isinstance(error, ValidationError):
            # Validator messages and input values can include credentials.
            # Only expose top-level schema field names from a fixed allowlist.
            fields = sorted(
                {
                    str(item["loc"][0])
                    for item in error.errors(include_input=False)
                    if item["loc"]
                    and item["loc"][0]
                    in {
                        "title",
                        "description",
                        "entrypoint",
                        "actions",
                        "config",
                        "returns",
                        "error_handler",
                    }
                }
            )
            if fields:
                message = (
                    f"Check these workflow fields before pushing: {', '.join(fields)}."
                )
        return WorkflowExportValidationError(
            [
                await self.diagnostic(
                    workflow, code=code, message=message, stage=stage, provider=provider
                )
            ]
        )

    async def diagnostic(
        self,
        workflow: Workflow,
        *,
        code: WorkflowExportCode,
        message: str,
        stage: WorkflowExportStage = "definition",
        provider: VcsProvider | None = None,
    ) -> WorkflowExportDiagnostic:
        """Describe an authorized workflow without inventing repository paths."""
        configured_provider = (
            select(
                func.coalesce(
                    func.nullif(Workspace.settings["git_provider"].astext, ""), "github"
                )
            )
            .where(Workspace.id == self.workspace_id)
            .scalar_subquery()
        )
        mapping = await self.session.scalar(
            select(WorkspaceSyncResourceMapping).where(
                WorkspaceSyncResourceMapping.workspace_id == self.workspace_id,
                WorkspaceSyncResourceMapping.provider
                == (provider.value if provider else configured_provider),
                WorkspaceSyncResourceMapping.resource_type == "workflow",
                WorkspaceSyncResourceMapping.local_id == workflow.id,
            )
        )
        return WorkflowExportDiagnostic(
            workflow_id=workflow.id,
            workflow_title=workflow.title,
            source_id=mapping.source_id if mapping else None,
            source_path=mapping.source_path if mapping else None,
            stage=stage,
            code=code,
            message=message,
        )
