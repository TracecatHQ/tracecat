"""Tests for form file uploads: upload, claiming on submit and download.

Blob storage is replaced by an in-memory fake patched onto
``tracecat.storage.blob``, so these tests need no MinIO.
"""

import base64
import hashlib
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from tests._forms_utils import (
    create_form,
    create_org_user,
    create_workflow,
    publish_workflow,
)
from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import (
    Form,
    FormFile,
    FormSubmission,
    ServiceAccount,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatException
from tracecat.forms import router as forms_router
from tracecat.forms.constants import (
    MAX_FILES_PER_FIELD,
    MAX_PENDING_FILES_PER_UPLOADER,
    PENDING_FILE_TTL,
)
from tracecat.forms.enums import FormErrorCode, FormFieldErrorCode, FormStatus
from tracecat.forms.exceptions import FormError
from tracecat.forms.files import FormFilesService, file_blob_key, matches_accept
from tracecat.forms.schemas import FormFileRead, FormSubmissionCreate
from tracecat.forms.submissions import FormSubmissionsService
from tracecat.storage import blob
from tracecat.workflow.executions.service import WorkflowExecutionsService

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

INTAKE_SCOPES = frozenset({"form:read", "form:submit"})
READER_SCOPES = INTAKE_SCOPES | {"form:submission:read"}

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
TEXT = b"Quote for one laptop.\n"

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


@dataclass
class FakeBlobStore:
    """In-memory stand-in for the functions of ``tracecat.storage.blob``."""

    objects: dict[str, bytes] = field(default_factory=dict)
    content_types: dict[str, str | None] = field(default_factory=dict)
    buckets: set[str] = field(default_factory=set)
    presigned: list[dict[str, Any]] = field(default_factory=list)
    fail_uploads: bool = False

    async def upload_file(
        self,
        content: bytes,
        key: str,
        bucket: str,
        content_type: str | None = None,
        *,
        redact_log_identifiers: bool = False,
    ) -> None:
        if self.fail_uploads:
            raise blob.StorageUploadError(error_code="SlowDown")
        self.objects[key] = content
        self.content_types[key] = content_type
        self.buckets.add(bucket)

    async def download_file(
        self, key: str, bucket: str, *, redact_log_identifiers: bool = False
    ) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError
        return self.objects[key]

    async def delete_file(
        self, key: str, bucket: str, *, redact_log_identifiers: bool = False
    ) -> None:
        self.objects.pop(key, None)

    async def generate_presigned_download_url(self, **kwargs: Any) -> str:
        self.presigned.append(kwargs)
        return f"https://storage.example.com/{kwargs['key']}?signature=synthetic"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeBlobStore:
    store = FakeBlobStore()
    for name in (
        "upload_file",
        "download_file",
        "delete_file",
        "generate_presigned_download_url",
    ):
        monkeypatch.setattr(blob, name, getattr(store, name))
    return store


@pytest.fixture
def temporal(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stands in for ``WorkflowExecutionsService.connect``. Returns the service."""
    service = AsyncMock(spec=WorkflowExecutionsService)
    service.query_executions.return_value = []
    monkeypatch.setattr(
        WorkflowExecutionsService, "connect", AsyncMock(return_value=service)
    )
    return service


@pytest.fixture
async def requester(session: AsyncSession, svc_workspace: Workspace) -> User:
    return await create_org_user(
        session, svc_workspace.organization_id, email="maya.okafor@example.com"
    )


@pytest.fixture
def intake_role(svc_workspace: Workspace, requester: User) -> Role:
    """A requester holding nothing but the Intake only preset's scopes."""
    return Role(
        type="user",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=requester.id,
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )


@pytest.fixture
async def colleague_role(
    session: AsyncSession, svc_workspace: Workspace, intake_role: Role
) -> Role:
    colleague = await create_org_user(
        session, svc_workspace.organization_id, email="arjun.rao@example.com"
    )
    return intake_role.model_copy(update={"user_id": colleague.id})


@pytest.fixture
def reader_role(colleague_role: Role) -> Role:
    return colleague_role.model_copy(update={"scopes": READER_SCOPES})


async def _online_form(
    session: AsyncSession, role: Role, workspace_id: uuid.UUID, *, title: str
) -> Form:
    workflow = await create_workflow(session, workspace_id, title=title)
    workflow_role = role.model_copy(update={"workspace_id": workspace_id})
    await publish_workflow(session, workflow_role, workflow, EXPECTS)
    return await create_form(session, workflow, layout=LAYOUT)


@pytest.fixture
async def form(
    session: AsyncSession, intake_role: Role, svc_workspace: Workspace
) -> Form:
    return await _online_form(session, intake_role, svc_workspace.id, title="Purchase")


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


def _values(values: dict[str, Any]) -> FormSubmissionCreate:
    return FormSubmissionCreate(values=values)


async def _submit_errors(
    service: FormSubmissionsService, form: Form, values: dict[str, Any]
) -> dict[str, FormFieldErrorCode]:
    with pytest.raises(FormError) as exc_info:
        await service.submit(form, FormSubmissionCreate(values=values))
    error = exc_info.value
    assert error.code is FormErrorCode.FORM_VALIDATION_ERROR
    return {item.field: item.code for item in error.errors or []}


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


# ----- Pure helpers -----


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
    assert (
        matches_accept(accept, extension=extension, content_type=content_type)
        is expected
    )


def test_blob_key_is_built_from_ids_only() -> None:
    workspace_id, form_id, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    assert (
        file_blob_key(workspace_id, form_id, file_id)
        == f"forms/{workspace_id}/{form_id}/{file_id}"
    )


def test_limits() -> None:
    assert MAX_FILES_PER_FIELD == 10
    assert MAX_PENDING_FILES_PER_UPLOADER == 20
    assert PENDING_FILE_TTL == timedelta(hours=24)


# ----- Upload -----


async def test_upload_stores_a_pending_file_under_an_id_only_key(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    requester: User,
) -> None:
    uploaded = await _upload(files, form, filename="Q3 quote (final).pdf")

    assert uploaded.model_dump() == {
        "id": uploaded.id,
        "name": "Q3 quote (final).pdf",
        "content_type": "application/pdf",
        "size": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(),
    }
    key = f"forms/{form.workspace_id}/{form.id}/{uploaded.id}"
    assert store.objects == {key: PDF}
    assert store.content_types == {key: "application/pdf"}
    assert store.buckets == {config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS}
    [row] = (await _rows(session)).values()
    assert row.form_id == form.id
    assert row.field == "quote"
    assert row.submission_id is None
    assert row.uploaded_by_user_id == requester.id
    assert row.uploaded_by_service_account_id is None


async def test_upload_ignores_the_declared_type_when_the_bytes_disagree(
    session: AsyncSession, files: FormFilesService, store: FakeBlobStore, form: Form
) -> None:
    code = await _upload_error(
        files, form, filename="quote.pdf", content_type="application/pdf", content=TEXT
    )

    assert code is FormErrorCode.FILE_TYPE_NOT_ALLOWED
    assert store.objects == {}
    assert await _rows(session) == {}


async def test_upload_checks_the_bytes_even_if_the_workspace_turned_that_off(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    svc_workspace: Workspace,
) -> None:
    await session.execute(
        update(Workspace)
        .where(Workspace.id == svc_workspace.id)
        .values(settings={"validate_attachment_magic_number": False})
    )
    await session.commit()

    code = await _upload_error(files, form, content=TEXT)

    assert code is FormErrorCode.FILE_TYPE_NOT_ALLOWED


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"filename": "../quote.pdf"}, FormErrorCode.FILE_INVALID),
        ({"filename": "dir/quote.pdf"}, FormErrorCode.FILE_INVALID),
        ({"filename": ""}, FormErrorCode.FILE_INVALID),
        ({"content": b""}, FormErrorCode.FILE_INVALID),
        ({"filename": "quote.exe"}, FormErrorCode.FILE_TYPE_NOT_ALLOWED),
        (
            {"content_type": "application/x-msdownload"},
            FormErrorCode.FILE_TYPE_NOT_ALLOWED,
        ),
        # Allowed by the workspace, not by the field's accept.
        (
            {"filename": "quote.txt", "content_type": "text/plain", "content": TEXT},
            FormErrorCode.FILE_TYPE_NOT_ALLOWED,
        ),
    ],
)
async def test_upload_rejects_files_that_fail_validation(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    kwargs: dict[str, Any],
    expected: FormErrorCode,
) -> None:
    assert await _upload_error(files, form, **kwargs) is expected
    assert store.objects == {}
    assert await _rows(session) == {}


