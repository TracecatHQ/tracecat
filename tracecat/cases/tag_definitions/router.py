from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.cases.tags.models import CaseTagRead
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import CaseTagID
from tracecat.tags.models import TagCreate, TagUpdate

router = APIRouter(prefix="/case-tags", tags=["case-tags"])


@router.get("", response_model=list[CaseTagRead])
async def list_case_tags(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[CaseTagRead]:
    """List all case tags available in the current workspace."""
    service = CaseTagsService(session=session, role=role)
    tags = await service.list_workspace_tags()
    return [CaseTagRead.model_validate(tag, from_attributes=True) for tag in tags]


@router.get("/{tag_id}", response_model=CaseTagRead)
async def get_case_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: CaseTagID,
) -> CaseTagRead:
    """Retrieve a single case tag by ID."""
    service = CaseTagsService(session=session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from err
    return CaseTagRead.model_validate(tag, from_attributes=True)


@router.post("", response_model=CaseTagRead, status_code=status.HTTP_201_CREATED)
async def create_case_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: TagCreate,
) -> CaseTagRead:
    """Create a new case tag definition."""
    service = CaseTagsService(session=session, role=role)
    try:
        tag = await service.create_tag(params)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case tag already exists",
        ) from err
    return CaseTagRead.model_validate(tag, from_attributes=True)


@router.patch("/{tag_id}", response_model=CaseTagRead)
async def update_case_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: CaseTagID,
    params: TagUpdate,
) -> CaseTagRead:
    """Update an existing case tag definition."""
    service = CaseTagsService(session=session, role=role)
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case tag already exists",
        ) from err
    return CaseTagRead.model_validate(updated, from_attributes=True)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case_tag(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    tag_id: CaseTagID,
) -> None:
    """Delete a case tag definition."""
    service = CaseTagsService(session=session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from err
    await service.delete_tag(tag)
