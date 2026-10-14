"""Workspace and requester routes for forms.

``FormError`` becomes its HTTP response in ``form_error_handler``, which both
apps register.
"""

import uuid
from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status

from tracecat import config
from tracecat.auth.dependencies import OrgUserRole, WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Form
from tracecat.forms.access import can_read_form_config
from tracecat.forms.dependencies import (
    require_forms_enabled,
    require_forms_enabled_for_org,
)
from tracecat.forms.enums import FormErrorCode
from tracecat.forms.exceptions import FormError
from tracecat.forms.files import FormFilesService
from tracecat.forms.schemas import (
    FormCreate,
    FormErrorResponse,
    FormFileDownload,
    FormFileRead,
    FormIntakeLocation,
    FormIntakeRead,
    FormIntakeSubmissionRead,
    FormPreview,
    FormRead,
    FormReadMinimal,
    FormSubmissionCreate,
    FormSubmissionRead,
    FormSubmissionReceipt,
    FormUpdate,
    FormUserRead,
    FormValidateRequest,
    FormValidateResponse,
)
from tracecat.forms.service import FormsIntakeService, FormsService
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.pagination import CursorPaginatedResponse, PageParams, PaginationError

router = APIRouter(
    prefix="/forms", tags=["forms"], dependencies=[Depends(require_forms_enabled)]
)
intake_router = APIRouter(
    prefix="/intake",
    tags=["forms"],
    dependencies=[Depends(require_forms_enabled_for_org)],
)

PageQuery = Annotated[PageParams, Depends()]


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": FormErrorResponse} for code in codes}


async def paged[T](items: Awaitable[T]) -> T:
    """Answer a bad pagination cursor with 400."""
    try:
        return await items
    except PaginationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=e.detail or str(e)
        ) from e


def not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found"
    )


async def get_form_or_404(
    service: FormsService, form_id: uuid.UUID, *, config_access: bool = False
) -> Form:
    """Get a form of the workspace.

    With ``config_access``, a requester (who may not read the raw config)
    gets the same 404 as for an unknown ID.
    """
    if config_access and not can_read_form_config(service.role):
        raise not_found("Form")
    if (form := await service.get_form(form_id)) is None:
        raise not_found("Form")
    return form


# ----- Builder routes -----


@router.get("")
@require_scope("form:read")
async def list_forms(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, page: PageQuery
) -> CursorPaginatedResponse[FormReadMinimal]:
    """List forms. Callers without form:update only see online forms."""
    return await paged(FormsService(session, role).list_forms(page))


@router.post("", status_code=status.HTTP_201_CREATED, responses=_errors(404, 409, 422))
@require_scope("form:create")
async def create_form(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, params: FormCreate
) -> FormRead:
    """Create the form of a workflow."""
    service = FormsService(session, role)
    return await service.read_form(await service.create_form(params))


@router.get("/{form_id}")
@require_scope("form:read")
async def get_form(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, form_id: uuid.UUID
) -> FormRead:
    """Get a form's config, its issues, its URL and its counts.

    Needs form:update or form:submission:read on top of form:read; other
    callers get 404.
    """
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id, config_access=True)
    return await service.read_form(form)


@router.patch("/{form_id}", responses=_errors(409, 422))
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
    form = await service.update_form(await get_form_or_404(service, form_id), params)
    return await service.read_form(form)


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("form:delete")
async def delete_form(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, form_id: uuid.UUID
) -> None:
    """Delete a form with its submissions, views and files."""
    service = FormsService(session, role)
    await service.delete_form(await get_form_or_404(service, form_id))


@router.post("/{form_id}/validate")
@require_scope("form:read")
async def validate_form(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    params: FormValidateRequest,
) -> FormValidateResponse:
    """Resolve a config, saved or not, against the inputs of a form's workflow.

    Needs form:update or form:submission:read on top of form:read; other
    callers get 404.
    """
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id, config_access=True)
    return await service.validate_config(form, params.config)


