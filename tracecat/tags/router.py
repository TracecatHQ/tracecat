from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.audit.logger import AuditLogger
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Tag
from tracecat.identifiers import TagID
from tracecat.tags.schemas import TagCreate, TagRead, TagUpdate
from tracecat.tags.service import TagsService

# Initialize router
router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagRead])
async def list_tags(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> Sequence[Tag]:
    """List all tags for the current workspace."""
    service = TagsService(session, role)
    return await service.list_tags()


@router.get("/{tag_id}", response_model=TagRead)
async def get_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: TagID,
) -> Tag:
    """Get a specific tag by ID."""
    service = TagsService(session, role)
    try:
        return await service.get_tag(tag_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from e


@router.post("", response_model=TagRead)
async def create_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag: TagCreate,
) -> Tag:
    """Create a new tag."""
    async with AuditLogger(
        resource_type="tag",
        action="create",
        session=session,
    ) as audit_log:
        service = TagsService(session, role)
        try:
            new_tag = await service.create_tag(tag)
            audit_log.set_resource(new_tag.id)
            return new_tag
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tag already exists",
            ) from e


@router.patch("/{tag_id}", response_model=TagRead)
async def update_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: TagID,
    tag_update: TagUpdate,
) -> Tag:
    """Update an existing tag."""
    async with AuditLogger(
        resource_type="tag",
        action="update",
        resource_id=tag_id,
        session=session,
    ):
        service = TagsService(session, role)
        tag = await service.get_tag(tag_id)
        try:
            return await service.update_tag(tag, tag_update)
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tag already exists",
            ) from e


@router.delete("/{tag_id}")
async def delete_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: TagID,
) -> None:
    """Delete a tag."""
    async with AuditLogger(
        resource_type="tag",
        action="delete",
        resource_id=tag_id,
        session=session,
    ):
        service = TagsService(session, role)
        try:
            tag = await service.get_tag(tag_id)
        except NoResultFound as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tag not found",
            ) from e
        await service.delete_tag(tag)
