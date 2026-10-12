"""Tests for removing form files when their owner is deleted.

Blob deletion is recorded by a fake patched onto ``tracecat.storage.blob``.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import create_form, create_workflow
from tracecat.auth.types import Role
from tracecat.db.models import Form, FormFile, FormSubmission, Workspace
from tracecat.forms import storage
from tracecat.forms.service import FormsService
from tracecat.forms.storage import delete_file_blobs, file_blob_key
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage import blob
from tracecat.workflow.management.management import WorkflowsManagementService

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


@pytest.fixture
def deleted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Keys passed to ``blob.delete_file``. A key holding ``fail`` raises."""
    keys: list[str] = []

    async def delete_file(
        key: str, bucket: str, *, redact_log_identifiers: bool = False
    ) -> None:
        keys.append(key)
        if "fail" in key:
            raise blob.StorageDeleteError(error_code="SlowDown")

    monkeypatch.setattr(blob, "delete_file", delete_file)
    return keys


@pytest.fixture
def role(svc_workspace: Workspace) -> Role:
    return Role(
        type="user",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )


async def _form(session: AsyncSession, workspace: Workspace, title: str) -> Form:
    workflow = await create_workflow(session, workspace.id, title=title)
    return await create_form(session, workflow)


async def _submission(session: AsyncSession, form: Form) -> FormSubmission:
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        answers={},
        definition_version=1,
        wf_exec_id="wf_synthetic/exec_synthetic",
    )
    session.add(submission)
    await session.commit()
    return submission


async def _file(
    session: AsyncSession, form: Form, submission: FormSubmission | None = None
) -> str:
    """Add a file row and return its blob key."""
    file = FormFile(
        workspace_id=form.workspace_id,
        form_id=form.id,
        submission_id=submission.id if submission is not None else None,
        field="quote",
        name="quote.pdf",
        content_type="application/pdf",
        size=12,
        sha256="0" * 64,
    )
    session.add(file)
    await session.commit()
    return file_blob_key(form.workspace_id, form.id, file.id)


async def _remaining_keys(session: AsyncSession) -> set[str]:
    rows = (
        (
            await session.execute(
                select(FormFile.workspace_id, FormFile.form_id, FormFile.id)
            )
        )
        .tuples()
        .all()
    )
    return {file_blob_key(*row) for row in rows}


async def test_deleting_a_submission_deletes_its_files_and_blobs(
    session: AsyncSession, role: Role, svc_workspace: Workspace, deleted: list[str]
) -> None:
    form = await _form(session, svc_workspace, "Purchase")
    submission = await _submission(session, form)
    other_submission = await _submission(session, form)
    claimed = {await _file(session, form, submission) for _ in range(2)}
    kept = {await _file(session, form, other_submission), await _file(session, form)}

    await FormSubmissionsService(session, role).delete_submission(submission)

    assert set(deleted) == claimed
    assert await _remaining_keys(session) == kept


async def test_deleting_a_form_deletes_every_file_of_the_form(
    session: AsyncSession, role: Role, svc_workspace: Workspace, deleted: list[str]
) -> None:
    form = await _form(session, svc_workspace, "Purchase")
    other_form = await _form(session, svc_workspace, "Other")
    submission = await _submission(session, form)
    gone = {await _file(session, form, submission), await _file(session, form)}
    kept = {await _file(session, other_form)}

    await FormsService(session, role).delete_form(form)

    assert set(deleted) == gone
    assert await _remaining_keys(session) == kept


async def test_deleting_a_workflow_deletes_the_files_of_its_form(
    session: AsyncSession, role: Role, svc_workspace: Workspace, deleted: list[str]
) -> None:
    form = await _form(session, svc_workspace, "Purchase")
    other_form = await _form(session, svc_workspace, "Other")
    submission = await _submission(session, form)
    gone = {await _file(session, form, submission), await _file(session, form)}
    kept = {await _file(session, other_form)}

    await WorkflowsManagementService(session, role=role).delete_workflow(
        WorkflowUUID.new(form.workflow_id)
    )

    assert set(deleted) == gone
    assert await _remaining_keys(session) == kept


async def test_deleting_a_workflow_without_a_form_touches_no_blob(
    session: AsyncSession, role: Role, svc_workspace: Workspace, deleted: list[str]
) -> None:
    workflow = await create_workflow(session, svc_workspace.id, title="Plain")

    await WorkflowsManagementService(session, role=role).delete_workflow(
        WorkflowUUID.new(workflow.id)
    )

    assert deleted == []


async def test_blob_deletion_is_best_effort_and_bounded(
    deleted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "MAX_BLOB_DELETES_PER_REQUEST", 3)
    keys = ["forms/w/f/1", "forms/w/f/fail", "forms/w/f/3", "forms/w/f/4"]

    await delete_file_blobs(keys)

    # A failure does not stop the rest; the fourth is left for an operator.
    assert deleted == keys[:3]
