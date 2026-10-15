"""Form files: upload, claim on submit, download, expiry and deletion.

Blob storage is the in-memory ``blob_store`` fake, so these tests need no MinIO.
"""

import asyncio
import base64
import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import String, delete, event, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from tests._forms_utils import (
    PDF,
    FakeBlobStore,
    FakeTemporal,
    create_file,
    create_form,
    create_organization_with_workspace,
    create_submission,
    create_workflow,
    publish_workflow,
    use_session,
)
from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import Form, FormFile, FormSubmission, FormView, Workspace
from tracecat.exceptions import TracecatException
from tracecat.forms import router as forms_router
from tracecat.forms import storage
from tracecat.forms.constants import MAX_PENDING_FILES_PER_UPLOADER, PENDING_FILE_TTL
from tracecat.forms.enums import FormErrorCode, FormFieldErrorCode, FormStatus
from tracecat.forms.exceptions import FormError
from tracecat.forms.files import FormFilesService, matches_accept
from tracecat.forms.schemas import FormFileRead, FormSubmissionCreate
from tracecat.forms.service import FormsService
from tracecat.forms.storage import file_blob_key, purge_expired_files
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage import blob
from tracecat.workflow.management.management import WorkflowsManagementService

pytestmark = [
    pytest.mark.usefixtures("db", "blob_store"),
    pytest.mark.anyio,
]

TEXT = b"Quote for one laptop.\n"
TEXT_FILE = {"filename": "notes.txt", "content_type": "text/plain", "content": TEXT}
EXPIRED = PENDING_FILE_TTL + timedelta(minutes=1)

EXPECTS: dict[str, dict[str, Any]] = {
    "has_extras": {"type": "bool", "default": False},
    "quote": {"type": "dict | None", "default": None},
    "extras": {"type": "list[dict]", "default": []},
    "conditional": {"type": "dict | None", "default": None},
    "notes": {"type": "str", "default": ""},
    "internal": {"type": "dict | None", "default": None},
}
LAYOUT: list[dict[str, Any]] = [
    {"field": "has_extras"},
    {"field": "quote", "widget": "file", "accept": [".pdf"]},
    {"field": "extras", "widget": "file", "max_files": 2},
    {
        "field": "conditional",
        "widget": "file",
        "visible_if": {"field": "has_extras", "equals": True},
    },
    {"field": "notes"},
    {"field": "internal", "hidden": True},
]


async def _online_form(
    session: AsyncSession, role: Role, workspace_id: uuid.UUID, title: str = "Other"
) -> Form:
    workflow = await create_workflow(session, workspace_id, title=title)
    await publish_workflow(session, role, workflow, EXPECTS)
    return await create_form(session, workflow, layout=LAYOUT)


@pytest.fixture
async def form(
    session: AsyncSession, intake_role: Role, svc_workspace: Workspace
) -> Form:
    return await _online_form(session, intake_role, svc_workspace.id, "Purchase")


@pytest.fixture
def files(session: AsyncSession, intake_role: Role) -> FormFilesService:
    return FormFilesService(session, intake_role)


@pytest.fixture
def submissions(session: AsyncSession, intake_role: Role) -> FormSubmissionsService:
    return FormSubmissionsService(session, intake_role)


async def _upload(
    service: FormFilesService,
    form: Form,
    field_name: str = "quote",
    *,
    filename: str = "quote.pdf",
    content_type: str = "application/pdf",
    content: bytes = PDF,
) -> FormFileRead:
    return await service.upload(
        form, field_name, filename=filename, content_type=content_type, content=content
    )


async def _upload_error(
    service: FormFilesService, form: Form, field_name: str = "quote", **kwargs: Any
) -> FormErrorCode:
    with pytest.raises(FormError) as exc_info:
        await _upload(service, form, field_name, **kwargs)
    return exc_info.value.code


def _values(**values: Any) -> FormSubmissionCreate:
    return FormSubmissionCreate(values=values)


async def _claim_errors(
    service: FormSubmissionsService, form: Form, **values: Any
) -> dict[str, FormFieldErrorCode]:
    with pytest.raises(FormError) as exc_info:
        await service.submit(form, _values(**values))
    assert exc_info.value.code is FormErrorCode.FORM_VALIDATION_ERROR
    return {item.field: item.code for item in exc_info.value.errors or []}


