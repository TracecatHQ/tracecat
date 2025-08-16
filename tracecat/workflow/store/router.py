from fastapi import APIRouter, HTTPException, status

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.types.exceptions import (
    TracecatCredentialsNotFoundError,
    TracecatSettingsError,
)
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.store.models import WorkflowDslPublish
from tracecat.workflow.store.service import WorkflowStoreService

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/{workflow_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def publish_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WorkflowDslPublish,
):
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    defn_svc = WorkflowDefinitionsService(session=session)
    defn = await defn_svc.get_definition_by_workflow_id(workflow_id)
    if not defn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow definition not found",
        )
    dsl = DSLInput(**defn.content)
    store_svc = WorkflowStoreService(session=session)
    try:
        await store_svc.publish_workflow_dsl(dsl, params)
    except TracecatSettingsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except TracecatCredentialsNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
