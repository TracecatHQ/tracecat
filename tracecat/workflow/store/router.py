# tracecat/workflow/sync/router.py
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers.workflow import WorkflowID

router = APIRouter(prefix="/workflows", tags=["workflows"])


class PublishWorkflowRequest(BaseModel):
    repo_url: str
    ref: str | None = None
    message: str | None = None


@router.post("/{workflow_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def publish_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    params: PublishWorkflowRequest,
):
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
