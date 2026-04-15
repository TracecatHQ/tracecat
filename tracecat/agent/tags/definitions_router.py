"""HTTP routes for agent tag definition CRUD."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.agent.tags.schemas import AgentTagRead
from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import AgentTagID
from tracecat.tags.schemas import TagCreate, TagUpdate

router = APIRouter(prefix="/agent-tags", tags=["agent-tags"])


@router.get("", response_model=list[AgentTagRead])
@require_scope("agent:read")
async def list_agent_tags(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[AgentTagRead]:
    """List all agent tags in the workspace."""
    service = AgentTagsService(session=session, role=role)
    tags = await service.list_tags()
    return [AgentTagRead.model_validate(tag, from_attributes=True) for tag in tags]


@router.get("/{tag_id}", response_model=AgentTagRead)
@require_scope("agent:read")
async def get_agent_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: AgentTagID,
) -> AgentTagRead:
    """Get an agent tag by ID."""
    service = AgentTagsService(session=session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from err
    return AgentTagRead.model_validate(tag, from_attributes=True)


@router.post("", response_model=AgentTagRead, status_code=status.HTTP_201_CREATED)
async def create_agent_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: TagCreate,
) -> AgentTagRead:
    """Create a new agent tag definition."""
    service = AgentTagsService(session=session, role=role)
    try:
        tag = await service.create_tag(params)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent tag already exists",
        ) from err
    return AgentTagRead.model_validate(tag, from_attributes=True)


@router.patch("/{tag_id}", response_model=AgentTagRead)
async def update_agent_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: AgentTagID,
    params: TagUpdate,
) -> AgentTagRead:
    """Update an agent tag definition."""
    service = AgentTagsService(session=session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from err
    try:
        updated = await service.update_tag(tag, params)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent tag already exists",
        ) from err
    return AgentTagRead.model_validate(updated, from_attributes=True)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: AgentTagID,
) -> None:
    """Delete an agent tag definition."""
    service = AgentTagsService(session=session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from err
    await service.delete_tag(tag)
