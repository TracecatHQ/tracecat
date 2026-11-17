from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import NoResultFound

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.tags.service import TagsService

router = APIRouter(
    prefix="/internal/tags", tags=["internal-tags"], include_in_schema=False
)


@router.delete("/{tag_identifier}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("tag:delete")
async def executor_delete_workflow_tag(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    tag_identifier: str,
) -> None:
    service = TagsService(session, role)
    try:
        tag = await service.get_tag_by_ref_or_id(tag_identifier)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from err
    await service.delete_tag(tag)
