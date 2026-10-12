"""Workspace routes for forms."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Form
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.forms.access import can_read_form_config
from tracecat.forms.dependencies import require_forms_enabled
from tracecat.forms.enums import FormErrorCode
from tracecat.forms.exceptions import FormError
from tracecat.forms.schemas import (
    FormCreate,
    FormErrorDetail,
    FormErrorResponse,
    FormEventRead,
    FormRead,
    FormReadMinimal,
    FormResolved,
    FormSubmissionCreate,
    FormSubmissionRead,
    FormSubmissionReceipt,
    FormUpdate,
    FormUserRead,
    FormValidateRequest,
    FormValidateResponse,
)
from tracecat.forms.service import FormsService
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import CursorPaginatedResponse, PageParams, PaginationError

router = APIRouter(
    prefix="/forms",
    tags=["forms"],
    dependencies=[Depends(require_forms_enabled)],
)

PageQuery = Annotated[PageParams, Depends()]

_STATUS_BY_ERROR_CODE: dict[FormErrorCode, int] = {
    FormErrorCode.FORM_CONFIG_INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormErrorCode.FORM_WORKFLOW_UNPUBLISHED: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_OFFLINE: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_UNAVAILABLE: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_CHANGED: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_VALIDATION_ERROR: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormErrorCode.WORKFLOW_START_FAILED: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def form_http_error(exc: FormError) -> HTTPException:
    """Translate a typed form error into its HTTP response."""
    detail = FormErrorDetail(
        code=exc.code, message=str(exc), errors=exc.errors, issues=exc.issues
    )
    return HTTPException(
        status_code=_STATUS_BY_ERROR_CODE[exc.code],
        detail=detail.model_dump(mode="json"),
    )


def invalid_cursor_error(exc: PaginationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail or str(exc)
    )


async def get_form_or_404(service: FormsService, form_id: uuid.UUID) -> Form:
    if (form := await service.get_form(form_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Form not found"
        )
    return form


def require_config_access(role: Role) -> None:
    """Hide a form's raw config from requesters.

    ``form:read`` alone is what an intake-only requester holds. They get the
    resolved form from ``/render`` and nothing else, so the detail route
    answers 404 unless the caller builds forms or reads submissions.
    """
    if not can_read_form_config(role):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Form not found"
        )


@router.get("")
@require_scope("form:read")
async def list_forms(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    page: PageQuery,
) -> CursorPaginatedResponse[FormReadMinimal]:
    """List forms. Callers without form:update only see online forms."""
    try:
        return await FormsService(session, role).list_forms(page)
    except PaginationError as e:
        raise invalid_cursor_error(e) from e


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": FormErrorResponse}, 422: {"model": FormErrorResponse}},
)
@require_scope("form:create")
async def create_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: FormCreate,
) -> FormRead:
    """Create the form of a workflow. A workflow has at most one form."""
    service = FormsService(session, role)
    try:
        form = await service.create_form(params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except FormError as e:
        raise form_http_error(e) from e
    return await service.read_form(form)


@router.post("/validate")
@require_scope("form:update")
async def validate_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: FormValidateRequest,
) -> FormValidateResponse:
    """Resolve an unsaved config against a workflow's inputs."""
    try:
        return await FormsService(session, role).validate_config(
            WorkflowUUID.new(params.workflow_id), params.config
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except FormError as e:
        raise form_http_error(e) from e


@router.get("/{form_id}")
@require_scope("form:read")
async def get_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
) -> FormRead:
    """Get a form's config, its issues, its URL and its counts.

    Needs form:update or form:submission:read on top of form:read; other
    callers get 404.
    """
    require_config_access(role)
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    try:
        return await service.read_form(form)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except FormError as e:
        raise form_http_error(e) from e


