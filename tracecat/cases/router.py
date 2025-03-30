import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.cases.models import (
    CaseCreate,
    CaseRead,
    CaseReadMinimal,
    CaseUpdate,
    CommentCreate,
    CommentUpdate,
    EventActivity,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.types.auth import AccessLevel, Role

router = APIRouter(prefix="/cases", tags=["cases"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]
WorkspaceAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=AccessLevel.ADMIN,
    ),
]


# Case Management


@router.get("")
async def list_cases(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> list[CaseReadMinimal]:
    """List all cases."""
    # TODO: Implement
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.get("/{case_id}")
async def get_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseRead:
    """Get a specific case."""
    # TODO: Implement
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: CaseCreate,
) -> None:
    """Create a new case."""
    # TODO: Implement
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.patch("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_case(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: CaseUpdate,
    case_id: uuid.UUID,
) -> None:
    """Update a case."""
    # TODO: Implement
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> None:
    """Delete a case."""
    # TODO: Implement. We may not want to allow delete. Currently only admins can delete cases.
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


# Case Comments
# Support comments as a first class activity type.
# We anticipate having other complex comment functionality in the future.


@router.post("/{case_id}/comments", status_code=status.HTTP_201_CREATED)
async def create_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CommentCreate,
) -> None:
    """Create a new comment on a case."""
    # TODO: Implement by constructing a CaseCommentCreate and storing it
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.patch(
    "/{case_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
    params: CommentUpdate,
) -> None:
    """Update an existing comment."""
    # TODO: Implement by finding the comment and creating a CaseCommentUpdate
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.delete(
    "/{case_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_comment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
) -> None:
    """Delete a comment."""
    # TODO: Implement by finding the comment and creating a CaseCommentDelete
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


# Case Activity
# This is append-only. Once created, a case activity cannot be updated or deleted.


@router.get("/{case_id}/events", status_code=status.HTTP_200_OK)
async def list_events(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[EventActivity]:
    """List all events for a case."""
    # TODO: Implement
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
