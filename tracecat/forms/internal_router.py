"""Internal router for form operations (SDK/UDF use).

The executor's scopes are static, so these routes do not look at the user a
run is attributed to: a form-triggered run must be able to read its own
submission whoever submitted it.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.forms.dependencies import require_forms_enabled_for_executor
from tracecat.forms.files import FormFilesService
from tracecat.forms.router import get_form_or_404, not_found, paged
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
from tracecat.pagination import CursorPaginatedResponse, PageParams

router = APIRouter(
    prefix="/internal/forms",
    tags=["internal-forms"],
    include_in_schema=False,
    dependencies=[Depends(require_forms_enabled_for_executor)],
)

PageQuery = Annotated[PageParams, Depends()]


@router.get("")
@require_scope("form:read")
async def list_forms(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, page: PageQuery
) -> CursorPaginatedResponse[FormReadMinimal]:
    """List the workspace's forms, newest first."""
    return await paged(FormsService(session, role).list_forms(page))


# Declared before ``/{form_id}`` so ``submissions`` is not read as a form ID.
@router.get("/submissions/{submission_id}")
@require_scope("form:submission:read")
async def get_submission(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, submission_id: uuid.UUID
) -> FormSubmissionRead:
    """Get one submission with its answers."""
    service = FormSubmissionsService(session, role)
    form = await service.get_form_of_submission(submission_id)
    if (
        form is None
        or (submission := await service.read_submission(form, submission_id)) is None
    ):
        raise not_found("Submission")
    return submission


@router.delete("/submissions/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("form:submission:delete")
async def delete_submission(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, submission_id: uuid.UUID
) -> None:
    """Delete a submission. The workflow run it started is not affected."""
    service = FormSubmissionsService(session, role)
    form = await service.get_form_of_submission(submission_id)
    if (
        form is None
        or (submission := await service.get_submission(form, submission_id)) is None
    ):
        raise not_found("Submission")
    await service.delete_submission(submission)


@router.get("/files/{file_id}/download")
@require_scope("form:submission:read")
async def download_file(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, file_id: uuid.UUID
) -> FormFileContent:
    """Download a file a submission has claimed, with its content in base64."""
    if (content := await FormFilesService(session, role).read_content(file_id)) is None:
        raise not_found("File")
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
    return await service.read_form(await service.upsert_form(workflow_id, params))


@router.get("/{form_id}")
@require_scope("form:read")
async def get_form(
    *, role: ExecutorWorkspaceRole, session: AsyncDBSession, form_id: uuid.UUID
) -> FormRead:
    """Get a form's config, its issues, its URL and its counts."""
    service = FormsService(session, role)
    return await service.read_form(await get_form_or_404(service, form_id))


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
    return await paged(
        FormSubmissionsService(session, role).list_submissions(
            form,
            page,
            submitted_after=submitted_after,
            submitted_before=submitted_before,
        )
    )