@router.patch(
    "/{form_id}",
    responses={409: {"model": FormErrorResponse}, 422: {"model": FormErrorResponse}},
)
@require_scope("form:update")
async def update_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    params: FormUpdate,
) -> FormRead:
    """Update a form's status, config or both. The config is replaced whole."""
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    try:
        form = await service.update_form(form, params)
        return await service.read_form(form)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except FormError as e:
        raise form_http_error(e) from e


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("form:delete")
async def delete_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
) -> None:
    """Delete a form with its submissions and activity."""
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    await service.delete_form(form)


@router.get("/{form_id}/render", responses={409: {"model": FormErrorResponse}})
@require_scope("form:read")
async def render_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
) -> FormResolved:
    """Get an online form as the blocks a client renders, and record the view."""
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    try:
        resolved = await service.render_form(form)
    except FormError as e:
        raise form_http_error(e) from e
    await service.record_view(form)
    return resolved


@router.get("/{form_id}/activity")
@require_scope("form:submission:read")
async def list_form_activity(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    page: PageQuery,
) -> CursorPaginatedResponse[FormEventRead]:
    """List a form's views and submissions, newest first."""
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    try:
        return await service.list_events(form, page)
    except PaginationError as e:
        raise invalid_cursor_error(e) from e


@router.get("/{form_id}/users")
@require_scope("form:read")
async def search_form_users(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    page: PageQuery,
    search: str | None = Query(default=None, max_length=255),
) -> CursorPaginatedResponse[FormUserRead]:
    """Search organization members for a form's user field."""
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    try:
        return await service.search_users(form, page, search=search)
    except PaginationError as e:
        raise invalid_cursor_error(e) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except FormError as e:
        raise form_http_error(e) from e


@router.post(
    "/{form_id}/submissions",
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": FormErrorResponse},
        422: {"model": FormErrorResponse},
        503: {"model": FormErrorResponse},
    },
)
@require_scope("form:read", "form:submit")
async def submit_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    params: FormSubmissionCreate,
) -> FormSubmissionReceipt:
    """Submit a form. Starts the form's workflow with the answers as inputs."""
    form = await get_form_or_404(FormsService(session, role), form_id)
    try:
        return await FormSubmissionsService(session, role).submit(form, params)
    except FormError as e:
        raise form_http_error(e) from e


@router.get("/{form_id}/submissions")
@require_scope("form:submission:read")
async def list_form_submissions(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    page: PageQuery,
    submitted_after: datetime | None = Query(
        default=None, description="Only submissions at or after this time."
    ),
    submitted_before: datetime | None = Query(
        default=None, description="Only submissions before this time."
    ),
) -> CursorPaginatedResponse[FormSubmissionRead]:
    """List a form's submissions with answers, submitter and run status."""
    form = await get_form_or_404(FormsService(session, role), form_id)
    try:
        return await FormSubmissionsService(session, role).list_submissions(
            form,
            page,
            submitted_after=submitted_after,
            submitted_before=submitted_before,
        )
    except PaginationError as e:
        raise invalid_cursor_error(e) from e


@router.get("/{form_id}/submissions/{submission_id}")
@require_scope("form:read")
async def get_form_submission(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    submission_id: uuid.UUID,
) -> FormSubmissionRead:
    """Get a submission: the caller's own, or any with form:submission:read."""
    form = await get_form_or_404(FormsService(session, role), form_id)
    submission = await FormSubmissionsService(session, role).read_submission(
        form, submission_id
    )
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )
    return submission


@router.delete(
    "/{form_id}/submissions/{submission_id}", status_code=status.HTTP_204_NO_CONTENT
)
@require_scope("form:submission:delete")
async def delete_form_submission(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    submission_id: uuid.UUID,
) -> None:
    """Delete a submission. The workflow run it started is not affected."""
    form = await get_form_or_404(FormsService(session, role), form_id)
    service = FormSubmissionsService(session, role)
    if (submission := await service.get_submission(form, submission_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )
    await service.delete_submission(submission)