async def _rows(session: AsyncSession) -> dict[uuid.UUID, FormFile]:
    result = await session.execute(
        select(FormFile).execution_options(populate_existing=True)
    )
    return {row.id: row for row in result.scalars()}


async def _age(session: AsyncSession, file_id: uuid.UUID, age: timedelta) -> None:
    await session.execute(
        update(FormFile)
        .where(FormFile.id == file_id)
        .values(created_at=datetime.now(UTC) - age)
    )
    await session.commit()


def _key(form: Form, file_id: uuid.UUID) -> str:
    return file_blob_key(form.workspace_id, form.id, file_id)


async def _set_workspace_settings(
    session: AsyncSession, workspace: Workspace, settings: dict[str, Any]
) -> None:
    await session.execute(
        update(Workspace).where(Workspace.id == workspace.id).values(settings=settings)
    )
    await session.commit()


# ----- Upload -----


@pytest.mark.parametrize(
    ("accept", "extension", "content_type", "expected"),
    [
        (None, ".pdf", "application/pdf", True),
        ([], ".pdf", "application/pdf", True),
        ([".pdf"], ".pdf", "application/pdf", True),
        ([" .PDF "], ".pdf", "application/pdf", True),
        ([".pdf"], ".txt", "text/plain", False),
        (["image/*"], ".png", "image/png", True),
        (["image/*"], ".pdf", "application/pdf", False),
        (["application/pdf"], ".pdf", "application/pdf", True),
        (["text/csv", ".png"], ".pdf", "application/pdf", False),
        (["*/*"], ".pdf", "application/pdf", False),
        (["pdf"], ".pdf", "application/pdf", False),
    ],
)
def test_matches_accept(
    accept: list[str] | None, extension: str, content_type: str, expected: bool
) -> None:
    matched = matches_accept(accept, extension=extension, content_type=content_type)

    assert matched is expected


async def test_upload_stores_a_pending_file_and_serves_it_back(
    session: AsyncSession,
    files: FormFilesService,
    blob_store: FakeBlobStore,
    form: Form,
    intake_role: Role,
) -> None:
    uploaded = await _upload(files, form, filename="Q3 quote (final).pdf")

    assert uploaded.model_dump() == {
        "id": uploaded.id,
        "name": "Q3 quote (final).pdf",
        "content_type": "application/pdf",
        "size": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(),
    }
    # The key holds IDs only; the name travels as a download header.
    key = f"forms/{form.workspace_id}/{form.id}/{uploaded.id}"
    assert blob_store.objects == {key: PDF}
    assert blob_store.content_types == {key: "application/pdf"}
    [row] = (await _rows(session)).values()
    assert (row.form_id, row.field, row.submission_id) == (form.id, "quote", None)
    assert row.uploaded_by_user_id == intake_role.user_id
    assert row.uploaded_by_service_account_id is None

    download = await files.get_download(form.id, uploaded.id)
    assert download is not None
    assert download.model_dump() == {
        "download_url": f"https://storage.example.com/{key}?signature=synthetic",
        "file_name": "Q3 quote (final).pdf",
        "content_type": "application/pdf",
    }
    assert blob_store.presigned == [
        {
            "key": key,
            "bucket": config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS,
            "force_download": True,
            "override_content_type": "application/octet-stream",
            "redact_log_identifiers": True,
            "download_filename": "Q3 quote (final).pdf",
        }
    ]

    content = await files.read_content(uploaded.id)
    assert content is not None
    assert base64.b64decode(content.content_base64) == PDF
    assert content.model_dump(exclude={"content_base64"}) == uploaded.model_dump()
    blob_store.objects[key] = b"tampered"
    with pytest.raises(TracecatException):
        await files.read_content(uploaded.id)
    del blob_store.objects[key]
    assert await files.read_content(uploaded.id) is None


