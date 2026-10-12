"""Internal router for form operations (SDK/UDF use).

The executor's scopes are static, so these routes do not look at the user a
run is attributed to: a form-triggered run must be able to read its own
submission whoever submitted it.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.forms.dependencies import require_forms_enabled_for_executor
from tracecat.forms.exceptions import FormError
from tracecat.forms.files import FormFilesService
from tracecat.forms.router import form_http_error, get_form_or_404, invalid_cursor_error
from tracecat.forms.schemas import (
    FormFileContent,
    FormRead,
    FormReadMinimal,
    FormSubmissionRead,
    FormUpdate,
)
from tracecat.forms.service import FormsService
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.pagination import CursorPaginatedResponse, PageParams, PaginationError

router = APIRouter(
    prefix="/internal/forms",
    tags=["internal-forms"],
    include_in_schema=False,
    dependencies=[Depends(require_forms_enabled_for_executor)],
)

PageQuery = Annotated[PageParams, Depends()]


def _submission_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
    )


@router.get("")
@require_scope("form:read")
async def list_forms(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    page: PageQuery,
) -> CursorPaginatedResponse[FormReadMinimal]:
    """List the workspace's forms, newest first."""
    try:
        return await FormsService(session, role).list_forms(page)
    except PaginationError as e:
        raise invalid_cursor_error(e) from e


# Declared before ``/{form_id}`` so ``submissions`` is not read as a form ID.
@router.get("/submissions/{submission_id}")
@require_scope("form:submission:read")
async def get_submission(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    submission_id: uuid.UUID,
) -> FormSubmissionRead:
    """Get one submission with its answers."""
    service = FormSubmissionsService(session, role)
    if (form := await service.get_form_of_submission(submission_id)) is None:
        raise _submission_not_found()
    if (submission := await service.read_submission(form, submission_id)) is None:
        raise _submission_not_found()
    return submission


@router.delete("/submissions/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("form:submission:delete")
async def delete_submission(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    submission_id: uuid.UUID,
) -> None:
    """Delete a submission. The workflow run it started is not affected."""
    service = FormSubmissionsService(session, role)
    if (form := await service.get_form_of_submission(submission_id)) is None:
        raise _submission_not_found()
    if (submission := await service.get_submission(form, submission_id)) is None:
        raise _submission_not_found()
    await service.delete_submission(submission)


@router.get("/files/{file_id}/download")
@require_scope("form:submission:read")
async def download_file(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    file_id: uuid.UUID,
) -> FormFileContent:
    """Download a file a submission has claimed, with its content in base64."""
    content = await FormFilesService(session, role).read_content(file_id)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
        )
    return content


@router.put("/workflows/{workflow_id}")
@require_scope("form:create", "form:update")
async def upsert_form(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: FormUpdate,
) -> FormRead:
    """Update a workflow's form, creating it if the workflow has none."""
    service = FormsService(session, role)
    try:
        form = await service.upsert_form(workflow_id, params)
        return await service.read_form(form)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except FormError as e:
        raise form_http_error(e) from e


@router.get("/{form_id}")
@require_scope("form:read")
async def get_form(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
) -> FormRead:
    """Get a form's config, its issues, its URL and its counts."""
    service = FormsService(session, role)
    form = await get_form_or_404(service, form_id)
    try:
        return await service.read_form(form)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except FormError as e:
        raise form_http_error(e) from e


@router.get("/{form_id}/submissions")
@require_scope("form:submission:read")
async def list_submissions(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    form_id: uuid.UUID,
    page: PageQuery,
    submitted_after: datetime | None = Query(default=None),
    submitted_before: datetime | None = Query(default=None),
) -> CursorPaginatedResponse[FormSubmissionRead]:
    """List a form's submissions with answers, newest first."""
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