@router.get("/{form_id}/render", responses=_errors(409))
@require_scope("form:read")
async def render_form(
    *, role: WorkspaceActorRouteRole, session: AsyncDBSession, form_id: uuid.UUID
) -> FormPreview:
    """Get an online form as the blocks a client renders, and record the view."""
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    preview = await service.render_form(form)
    await service.record_view(form)
    return preview


@router.get("/{form_id}/users", responses=_errors(404, 409))
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
    return await paged(service.search_users(form, page, search=search))


# ----- Submitting -----


@router.post(
    "/{form_id}/submissions",
    status_code=status.HTTP_201_CREATED,
    responses=_errors(409, 422, 503),
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
    return await FormSubmissionsService(session, role).submit(form, params)


@router.post(
    "/{form_id}/files",
    status_code=status.HTTP_201_CREATED,
    responses=_errors(400, 409, 413, 415, 422, 503),
)
@require_scope("form:read", "form:submit")
async def upload_form_file(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    file: UploadFile,
    field: str = Query(
        max_length=255, description="Name of the file field the upload is for."
    ),
) -> FormFileRead:
    """Upload a file for one of an online form's file fields.

    Submit the returned ID as the field's value. A file that is not submitted
    within 24 hours is deleted.
    """
    form = await get_form_or_404(FormsService(session, role), form_id)
    # The body is already spooled; refuse to load an oversized one into memory.
    max_size = config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
    if file.size is not None and file.size > max_size:
        raise FormError(
            FormErrorCode.FILE_TOO_LARGE,
            f"Files can be at most {max_size // (1024 * 1024)} MB",
        )
    return await FormFilesService(session, role).upload(
        form,
        field,
        filename=file.filename or "",
        content_type=file.content_type or "application/octet-stream",
        content=await file.read(),
    )


@router.get("/{form_id}/files/{file_id}/download")
@require_scope("form:read")
async def download_form_file(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    file_id: uuid.UUID,
) -> FormFileDownload:
    """Get a short-lived download URL for a file: one the caller uploaded, or
    any with form:submission:read."""
    form = await get_form_or_404(FormsService(session, role), form_id)
    download = await FormFilesService(session, role).get_download(form.id, file_id)
    if download is None:
        raise not_found("File")
    return download


# ----- Submissions -----


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
    return await paged(
        FormSubmissionsService(session, role).list_submissions(
            form,
            page,
            submitted_after=submitted_after,
            submitted_before=submitted_before,
        )
    )


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
    service = FormSubmissionsService(session, role)
    if (submission := await service.read_submission(form, submission_id)) is None:
        raise not_found("Submission")
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
        raise not_found("Submission")
    await service.delete_submission(submission)


# ----- Requester routes -----
# A requester arrives with a form ID and no workspace. These routes have no
# ``require_scope``: the role carries organization scopes only, so the service
# checks ``form:read`` per workspace.


@intake_router.get("/forms")
async def list_intake_forms(
    *, role: OrgUserRole, session: AsyncDBSession, page: PageQuery
) -> CursorPaginatedResponse[FormIntakeRead]:
    """List online forms across the caller's workspaces."""
    return await paged(FormsIntakeService(session, role).list_forms(page))


@intake_router.get("/forms/{form_id}")
async def locate_intake_form(
    *, role: OrgUserRole, session: AsyncDBSession, form_id: uuid.UUID
) -> FormIntakeLocation:
    """Find which workspace a form belongs to.

    Returns 404 for an unknown form, a form in another organization, and a
    form in a workspace where the caller cannot read forms.
    """
    if (
        location := await FormsIntakeService(session, role).locate_form(form_id)
    ) is None:
        raise not_found("Form")
    return location


@intake_router.get("/submissions")
async def list_intake_submissions(
    *, role: OrgUserRole, session: AsyncDBSession, page: PageQuery
) -> CursorPaginatedResponse[FormIntakeSubmissionRead]:
    """List the caller's own submissions."""
    return await paged(FormsIntakeService(session, role).list_submissions(page))