@pytest.mark.parametrize(
    ("field_name", "kwargs", "expected"),
    [
        # The bytes decide the type, not the declared type.
        ("quote", {"content": TEXT}, FormErrorCode.FILE_TYPE_NOT_ALLOWED),
        ("quote", {"filename": "../quote.pdf"}, FormErrorCode.FILE_INVALID),
        ("quote", {"filename": "dir/quote.pdf"}, FormErrorCode.FILE_INVALID),
        ("quote", {"filename": ""}, FormErrorCode.FILE_INVALID),
        ("quote", {"content": b""}, FormErrorCode.FILE_INVALID),
        ("quote", {"filename": "quote.exe"}, FormErrorCode.FILE_TYPE_NOT_ALLOWED),
        (
            "quote",
            {"content_type": "application/x-msdownload"},
            FormErrorCode.FILE_TYPE_NOT_ALLOWED,
        ),
        # Allowed by the workspace, not by the field's accept.
        ("quote", TEXT_FILE, FormErrorCode.FILE_TYPE_NOT_ALLOWED),
        ("notes", {}, FormErrorCode.FILE_FIELD_INVALID),
        ("internal", {}, FormErrorCode.FILE_FIELD_INVALID),
        ("missing", {}, FormErrorCode.FILE_FIELD_INVALID),
    ],
)
async def test_upload_rejects_files_that_fail_validation(
    session: AsyncSession,
    files: FormFilesService,
    blob_store: FakeBlobStore,
    form: Form,
    field_name: str,
    kwargs: dict[str, Any],
    expected: FormErrorCode,
) -> None:
    assert await _upload_error(files, form, field_name, **kwargs) is expected
    assert blob_store.objects == {}
    assert await _rows(session) == {}


