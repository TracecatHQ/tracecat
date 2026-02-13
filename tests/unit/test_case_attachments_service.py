import os
import uuid
from io import BytesIO

import pytest
from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.cases.attachments.service import CaseAttachmentService
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CaseEventsService, CasesService
from tracecat.exceptions import TracecatAuthorizationError, TracecatException
from tracecat.storage.blob import ensure_bucket_exists
from tracecat.storage.exceptions import (
    FileExtensionError,
    FileNameError,
    MaxAttachmentsExceededError,
    StorageLimitExceededError,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(scope="session", autouse=True)
def sync_minio_credentials(monkeysession: pytest.MonkeyPatch):
    """Ensure MinIO server and S3 client use same creds from .env.

    Reads credentials via python-dotenv using the same fallback chain as
    ``tests.conftest._minio_credentials`` so the test client authenticates
    with the same identity the MinIO container was started with.
    """
    try:
        env = dotenv_values()
    except Exception:
        env = {}

    access_key = (
        env.get("AWS_ACCESS_KEY_ID")
        or env.get("MINIO_ROOT_USER")
        or os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
        or "minio"
    )
    secret_key = (
        env.get("AWS_SECRET_ACCESS_KEY")
        or env.get("MINIO_ROOT_PASSWORD")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "password"
    )

    monkeysession.setenv("AWS_ACCESS_KEY_ID", access_key)
    monkeysession.setenv("AWS_SECRET_ACCESS_KEY", secret_key)


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def attachments_service(
    session: AsyncSession, svc_role: Role
) -> CaseAttachmentService:
    return CaseAttachmentService(session=session, role=svc_role)


@pytest.fixture
def attachment_params() -> CaseAttachmentCreate:
    content = b"hello tracecat"
    return CaseAttachmentCreate(
        file_name="hello.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )


@pytest.fixture
def second_attachment_params() -> CaseAttachmentCreate:
    content = b"second file content"
    return CaseAttachmentCreate(
        file_name="second.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )


@pytest.fixture
async def test_case(cases_service: CasesService) -> tuple:
    params = CaseCreate(
        summary="Attachments Integration Case",
        description="Case for testing attachments service",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    case = await cases_service.create_case(params)
    return case, cases_service


@pytest.fixture
async def configure_minio_for_attachments(
    minio_bucket: str, monkeypatch: pytest.MonkeyPatch
):
    # Point storage at the test MinIO instance and bucket
    monkeypatch.setattr(
        config,
        "TRACECAT__BLOB_STORAGE_ENDPOINT",
        "http://localhost:9000",
        raising=False,
    )
    monkeypatch.setattr(
        config, "TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS", minio_bucket, raising=False
    )

    # Set MinIO credentials for the client
    monkeypatch.setenv(
        "AWS_ACCESS_KEY_ID", os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin")
    )
    monkeypatch.setenv(
        "AWS_SECRET_ACCESS_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin")
    )

    # Ensure bucket exists from our client perspective
    await ensure_bucket_exists(minio_bucket)


# ----------------------------
# Tests
# ----------------------------


@pytest.mark.anyio
async def test_create_list_download_delete_attachment(
    configure_minio_for_attachments,
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    session: AsyncSession,
):
    case, cases_service = test_case

    # Create attachment
    created = await attachments_service.create_attachment(case, attachment_params)
    assert created.id is not None
    assert created.file.name == "hello.txt"
    assert created.file.content_type == "text/plain"
    assert created.file.size == attachment_params.size

    # List attachments
    items = await attachments_service.list_attachments(case)
    assert len(items) == 1
    assert items[0].id == created.id

    # Download and verify content + metadata
    content, filename, content_type = await attachments_service.download_attachment(
        case, created.id
    )
    assert content == attachment_params.content
    assert filename == attachment_params.file_name
    assert content_type == attachment_params.content_type

    # Presigned download URL
    url, fname, ctype = await attachments_service.get_attachment_download_url(
        case, created.id
    )
    assert isinstance(url, str) and url.startswith("http")
    assert fname == attachment_params.file_name
    assert ctype == attachment_params.content_type

    # Storage usage reflects the file size
    used = await attachments_service.get_total_storage_used(case)
    assert used == attachment_params.size

    # Delete and validate state
    await attachments_service.delete_attachment(case, created.id)

    # Deleted attachments are excluded from getters and listings
    assert await attachments_service.get_attachment(case, created.id) is None
    items_after = await attachments_service.list_attachments(case)
    assert len(items_after) == 0

    # Storage usage resets
    used_after = await attachments_service.get_total_storage_used(case)
    assert used_after == 0

    # Events: 1 created + 1 deleted
    events = await CaseEventsService(session, cases_service.role).list_events(case)
    types = [e.type for e in events]
    assert CaseEventType.ATTACHMENT_CREATED in types
    assert CaseEventType.ATTACHMENT_DELETED in types


@pytest.mark.anyio
async def test_dedup_same_file_returns_existing_attachment(
    configure_minio_for_attachments,
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    session: AsyncSession,
):
    case, cases_service = test_case

    # First create
    a1 = await attachments_service.create_attachment(case, attachment_params)

    # Second create with identical content should return the same attachment, no new event
    a2 = await attachments_service.create_attachment(case, attachment_params)
    assert a2.id == a1.id

    # Only one created event
    events = await CaseEventsService(session, cases_service.role).list_events(case)
    created_events = [e for e in events if e.type == CaseEventType.ATTACHMENT_CREATED]
    assert len(created_events) == 1


@pytest.mark.anyio
async def test_restore_after_delete_reuses_attachment_id(
    configure_minio_for_attachments,
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    session: AsyncSession,
):
    case, cases_service = test_case

    a1 = await attachments_service.create_attachment(case, attachment_params)
    await attachments_service.delete_attachment(case, a1.id)

    # Recreate with same content should restore and reuse the attachment id
    a2 = await attachments_service.create_attachment(case, attachment_params)
    assert a2.id == a1.id

    # Two created events (initial + restore) and one deleted
    events = await CaseEventsService(session, cases_service.role).list_events(case)
    created_events = [e for e in events if e.type == CaseEventType.ATTACHMENT_CREATED]
    deleted_events = [e for e in events if e.type == CaseEventType.ATTACHMENT_DELETED]
    assert len(created_events) == 2
    assert len(deleted_events) == 1


@pytest.mark.anyio
async def test_max_attachments_limit_enforced(
    configure_minio_for_attachments,
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    second_attachment_params: CaseAttachmentCreate,
    monkeypatch: pytest.MonkeyPatch,
):
    case, _ = test_case

    # Restrict to 1 attachment per case to trigger limit on second create
    monkeypatch.setattr(config, "TRACECAT__MAX_ATTACHMENTS_PER_CASE", 1, raising=False)

    # First attachment succeeds
    await attachments_service.create_attachment(case, attachment_params)

    # Second unique attachment should exceed the limit
    with pytest.raises(MaxAttachmentsExceededError):
        await attachments_service.create_attachment(case, second_attachment_params)


@pytest.mark.anyio
async def test_storage_limit_enforced(
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    monkeypatch: pytest.MonkeyPatch,
):
    """Exceed per-case total storage and expect StorageLimitExceededError."""
    case, _ = test_case
    # Set max storage below file size to trigger limit
    monkeypatch.setattr(
        config,
        "TRACECAT__MAX_CASE_STORAGE_BYTES",
        max(1, attachment_params.size - 1),
        raising=False,
    )
    with pytest.raises(StorageLimitExceededError):
        await attachments_service.create_attachment(case, attachment_params)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_name,exc",
    [
        ("malware.exe", FileExtensionError),
        ("bad/../name.txt", FileNameError),
    ],
)
async def test_validation_errors_filename_and_extension(
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    bad_name: str,
    exc: type[Exception],
):
    """Invalid filename and extension are rejected by the validator."""
    case, _ = test_case
    bad = CaseAttachmentCreate(
        file_name=bad_name,
        content_type="text/plain",
        size=len(attachment_params.content),
        content=attachment_params.content,
    )
    with pytest.raises(exc):
        await attachments_service.create_attachment(case, bad)


@pytest.mark.anyio
async def test_integrity_check_failure_after_object_corruption(
    configure_minio_for_attachments,
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    minio_bucket: str,
    minio_client,
):
    """If blob content is tampered, download fails integrity check."""
    case, _ = test_case
    created = await attachments_service.create_attachment(case, attachment_params)

    # Corrupt the object in MinIO by overwriting the same key
    key = created.storage_path
    tampered = b"tampered-content"
    minio_client.put_object(
        minio_bucket,
        key,
        BytesIO(tampered),
        length=len(tampered),
        content_type="text/plain",
    )

    with pytest.raises(TracecatException, match="File integrity check failed"):
        await attachments_service.download_attachment(case, created.id)


@pytest.mark.anyio
async def test_delete_authorization_basic_vs_admin(
    configure_minio_for_attachments,
    test_case: tuple,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
    session: AsyncSession,
    svc_admin_role,
):
    """Basic user who is not creator cannot delete; admin can."""
    case, _ = test_case
    created = await attachments_service.create_attachment(case, attachment_params)

    # Attempt delete with a different BASIC user in the same workspace
    other_role = Role(
        type="user",
        workspace_id=attachments_service.role.workspace_id,
        organization_id=attachments_service.role.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )
    other_svc = CaseAttachmentService(session=session, role=other_role)
    with pytest.raises(TracecatAuthorizationError):
        await other_svc.delete_attachment(case, created.id)

    # Admin role can delete
    admin_svc = CaseAttachmentService(session=session, role=svc_admin_role)
    await admin_svc.delete_attachment(case, created.id)


@pytest.mark.anyio
async def test_cross_case_dedup_shares_file(
    configure_minio_for_attachments,
    cases_service: CasesService,
    attachments_service: CaseAttachmentService,
    attachment_params: CaseAttachmentCreate,
):
    """Same content attached across cases shares the File entity (dedup)."""
    # Create two cases in same workspace
    c1 = await cases_service.create_case(
        CaseCreate(
            summary="c1",
            description="",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    c2 = await cases_service.create_case(
        CaseCreate(
            summary="c2",
            description="",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )

    a1 = await attachments_service.create_attachment(c1, attachment_params)
    a2 = await attachments_service.create_attachment(c2, attachment_params)

    assert a1.id != a2.id
    assert a1.file_id == a2.file_id
