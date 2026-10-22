"""Resolve export content without publishing an executable workflow version."""

from pydantic import ValidationError
from sqlalchemy import func, select

from tracecat.db.models import Workflow, Workspace, WorkspaceSyncResourceMapping
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.exceptions import WorkflowExportValidationError
from tracecat.workspace_sync.schemas import WorkflowExportDiagnostic
from tracecat.workspace_sync.types import WorkflowExportCode, WorkflowExportStage


class WorkflowExportDefinitionService(BaseWorkspaceService):
    """Share saved-definition preference across all Git push entry points."""

    service_name = "workflow_export_definitions"

    async def resolve(
        self, workflow: Workflow, *, provider: VcsProvider | None = None
    ) -> DSLInput:
        """Prefer saved content, or build export-only content from current edits."""
        definition = await WorkflowDefinitionsService(
            session=self.session, role=self.role
        ).get_definition_by_workflow_id(WorkflowUUID.new(workflow.id))
        code: WorkflowExportCode = "invalid_definition"
        message = "Check the workflow steps and configuration before pushing."
        try:
            if definition and definition.content:
                return DSLInput.model_validate(definition.content)
            await self.session.refresh(workflow, ["actions"])
            if not workflow.actions:
                code = "no_actions"
                message = "Add an action before pushing this workflow."
            else:
                return await WorkflowsManagementService(
                    session=self.session, role=self.role
                ).build_dsl_from_workflow(workflow)
        except ValidationError as exc:
            # Only report schema field names. Never copy an input or validator
            # message: either can contain credentials from action arguments.
            fields = sorted(
                {
                    str(error["loc"][0])
                    for error in exc.errors(include_input=False)
                    if error["loc"] and error["loc"][0] in DSLInput.model_fields
                }
            )
            if fields:
                message = (
                    f"Check these workflow fields before pushing: {', '.join(fields)}."
                )
        except TracecatValidationError:
            # Validation errors can include raw inputs and credentials. Return
            # corrective copy rather than serializing the original exception.
            pass
        raise WorkflowExportValidationError(
            [
                await self.diagnostic(
                    workflow, code=code, message=message, provider=provider
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