async def test_upload_follows_the_workspace_policy_and_the_form_state(
    session: AsyncSession,
    files: FormFilesService,
    blob_store: FakeBlobStore,
    form: Form,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "extras" has no accept, and a conditional field takes uploads too.
    await _upload(files, form, "extras", **TEXT_FILE)
    await _upload(files, form, "conditional")
    policy = {
        "allowed_attachment_extensions": [".pdf"],
        "allowed_attachment_mime_types": ["application/pdf"],
        # Ignored: form uploads always check the bytes.
        "validate_attachment_magic_number": False,
    }
    await _set_workspace_settings(session, svc_workspace, policy)

    assert await _upload_error(files, form, "extras", **TEXT_FILE) is (
        FormErrorCode.FILE_TYPE_NOT_ALLOWED
    )
    assert await _upload_error(files, form, content=TEXT) is (
        FormErrorCode.FILE_TYPE_NOT_ALLOWED
    )
    monkeypatch.setattr(config, "TRACECAT__MAX_ATTACHMENT_SIZE_BYTES", len(PDF) - 1)
    assert await _upload_error(files, form) is FormErrorCode.FILE_TOO_LARGE
    form.status = FormStatus.OFFLINE.value
    await session.commit()
    assert await _upload_error(files, form) is FormErrorCode.FORM_OFFLINE
    assert len(blob_store.objects) == 2


async def test_upload_route_refuses_an_oversized_body_and_reads_the_rest(
    session: AsyncSession,
    intake_role: Role,
    blob_store: FakeBlobStore,
    form: Form,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def upload(content: bytes) -> FormFileRead:
        return await forms_router.upload_form_file(
            role=intake_role,
            session=session,
            form_id=form.id,
            file=UploadFile(
                io.BytesIO(content),
                size=len(content),
                filename="quote.pdf",
                headers=Headers({"content-type": "application/pdf"}),
            ),
            field="quote",
        )

    uploaded = await upload(PDF)
    assert list(blob_store.objects) == [_key(form, uploaded.id)]

    monkeypatch.setattr(config, "TRACECAT__MAX_ATTACHMENT_SIZE_BYTES", 1024 * 1024)
    with pytest.raises(FormError) as exc_info:
        await upload(PDF + b"\x00" * (1024 * 1024))
    assert exc_info.value.code is FormErrorCode.FILE_TOO_LARGE
    assert str(exc_info.value) == "Files can be at most 1 MB"


async def test_pending_files_are_capped_per_uploader_per_form(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
    intake_role: Role,
    colleague_role: Role,
) -> None:
    uploads = [
        await _upload(files, form) for _ in range(MAX_PENDING_FILES_PER_UPLOADER)
    ]

    assert await _upload_error(files, form) is FormErrorCode.FILE_LIMIT_REACHED
    # Another uploader, and another form, have their own allowance.
    await _upload(FormFilesService(session, colleague_role), form)
    await _upload(files, await _online_form(session, intake_role, form.workspace_id))
    # A claimed file no longer counts.
    await submissions.submit(form, _values(quote=str(uploads[0].id)))
    await _upload(files, form)
    assert await _upload_error(files, form) is FormErrorCode.FILE_LIMIT_REACHED
    # Nor does an expired one, before a purge removes it.
    await _age(session, uploads[1].id, EXPIRED)
    await _upload(files, form)


async def test_upload_failures_leave_no_row_and_api_keys_are_recorded(
    session: AsyncSession,
    files: FormFilesService,
    blob_store: FakeBlobStore,
    api_key_role: Role,
    form: Form,
) -> None:
    uploaded = await _upload(FormFilesService(session, api_key_role), form)
    row = (await _rows(session))[uploaded.id]
    assert row.uploaded_by_user_id is None
    assert row.uploaded_by_service_account_id == api_key_role.service_account_id

    blob_store.fail_uploads = True
    assert await _upload_error(files, form) is FormErrorCode.FILE_UPLOAD_FAILED
    assert set(await _rows(session)) == {uploaded.id}


# ----- Expiry -----


async def test_expiry_purges_every_workspace_without_waiting_for_an_upload(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    blob_store: FakeBlobStore,
    temporal: FakeTemporal,
    form: Form,
    intake_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired, fresh, claimed = [await _upload(files, form) for _ in range(3)]
    await submissions.submit(form, _values(quote=str(claimed.id)))
    # Nothing uploads to an offline form or to the other workspace again.
    offline = await _online_form(session, intake_role, form.workspace_id)
    on_offline = await _upload(files, offline)
    offline.status = FormStatus.OFFLINE.value
    other_workspace = await create_organization_with_workspace(session, name="other")
    other_form = await _online_form(session, intake_role, other_workspace.id)
    foreign = await create_file(session, other_form)
    for file_id, age in [
        (expired.id, EXPIRED),
        (on_offline.id, EXPIRED),
        (foreign.id, EXPIRED),
        (claimed.id, PENDING_FILE_TTL + timedelta(days=30)),
        (fresh.id, PENDING_FILE_TTL - timedelta(minutes=1)),
    ]:
        await _age(session, file_id, age)
    # Two batches hold the three expired files.
    monkeypatch.setattr(storage, "EXPIRED_FILE_PURGE_BATCH", 2)
    monkeypatch.setattr(
        storage, "get_async_session_bypass_rls_context_manager", use_session(session)
    )
    stop = asyncio.Event()
    purge = storage.purge_expired_files

    async def purge_until_done(session: AsyncSession) -> int:
        if (purged := await purge(session)) < 2:
            stop.set()
        return purged

    monkeypatch.setattr(storage, "purge_expired_files", purge_until_done)

    async with asyncio.timeout(10):
        await storage.run_file_expiry(stop)

    kept = {fresh.id, claimed.id}
    assert set(await _rows(session)) == kept
    assert set(blob_store.objects) == {_key(form, file_id) for file_id in kept}
    assert file_blob_key(other_workspace.id, other_form.id, foreign.id) in (
        blob_store.deleted
    )


async def test_expiry_keeps_running_after_a_failed_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    passes: list[str] = []

    async def purge(session: AsyncSession) -> int:
        if not passes:
            passes.append("failed")
            raise TimeoutError
        passes.append("purged")
        stop.set()
        return 0

    monkeypatch.setattr(storage, "purge_expired_files", purge)
    monkeypatch.setattr(storage, "EXPIRED_FILE_PURGE_INTERVAL", timedelta(0))

    async with asyncio.timeout(10):
        await storage.run_file_expiry(stop)

    assert passes == ["failed", "purged"]


async def test_purge_removes_a_bounded_batch(
    session: AsyncSession,
    files: FormFilesService,
    blob_store: FakeBlobStore,
    form: Form,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "EXPIRED_FILE_PURGE_BATCH", 2)
    uploads = [await _upload(files, form)]
    # Statistics of a one-row table make Postgres pick a nested loop that
    # rescans a row-limiting subquery once per file row. A purge that limits
    # in a subquery of its DELETE then removes every expired file.
    await session.execute(text("ANALYZE form_file"))
    uploads += [await _upload(files, form) for _ in range(2)]
    for uploaded in uploads:
        await _age(session, uploaded.id, EXPIRED)

    assert await purge_expired_files(session) == 2

    remaining = await _rows(session)
    assert len(remaining) == 1
    # Only the blobs of the deleted rows are gone.
    assert set(blob_store.objects) == {_key(form, file_id) for file_id in remaining}


async def test_purge_keeps_the_row_of_a_blob_storage_failed_to_delete(
    session: AsyncSession,
    files: FormFilesService,
    blob_store: FakeBlobStore,
    form: Form,
) -> None:
    """A storage failure leaves the file's row, so the next purge retries it."""
    uploaded = await _upload(files, form)
    await _age(session, uploaded.id, EXPIRED)

    blob_store.fail_deletes = True
    assert await purge_expired_files(session) == 0
    assert set(await _rows(session)) == {uploaded.id}
    assert set(blob_store.objects) == {_key(form, uploaded.id)}

    blob_store.fail_deletes = False
    assert await purge_expired_files(session) == 1
    assert await _rows(session) == {}
    assert blob_store.objects == {}
    assert blob_store.deleted == [_key(form, uploaded.id)] * 2


# ----- Claiming on submit -----


async def test_submit_claims_files_and_expands_them_to_file_objects(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    quote = await _upload(files, form)
    extras = [await _upload(files, form, "extras") for _ in range(3)]
    hidden = await _upload(files, form, "conditional")
    extra_ids = [str(extra.id) for extra in extras]

    errors = await _claim_errors(submissions, form, extras=extra_ids)
    assert errors == {"extras": FormFieldErrorCode.TOO_MANY_FILES}
    # "conditional" is not visible, so its value is dropped and not claimed.
    receipt = await submissions.submit(
        form,
        _values(quote=str(quote.id), extras=extra_ids[:2], conditional=str(hidden.id)),
    )

    file_object = quote.model_dump(mode="json")
    assert file_object == {
        "id": str(quote.id),
        "name": "quote.pdf",
        "content_type": "application/pdf",
        "size": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(),
    }
    [submission] = (await session.execute(select(FormSubmission))).scalars().all()
    assert submission.answers["quote"] == file_object
    assert [item["id"] for item in submission.answers["extras"]] == extra_ids[:2]
    assert submission.answers["conditional"] is None
    # The run gets the same objects: no storage key and no URL.
    assert temporal.start_kwargs["payload"]["quote"] == file_object
    claimed = {row.id for row in (await _rows(session)).values() if row.submission_id}
    assert claimed == {quote.id, extras[0].id, extras[1].id}
    assert receipt.id == (await _rows(session))[quote.id].submission_id


async def test_submit_claims_only_the_callers_pending_uploads_to_that_field(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
    intake_role: Role,
    colleague_role: Role,
    reader_role: Role,
    svc_workspace: Workspace,
) -> None:
    mine = await _upload(files, form)
    to_extras = await _upload(files, form, "extras")
    expired = await _upload(files, form)
    elsewhere = await _upload(
        files, await _online_form(session, intake_role, form.workspace_id)
    )
    other_workspace = Workspace(
        name="Other workspace", organization_id=svc_workspace.organization_id
    )
    session.add(other_workspace)
    await session.commit()
    outsider = intake_role.model_copy(update={"workspace_id": other_workspace.id})
    foreign = await _upload(
        FormFilesService(session, outsider),
        await _online_form(session, outsider, other_workspace.id),
    )
    # Even a row that names this form is out of reach from another workspace.
    await session.execute(
        update(FormFile).where(FormFile.id == foreign.id).values(form_id=form.id)
    )
    await session.commit()
    claimed = await _upload(files, form)
    await submissions.submit(form, _values(quote=str(claimed.id)))
    # Expired but not yet purged: the claim refuses it.
    await _age(session, expired.id, EXPIRED)

    cases: list[tuple[Role, str, Any]] = [
        (colleague_role, "quote", str(mine.id)),
        # Reading submissions is no licence to submit someone else's upload.
        (reader_role, "quote", str(mine.id)),
        (intake_role, "quote", str(to_extras.id)),
        (intake_role, "quote", str(expired.id)),
        (intake_role, "quote", str(elsewhere.id)),
        (intake_role, "quote", str(foreign.id)),
        (intake_role, "quote", str(claimed.id)),
        (intake_role, "quote", str(uuid.uuid4())),
        (intake_role, "quote", "not-a-file-id"),
        (intake_role, "quote", 17),
        (intake_role, "quote", {"id": str(mine.id), "name": "quote.pdf"}),
        (intake_role, "extras", [str(to_extras.id), str(to_extras.id)]),
    ]
    for role, field_name, value in cases:
        service = FormSubmissionsService(session, role)
        errors = await _claim_errors(service, form, **{field_name: value})
        assert errors == {field_name: FormFieldErrorCode.INVALID_FILE}, value

    pending = {
        row.id for row in (await _rows(session)).values() if not row.submission_id
    }
    assert {mine.id, to_extras.id, elsewhere.id, foreign.id} <= pending


async def test_submit_checks_files_against_the_fields_current_accept(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    notes = await _upload(files, form, "extras", **TEXT_FILE)
    # The builder narrows the field after the upload.
    form.layout = [
        entry | {"accept": [".pdf"]} if entry.get("field") == "extras" else entry
        for entry in LAYOUT
    ]
    await session.commit()

    errors = await _claim_errors(submissions, form, extras=[str(notes.id)])

    assert errors == {"extras": FormFieldErrorCode.INVALID_FILE}


@pytest.mark.parametrize(
    ("settings", "max_size"),
    [
        ({"allowed_attachment_extensions": [".pdf"]}, None),
        ({"allowed_attachment_mime_types": ["application/pdf"]}, None),
        ({}, len(TEXT) - 1),
    ],
    ids=["extension", "content_type", "size"],
)
async def test_submit_checks_files_against_the_workspaces_current_policy(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, Any],
    max_size: int | None,
) -> None:
    notes = await _upload(files, form, "extras", **TEXT_FILE)
    # An admin narrows the workspace's attachment settings after the upload.
    await _set_workspace_settings(session, svc_workspace, settings)
    if max_size is not None:
        monkeypatch.setattr(config, "TRACECAT__MAX_ATTACHMENT_SIZE_BYTES", max_size)

    errors = await _claim_errors(submissions, form, extras=[str(notes.id)])

    assert errors == {"extras": FormFieldErrorCode.INVALID_FILE}


async def test_submit_reads_the_extension_as_the_upload_check_does(
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
) -> None:
    # The stored name keeps the requester's spaces; the extension ignores them.
    quote = await _upload(files, form, filename="quote.PDF ")

    await submissions.submit(form, _values(quote=str(quote.id)))

    temporal.start.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "claimed"),
    [
        (RuntimeError("temporal is down"), False),
        # The run may exist and download the file.
        (TimeoutError("Timed out while dispatching workflow"), True),
    ],
)
async def test_start_failure_releases_the_files_unless_the_run_may_exist(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
    error: Exception,
    claimed: bool,
) -> None:
    uploaded = await _upload(files, form)
    form_id = form.id
    values = _values(quote=str(uploaded.id))
    temporal.start.side_effect = error

    with pytest.raises(FormError):
        await submissions.submit(form, values)

    assert ((await _rows(session))[uploaded.id].submission_id is not None) is claimed
    if not claimed:
        # The requester can submit the same upload again.
        temporal.start.side_effect = None
        form = (
            await session.execute(select(Form).where(Form.id == form_id))
        ).scalar_one()
        receipt = await submissions.submit(form, values)
        assert (await _rows(session))[uploaded.id].submission_id == receipt.id


# ----- Download -----


async def test_pending_files_are_private_to_their_uploader(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    temporal: FakeTemporal,
    form: Form,
    intake_role: Role,
    colleague_role: Role,
    reader_role: Role,
    svc_workspace: Workspace,
) -> None:
    uploaded = await _upload(files, form)
    executor = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )

    async def readable(role: Role) -> tuple[bool, bool]:
        service = FormFilesService(session, role)
        download = await service.get_download(form.id, uploaded.id)
        content = await service.read_content(uploaded.id)
        return download is not None, content is not None

    # Unclaimed: form:submission:read is not enough.
    assert await readable(intake_role) == (True, True)
    for role in (colleague_role, reader_role, executor):
        assert await readable(role) == (False, False)

    await submissions.submit(form, _values(quote=str(uploaded.id)))

    for role in (intake_role, reader_role, executor):
        assert await readable(role) == (True, True)
    assert await readable(colleague_role) == (False, False)


async def test_downloads_are_scoped_to_the_form_and_the_workspace(
    session: AsyncSession,
    files: FormFilesService,
    form: Form,
    intake_role: Role,
    colleague_role: Role,
    svc_workspace: Workspace,
) -> None:
    uploaded = await _upload(files, form)
    other_form = await _online_form(session, intake_role, form.workspace_id)
    other_workspace = Workspace(
        name="Other workspace", organization_id=svc_workspace.organization_id
    )
    session.add(other_workspace)
    await session.commit()
    outsider = FormFilesService(
        session, intake_role.model_copy(update={"workspace_id": other_workspace.id})
    )

    assert await files.get_download(other_form.id, uploaded.id) is None
    assert await outsider.get_download(form.id, uploaded.id) is None
    assert await outsider.read_content(uploaded.id) is None
    for role, file_id in ((colleague_role, uploaded.id), (intake_role, uuid.uuid4())):
        with pytest.raises(HTTPException) as exc_info:
            await forms_router.download_form_file(
                role=role, session=session, form_id=form.id, file_id=file_id
            )
        assert (exc_info.value.status_code, exc_info.value.detail) == (
            404,
            "File not found",
        )


# ----- Deletion -----


@pytest.mark.parametrize("owner", ["submission", "form", "workflow"])
async def test_deleting_an_owner_deletes_its_rows_and_blobs(
    session: AsyncSession,
    builder_role: Role,
    blob_store: FakeBlobStore,
    svc_workspace: Workspace,
    owner: str,
) -> None:
    form = await create_form(session, await create_workflow(session, svc_workspace.id))
    other_form = await create_form(
        session, await create_workflow(session, svc_workspace.id, title="Other")
    )
    submission = await create_submission(session, form)
    other_submission = await create_submission(session, form)
    session.add(
        FormView(
            workspace_id=form.workspace_id,
            form_id=form.id,
            window_start=datetime.now(UTC),
        )
    )
    claimed = await create_file(session, form, submission)
    pending = await create_file(session, form)
    kept = [
        await create_file(session, other_form),
        await create_file(session, form, other_submission),
    ]

    match owner:
        case "submission":
            await FormSubmissionsService(session, builder_role).delete_submission(
                submission
            )
            gone = [claimed]
            kept.append(pending)
        case "form":
            await FormsService(session, builder_role).delete_form(form)
            gone = [claimed, pending, kept.pop()]
        case _:
            await WorkflowsManagementService(
                session, role=builder_role
            ).delete_workflow(WorkflowUUID.new(form.workflow_id))
            gone = [claimed, pending, kept.pop()]

    deleted = {_key(form, file.id) for file in gone}
    assert set(blob_store.deleted) == deleted
    remaining = (await session.execute(select(FormFile.id))).scalars().all()
    assert set(remaining) == {file.id for file in kept}
    if owner != "submission":
        for model in (Form, FormSubmission, FormView):
            count = (
                select(func.count())
                .select_from(model)
                .where(model.workspace_id == form.workspace_id)
            )
            assert await session.scalar(count) == (1 if model is Form else 0)


@pytest.mark.parametrize("owner", ["form", "workflow"])
async def test_deleting_a_form_locks_it_before_collecting_its_file_keys(
    session: AsyncSession,
    builder_role: Role,
    svc_workspace: Workspace,
    owner: str,
) -> None:
    # An upload holds a FOR NO KEY UPDATE lock on its form while it adds its
    # row, so once the deletion holds the form, no upload can add a key it misses.
    form = await create_form(session, await create_workflow(session, svc_workspace.id))
    statements: list[str] = []

    def record(*args: Any) -> None:
        statements.append(args[2])

    connection = (await session.connection()).sync_connection
    assert connection is not None
    event.listen(connection, "before_cursor_execute", record)
    try:
        if owner == "form":
            await FormsService(session, builder_role).delete_form(form)
        else:
            await WorkflowsManagementService(
                session, role=builder_role
            ).delete_workflow(WorkflowUUID.new(form.workflow_id))
    finally:
        event.remove(connection, "before_cursor_execute", record)

    locked = [
        i
        for i, sql in enumerate(statements)
        if "FROM form " in sql and "FOR UPDATE" in sql
    ]
    collected = [i for i, sql in enumerate(statements) if "FROM form_file" in sql]
    assert locked and collected and locked[0] < collected[0]


async def test_an_upload_to_a_form_deleted_meanwhile_stores_nothing(
    session: AsyncSession,
    files: FormFilesService,
    form: Form,
    blob_store: FakeBlobStore,
) -> None:
    # The upload resolved the form, then waited for its lock while it was deleted.
    await session.execute(delete(Form).where(Form.id == form.id))
    await session.commit()

    assert await _upload_error(files, form) is FormErrorCode.NOT_FOUND
    assert (await _rows(session)) == {}
    assert blob_store.objects == {}


async def test_deleting_a_workflow_without_a_form_touches_no_blob(
    session: AsyncSession,
    builder_role: Role,
    blob_store: FakeBlobStore,
    svc_workspace: Workspace,
) -> None:
    workflow = await create_workflow(session, svc_workspace.id, title="Plain")

    await WorkflowsManagementService(session, role=builder_role).delete_workflow(
        WorkflowUUID.new(workflow.id)
    )

    assert blob_store.deleted == []


async def test_blob_deletion_is_best_effort_and_covers_every_key(
    blob_store: FakeBlobStore,
) -> None:
    # More keys than one batch, with a failure early on.
    keys = ["forms/w/f/fail", *(f"forms/w/f/{i}" for i in range(501))]

    # A failure does not stop the rest, no key is left behind, and the
    # caller learns which keys failed.
    assert await storage.delete_file_blobs(keys) == {"forms/w/f/fail"}
    assert sorted(blob_store.deleted) == sorted(keys)


async def test_an_upload_that_races_its_forms_deletion_leaves_no_blob(
    session: AsyncSession,
    files: FormFilesService,
    form: Form,
    blob_store: FakeBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = blob_store.upload_file

    async def upload_while_the_form_is_deleted(*args: Any, **kwargs: Any) -> None:
        # The deletion snapshots its blob keys before this blob exists.
        await session.execute(delete(Form).where(Form.id == form.id))
        await session.commit()
        await store(*args, **kwargs)

    monkeypatch.setattr(blob, "upload_file", upload_while_the_form_is_deleted)

    assert await _upload_error(files, form) is FormErrorCode.FILE_UPLOAD_FAILED
    assert blob_store.objects == {}


async def test_an_upload_whose_storage_answer_is_lost_leaves_no_blob(
    session: AsyncSession,
    files: FormFilesService,
    form: Form,
    blob_store: FakeBlobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = blob_store.upload_file

    async def stored_then_timed_out(*args: Any, **kwargs: Any) -> None:
        # Storage kept the object, but its answer never arrived.
        await store(*args, **kwargs)
        raise TimeoutError

    monkeypatch.setattr(blob, "upload_file", stored_then_timed_out)

    assert await _upload_error(files, form) is FormErrorCode.FILE_UPLOAD_FAILED
    assert (await _rows(session)) == {}
    assert blob_store.objects == {}


def test_file_names_follow_the_configured_length_limit() -> None:
    # Validation enforces TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH, which may
    # exceed 255, so the column must not cap names on its own.
    column_type = FormFile.__table__.c.name.type
    assert isinstance(column_type, String)
    assert column_type.length is None