async def test_upload_allows_only_what_the_workspace_and_the_field_both_allow(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    svc_workspace: Workspace,
) -> None:
    # "extras" has no accept, so it takes what the workspace allows.
    text = {"filename": "notes.txt", "content_type": "text/plain", "content": TEXT}
    await _upload(files, form, "extras", **text)
    await session.execute(
        update(Workspace)
        .where(Workspace.id == svc_workspace.id)
        .values(
            settings={
                "allowed_attachment_extensions": [".pdf"],
                "allowed_attachment_mime_types": ["application/pdf"],
            }
        )
    )
    await session.commit()

    code = await _upload_error(files, form, "extras", **text)

    assert code is FormErrorCode.FILE_TYPE_NOT_ALLOWED
    await _upload(files, form, "extras")
    assert len(store.objects) == 2


async def test_upload_rejects_a_file_over_the_attachment_size_limit(
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__MAX_ATTACHMENT_SIZE_BYTES", len(PDF) - 1)

    assert await _upload_error(files, form) is FormErrorCode.FILE_TOO_LARGE
    assert store.objects == {}


@pytest.mark.parametrize("field_name", ["notes", "internal", "missing"])
async def test_upload_needs_a_file_field_of_the_form(
    files: FormFilesService, store: FakeBlobStore, form: Form, field_name: str
) -> None:
    code = await _upload_error(files, form, field_name)

    assert code is FormErrorCode.FILE_FIELD_INVALID
    assert store.objects == {}


async def test_upload_to_a_conditional_file_field_is_allowed(
    files: FormFilesService, store: FakeBlobStore, form: Form
) -> None:
    uploaded = await _upload(files, form, "conditional")

    assert len(store.objects) == 1
    assert uploaded.name == "quote.pdf"


async def test_upload_needs_an_online_form(
    session: AsyncSession, files: FormFilesService, store: FakeBlobStore, form: Form
) -> None:
    form.status = FormStatus.OFFLINE.value
    await session.commit()

    assert await _upload_error(files, form) is FormErrorCode.FORM_OFFLINE
    assert store.objects == {}


async def test_upload_route_needs_form_submit(
    session: AsyncSession, intake_role: Role, store: FakeBlobStore, form: Form
) -> None:
    viewer = intake_role.model_copy(update={"scopes": frozenset({"form:read"})})

    with pytest.raises(TracecatException):
        await forms_router.upload_form_file(
            role=viewer,
            session=session,
            form_id=form.id,
            file=UploadFile(io.BytesIO(PDF), filename="quote.pdf"),
            field="quote",
        )

    assert store.objects == {}


async def test_upload_route_stores_the_file_and_maps_errors(
    session: AsyncSession, intake_role: Role, store: FakeBlobStore, form: Form
) -> None:
    def upload_file(filename: str) -> UploadFile:
        return UploadFile(
            io.BytesIO(PDF),
            filename=filename,
            headers=Headers({"content-type": "application/pdf"}),
        )

    uploaded = await forms_router.upload_form_file(
        role=intake_role,
        session=session,
        form_id=form.id,
        file=upload_file("quote.pdf"),
        field="quote",
    )
    assert list(store.objects) == [f"forms/{form.workspace_id}/{form.id}/{uploaded.id}"]

    with pytest.raises(HTTPException) as exc_info:
        await forms_router.upload_form_file(
            role=intake_role,
            session=session,
            form_id=form.id,
            file=upload_file("quote.exe"),
            field="quote",
        )
    assert exc_info.value.status_code == 415
    detail: Any = exc_info.value.detail
    assert detail["code"] == "file_type_not_allowed"


async def test_read_upload_stops_at_the_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__MAX_ATTACHMENT_SIZE_BYTES", 2 * 1024 * 1024)
    reads: list[int] = []

    class CountingUpload(UploadFile):
        async def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return await super().read(size)

    oversized = CountingUpload(io.BytesIO(b"x" * (10 * 1024 * 1024)))

    with pytest.raises(FormError) as exc_info:
        await forms_router.read_upload(oversized)

    assert exc_info.value.code is FormErrorCode.FILE_TOO_LARGE
    # Three 1 MB chunks pass a 2 MB limit; the other seven are never read.
    assert reads == [1024 * 1024] * 3

    exact = UploadFile(io.BytesIO(b"x" * (2 * 1024 * 1024)))
    assert len(await forms_router.read_upload(exact)) == 2 * 1024 * 1024


async def test_pending_files_are_capped_per_uploader_per_form(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    intake_role: Role,
    colleague_role: Role,
) -> None:
    for _ in range(MAX_PENDING_FILES_PER_UPLOADER):
        await _upload(files, form)

    assert await _upload_error(files, form) is FormErrorCode.FILE_LIMIT_REACHED
    assert len(store.objects) == MAX_PENDING_FILES_PER_UPLOADER
    # Another uploader, and another form, have their own allowance.
    await _upload(FormFilesService(session, colleague_role), form)
    other_form = await _online_form(
        session, intake_role, form.workspace_id, title="Other"
    )
    await _upload(files, other_form)


async def test_claimed_files_do_not_count_as_pending(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    uploads = [
        await _upload(files, form) for _ in range(MAX_PENDING_FILES_PER_UPLOADER)
    ]
    await submissions.submit(form, _values({"quote": str(uploads[0].id)}))

    await _upload(files, form)


async def test_storage_failure_leaves_no_row(
    session: AsyncSession, files: FormFilesService, store: FakeBlobStore, form: Form
) -> None:
    store.fail_uploads = True

    assert await _upload_error(files, form) is FormErrorCode.FILE_UPLOAD_FAILED
    assert await _rows(session) == {}


async def test_upload_by_an_api_key_records_the_service_account(
    session: AsyncSession, svc_workspace: Workspace, store: FakeBlobStore, form: Form
) -> None:
    account = ServiceAccount(
        organization_id=svc_workspace.organization_id,
        workspace_id=svc_workspace.id,
        name="Intake bot",
    )
    session.add(account)
    await session.commit()
    role = Role(
        type="service_account",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        service_account_id=account.id,
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )

    uploaded = await _upload(FormFilesService(session, role), form)

    row = (await _rows(session))[uploaded.id]
    assert row.uploaded_by_user_id is None
    assert row.uploaded_by_service_account_id == account.id


# ----- Expiry -----


async def test_upload_deletes_expired_pending_files_and_their_blobs(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    expired = await _upload(files, form)
    fresh = await _upload(files, form)
    claimed = await _upload(files, form)
    await submissions.submit(form, _values({"quote": str(claimed.id)}))
    await _age(session, expired.id, PENDING_FILE_TTL + timedelta(minutes=1))
    await _age(session, claimed.id, PENDING_FILE_TTL + timedelta(days=30))
    await _age(session, fresh.id, PENDING_FILE_TTL - timedelta(minutes=1))

    newest = await _upload(files, form)

    assert set(await _rows(session)) == {fresh.id, claimed.id, newest.id}
    assert set(store.objects) == {
        file_blob_key(form.workspace_id, form.id, file_id)
        for file_id in (fresh.id, claimed.id, newest.id)
    }


async def test_submit_deletes_expired_pending_files_and_cannot_claim_them(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    expired = await _upload(files, form)
    await _age(session, expired.id, PENDING_FILE_TTL + timedelta(minutes=1))

    errors = await _submit_errors(submissions, form, {"quote": str(expired.id)})

    assert errors == {"quote": FormFieldErrorCode.INVALID_FILE}
    assert await _rows(session) == {}
    assert store.objects == {}


async def test_purge_is_scoped_to_the_form(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    intake_role: Role,
) -> None:
    other_form = await _online_form(
        session, intake_role, form.workspace_id, title="Other"
    )
    elsewhere = await _upload(files, other_form)
    await _age(session, elsewhere.id, PENDING_FILE_TTL + timedelta(minutes=1))

    await files.purge_expired(form.id)

    assert set(await _rows(session)) == {elsewhere.id}
    assert len(store.objects) == 1


# ----- Claiming on submit -----


async def test_submit_claims_the_file_and_expands_it_to_a_file_object(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    uploaded = await _upload(files, form)

    receipt = await submissions.submit(form, _values({"quote": str(uploaded.id)}))

    file_object = {
        "id": str(uploaded.id),
        "name": "quote.pdf",
        "content_type": "application/pdf",
        "size": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(),
    }
    [submission] = (await session.execute(select(FormSubmission))).scalars().all()
    assert submission.answers["quote"] == file_object
    # The run gets the same object: no storage key and no URL.
    start = temporal.create_workflow_execution_wait_for_start
    assert start.await_args is not None
    assert start.await_args.kwargs["payload"]["quote"] == file_object
    assert (await _rows(session))[uploaded.id].submission_id == receipt.id


async def test_submit_claims_a_list_of_files_up_to_max_files(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    uploads = [await _upload(files, form, "extras") for _ in range(3)]
    file_ids = [str(uploaded.id) for uploaded in uploads]

    errors = await _submit_errors(submissions, form, {"extras": file_ids})

    assert errors == {"extras": FormFieldErrorCode.TOO_MANY_FILES}
    assert all(row.submission_id is None for row in (await _rows(session)).values())

    receipt = await submissions.submit(form, _values({"extras": file_ids[:2]}))

    [submission] = (await session.execute(select(FormSubmission))).scalars().all()
    assert [item["id"] for item in submission.answers["extras"]] == file_ids[:2]
    rows = await _rows(session)
    assert rows[uploads[0].id].submission_id == receipt.id
    assert rows[uploads[1].id].submission_id == receipt.id
    assert rows[uploads[2].id].submission_id is None


@pytest.mark.parametrize(
    "value",
    [
        "7a1c2e3f-4b5d-4e6f-8a9b-0c1d2e3f4a5b",
        "not-a-file-id",
        17,
        {"id": "7a1c2e3f-4b5d-4e6f-8a9b-0c1d2e3f4a5b", "name": "quote.pdf"},
    ],
)
async def test_submit_rejects_values_that_are_not_ids_of_uploaded_files(
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
    value: Any,
) -> None:
    errors = await _submit_errors(submissions, form, {"quote": value})

    assert errors == {"quote": FormFieldErrorCode.INVALID_FILE}
    temporal.create_workflow_execution_wait_for_start.assert_not_awaited()


async def test_submit_rejects_another_requesters_file(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
    colleague_role: Role,
    reader_role: Role,
) -> None:
    uploaded = await _upload(files, form)

    # Neither a plain requester nor one who can read submissions.
    for role in (colleague_role, reader_role):
        errors = await _submit_errors(
            FormSubmissionsService(session, role), form, {"quote": str(uploaded.id)}
        )
        assert errors == {"quote": FormFieldErrorCode.INVALID_FILE}

    assert (await _rows(session))[uploaded.id].submission_id is None


async def test_submit_rejects_a_file_from_another_form(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
    intake_role: Role,
) -> None:
    other_form = await _online_form(
        session, intake_role, form.workspace_id, title="Other"
    )
    elsewhere = await _upload(files, other_form)

    errors = await _submit_errors(submissions, form, {"quote": str(elsewhere.id)})

    assert errors == {"quote": FormFieldErrorCode.INVALID_FILE}
    assert (await _rows(session))[elsewhere.id].submission_id is None


async def test_submit_rejects_a_file_from_another_workspace(
    session: AsyncSession,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
    intake_role: Role,
    svc_workspace: Workspace,
) -> None:
    other_workspace = Workspace(
        name="Other workspace", organization_id=svc_workspace.organization_id
    )
    session.add(other_workspace)
    await session.commit()
    other_role = intake_role.model_copy(update={"workspace_id": other_workspace.id})
    other_form = await _online_form(
        session, other_role, other_workspace.id, title="Other"
    )
    elsewhere = await _upload(FormFilesService(session, other_role), other_form)
    # Even a row that names this form is out of reach from another workspace.
    await session.execute(
        update(FormFile).where(FormFile.id == elsewhere.id).values(form_id=form.id)
    )
    await session.commit()

    errors = await _submit_errors(submissions, form, {"quote": str(elsewhere.id)})

    assert errors == {"quote": FormFieldErrorCode.INVALID_FILE}
    assert (await _rows(session))[elsewhere.id].submission_id is None


async def test_submit_rejects_a_file_uploaded_to_another_field(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    uploaded = await _upload(files, form, "extras")

    errors = await _submit_errors(submissions, form, {"quote": str(uploaded.id)})

    assert errors == {"quote": FormFieldErrorCode.INVALID_FILE}


async def test_a_file_is_claimed_once(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    uploaded = await _upload(files, form)
    values = {"quote": str(uploaded.id)}
    receipt = await submissions.submit(form, _values(values))

    errors = await _submit_errors(submissions, form, values)

    assert errors == {"quote": FormFieldErrorCode.INVALID_FILE}
    assert (await _rows(session))[uploaded.id].submission_id == receipt.id
    assert len((await session.execute(select(FormSubmission))).scalars().all()) == 1


async def test_a_file_cannot_be_listed_twice_in_one_field(
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    uploaded = await _upload(files, form, "extras")

    errors = await _submit_errors(
        submissions, form, {"extras": [str(uploaded.id), str(uploaded.id)]}
    )

    assert errors == {"extras": FormFieldErrorCode.INVALID_FILE}


async def test_start_failure_leaves_the_file_unclaimed(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    uploaded = await _upload(files, form)
    form_id = form.id
    values = _values({"quote": str(uploaded.id)})
    start = temporal.create_workflow_execution_wait_for_start
    start.side_effect = RuntimeError("temporal is down")

    with pytest.raises(FormError) as exc_info:
        await submissions.submit(form, values)

    assert exc_info.value.code is FormErrorCode.WORKFLOW_START_FAILED
    assert (await _rows(session))[uploaded.id].submission_id is None
    # The requester can submit the same upload again.
    start.side_effect = None
    form = (await session.execute(select(Form).where(Form.id == form_id))).scalar_one()
    receipt = await submissions.submit(form, values)
    assert (await _rows(session))[uploaded.id].submission_id == receipt.id


async def test_file_fields_are_optional_when_their_input_has_a_default(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
) -> None:
    hidden = await _upload(files, form, "conditional")

    # "conditional" is not visible, so its value is dropped and not claimed.
    await submissions.submit(form, _values({"conditional": str(hidden.id)}))

    [submission] = (await session.execute(select(FormSubmission))).scalars().all()
    assert submission.answers["quote"] is None
    assert submission.answers["extras"] == []
    assert submission.answers["conditional"] is None
    assert (await _rows(session))[hidden.id].submission_id is None


# ----- Download -----


async def test_uploader_gets_a_forced_download_url(
    files: FormFilesService, store: FakeBlobStore, form: Form
) -> None:
    uploaded = await _upload(files, form, filename="Q3 quote.pdf")

    download = await files.get_download(form.id, uploaded.id)

    assert download is not None
    key = f"forms/{form.workspace_id}/{form.id}/{uploaded.id}"
    assert download.model_dump() == {
        "download_url": f"https://storage.example.com/{key}?signature=synthetic",
        "file_name": "Q3 quote.pdf",
        "content_type": "application/pdf",
    }
    assert store.presigned == [
        {
            "key": key,
            "bucket": config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS,
            "force_download": True,
            "override_content_type": "application/octet-stream",
            "redact_log_identifiers": True,
            # The name is a response header. It is not in the key.
            "download_filename": "Q3 quote.pdf",
        }
    ]


async def test_a_pending_file_is_downloadable_by_its_uploader_only(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
    reader_role: Role,
    svc_workspace: Workspace,
) -> None:
    uploaded = await _upload(files, form)
    reader = FormFilesService(session, reader_role)
    executor = FormFilesService(
        session,
        Role(
            type="service",
            service_id="tracecat-executor",
            workspace_id=svc_workspace.id,
            organization_id=svc_workspace.organization_id,
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
        ),
    )

    # Unclaimed: form:submission:read is not enough, on either route.
    assert await reader.get_download(form.id, uploaded.id) is None
    assert await reader.read_content(uploaded.id) is None
    assert await executor.read_content(uploaded.id) is None
    with pytest.raises(HTTPException) as exc_info:
        await forms_router.download_form_file(
            role=reader_role, session=session, form_id=form.id, file_id=uploaded.id
        )
    assert exc_info.value.status_code == 404
    assert store.presigned == []
    assert await files.get_download(form.id, uploaded.id) is not None

    await submissions.submit(form, _values({"quote": str(uploaded.id)}))

    assert await reader.get_download(form.id, uploaded.id) is not None
    assert await executor.read_content(uploaded.id) is not None


async def test_download_is_for_the_uploader_or_a_submission_reader(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
    colleague_role: Role,
    reader_role: Role,
) -> None:
    uploaded = await _upload(files, form)
    await submissions.submit(form, _values({"quote": str(uploaded.id)}))

    colleague = FormFilesService(session, colleague_role)
    reader = FormFilesService(session, reader_role)

    assert await colleague.get_download(form.id, uploaded.id) is None
    assert await colleague.read_content(uploaded.id) is None
    assert await reader.get_download(form.id, uploaded.id) is not None
    assert await files.get_download(form.id, uploaded.id) is not None
    assert len(store.presigned) == 2


async def test_download_is_scoped_to_the_form_and_the_workspace(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    intake_role: Role,
    reader_role: Role,
    svc_workspace: Workspace,
) -> None:
    uploaded = await _upload(files, form)
    other_form = await _online_form(
        session, intake_role, form.workspace_id, title="Other"
    )
    other_workspace = Workspace(
        name="Other workspace", organization_id=svc_workspace.organization_id
    )
    session.add(other_workspace)
    await session.commit()
    outsider = FormFilesService(
        session, reader_role.model_copy(update={"workspace_id": other_workspace.id})
    )

    assert await files.get_download(other_form.id, uploaded.id) is None
    assert await files.get_download(form.id, uuid.uuid4()) is None
    assert await outsider.get_download(form.id, uploaded.id) is None
    assert await outsider.read_content(uploaded.id) is None
    assert store.presigned == []


async def test_download_route_answers_404_for_unknown_and_unauthorised_ids(
    session: AsyncSession,
    files: FormFilesService,
    store: FakeBlobStore,
    form: Form,
    intake_role: Role,
    colleague_role: Role,
) -> None:
    uploaded = await _upload(files, form)

    download = await forms_router.download_form_file(
        role=intake_role, session=session, form_id=form.id, file_id=uploaded.id
    )
    assert download.file_name == "quote.pdf"

    for role, file_id in ((colleague_role, uploaded.id), (intake_role, uuid.uuid4())):
        with pytest.raises(HTTPException) as exc_info:
            await forms_router.download_form_file(
                role=role, session=session, form_id=form.id, file_id=file_id
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "File not found"


async def test_read_content_returns_the_verified_bytes(
    session: AsyncSession,
    files: FormFilesService,
    submissions: FormSubmissionsService,
    store: FakeBlobStore,
    temporal: AsyncMock,
    form: Form,
    reader_role: Role,
) -> None:
    uploaded = await _upload(files, form)
    await submissions.submit(form, _values({"quote": str(uploaded.id)}))
    reader = FormFilesService(session, reader_role)
    key = file_blob_key(form.workspace_id, form.id, uploaded.id)

    content = await reader.read_content(uploaded.id)

    assert content is not None
    assert base64.b64decode(content.content_base64) == PDF
    assert content.model_dump(exclude={"content_base64"}) == uploaded.model_dump()

    store.objects[key] = b"tampered"
    with pytest.raises(TracecatException):
        await reader.read_content(uploaded.id)

    del store.objects[key]
    assert await reader.read_content(uploaded.id) is None
