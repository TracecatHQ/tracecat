from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import NoResultFound

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers.workflow import WorkflowID
from tracecat.tags.models import TagRead
from tracecat.workflow.tags.models import WorkflowTagCreate
from tracecat.workflow.tags.service import WorkflowTagsService

# Create a FastAPI router for workflow tags
router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("/{workflow_id}/tags", response_model=list[TagRead])
async def list_tags(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
) -> list[TagRead]:
    """List all tags for a workflow."""
    service = WorkflowTagsService(session, role=role)
    db_tags = await service.list_tags_for_workflow(workflow_id)
    return [TagRead.model_validate(tag, from_attributes=True) for tag in db_tags]


@router.post("/{workflow_id}/tags", status_code=status.HTTP_201_CREATED)
async def add_tag(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    params: WorkflowTagCreate,
) -> None:
    """Add a tag to a workflow."""
    service = WorkflowTagsService(session, role=role)
    await service.add_workflow_tag(workflow_id, params.tag_id)


@router.delete("/{workflow_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    tag_id: UUID4,
) -> None:
    service = WorkflowTagsService(session, role=role)
    try:
        wf_tag = await service.get_workflow_tag(workflow_id, tag_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from e
    await service.remove_workflow_tag(wf_tag)
