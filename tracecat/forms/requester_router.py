"""Organization-level routes for people who fill in forms.

A requester arrives with a form ID and no workspace. These routes have no
``require_scope``: the role carries organization scopes only, so each one
checks ``form:read`` per workspace through ``FormsIntakeService``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from tracecat.auth.dependencies import OrgUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.forms.dependencies import require_forms_enabled_for_org
from tracecat.forms.schemas import (
    FormIntakeLocation,
    FormIntakeRead,
    FormIntakeSubmissionRead,
)
from tracecat.forms.service import FormsIntakeService
from tracecat.pagination import CursorPaginatedResponse, PageParams, PaginationError

router = APIRouter(
    prefix="/intake",
    tags=["forms"],
    dependencies=[Depends(require_forms_enabled_for_org)],
)

PageQuery = Annotated[PageParams, Depends()]


def _invalid_cursor_error(exc: PaginationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail or str(exc)
    )


@router.get("/forms")
async def list_intake_forms(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    page: PageQuery,
) -> CursorPaginatedResponse[FormIntakeRead]:
    """List online forms across the caller's workspaces."""
    try:
        return await FormsIntakeService(session, role).list_forms(page)
    except PaginationError as e:
        raise _invalid_cursor_error(e) from e


@router.get("/forms/{form_id}")
async def locate_intake_form(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
) -> FormIntakeLocation:
    """Find which workspace a form belongs to.

    Returns 404 for an unknown form, a form in another organization, and a
    form in a workspace where the caller cannot read forms.
    """
    location = await FormsIntakeService(session, role).locate_form(form_id)
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Form not found"
        )
    return location


@router.get("/submissions")
async def list_intake_submissions(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    page: PageQuery,
) -> CursorPaginatedResponse[FormIntakeSubmissionRead]:
    """List the caller's own submissions."""
    try:
        return await FormsIntakeService(session, role).list_submissions(page)
    except PaginationError as e:
        raise _invalid_cursor_error(e) from e
