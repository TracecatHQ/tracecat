from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.cases.tags.models import CaseTagCreate, CaseTagRead
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.types.auth import Role

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("/{case_id}/tags", response_model=list[CaseTagRead])
async def list_tags(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
) -> list[CaseTagRead]:
    """List all tags for a case."""
    service = CaseTagsService(session, role=role)
    tags = await service.list_tags_for_case(case_id)
    return [
        CaseTagRead(id=tag.id, name=tag.name, ref=tag.ref, color=tag.color)
        for tag in tags
    ]


@router.post(
    "/{case_id}/tags", status_code=status.HTTP_201_CREATED, response_model=CaseTagRead
)
async def add_tag(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    params: CaseTagCreate,
) -> CaseTagRead:
    """Add a tag to a case using tag ID or slug."""
    service = CaseTagsService(session, role=role)
    try:
        tag = await service.add_case_tag(case_id, str(params.tag_id))
        return CaseTagRead(id=tag.id, name=tag.name, ref=tag.ref, color=tag.color)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        ) from err
    except IntegrityError as e:
        # This shouldn't happen with our idempotent design, but handle it
        if "duplicate key" in str(e).lower():
            # Re-fetch the existing tag
            tag = await service.add_case_tag(case_id, str(params.tag_id))
            return CaseTagRead(id=tag.id, name=tag.name, ref=tag.ref, color=tag.color)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Tag operation failed"
        ) from e


@router.delete(
    "/{case_id}/tags/{tag_identifier}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_tag(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    tag_identifier: str,  # Can be UUID or ref
) -> None:
    """Remove a tag from a case using tag ID or ref."""
    service = CaseTagsService(session, role=role)
    try:
        await service.remove_case_tag(case_id, tag_identifier)
    except NoResultFound as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found on case"
        ) from err
