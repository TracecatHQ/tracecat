import uuid
from unittest.mock import patch

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseAttachmentCreate, CaseCreate
from tracecat.cases.service import CaseAttachmentService, CasesService
from tracecat.db.schemas import CaseAttachment, CaseEvent, File
from tracecat.storage import compute_sha256
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures(
    "db",  # create database
    "minio_server",  # ensure MinIO is running
    "aioboto3_minio_client",  # patch aioboto3 to use MinIO
    "configured_bucket",  # ensure bucket is configured
)


@pytest.fixture
def sample_file_content() -> bytes:
    """Return sample file bytes for upload."""
    return b"Hello Tracecat!"


@pytest.fixture
def sample_attachment_params(sample_file_content: bytes) -> CaseAttachmentCreate:
    """Create attachment params for tests."""
    return CaseAttachmentCreate(
        file_name="greeting.txt",
        content_type="text/plain",
        size=len(sample_file_content),
        content=sample_file_content,
    )


@pytest.fixture
def configured_bucket(minio_bucket: str, monkeypatch: pytest.MonkeyPatch):
    """Configure Tracecat to use the dynamically-generated MinIO bucket."""
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET", minio_bucket)
    # Ensure the MinIO credentials are available for the storage client
    monkeypatch.setenv("MINIO_ROOT_USER", "minioadmin")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "minioadmin")
    return minio_bucket


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role) -> CasesService:
    """Return a CasesService instance for tests."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def attachments_service(session: AsyncSession, svc_role) -> CaseAttachmentService:
    """Return a CaseAttachmentService instance for tests."""
    return CaseAttachmentService(session=session, role=svc_role)


@pytest.fixture
def case_create_params() -> CaseCreate:
    """Default parameters for creating a case."""
    return CaseCreate(
        summary="Attachment Test Case",
        description="Testing case attachments end-to-end",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


@pytest.mark.anyio
class TestCaseAttachmentsIntegration:
    async def test_attachment_lifecycle(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
        sample_attachment_params: CaseAttachmentCreate,
        sample_file_content: bytes,
        session: AsyncSession,
    ) -> None:
        """Full lifecycle test: upload → list → download → delete attachment."""
        # 1. Create a case
        test_case = await cases_service.create_case(case_create_params)

        # 2. Upload an attachment (patch validator to avoid heavy dependencies)
        with patch(
            "tracecat.storage.FileSecurityValidator.validate_file",
            return_value={
                "filename": sample_attachment_params.file_name,
                "content_type": sample_attachment_params.content_type,
            },
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, sample_attachment_params
            )

        # Basic assertions on created attachment
        assert created_attachment.case_id == test_case.id
        assert created_attachment.file.size == len(sample_file_content)
        assert created_attachment.file.sha256 == compute_sha256(sample_file_content)

        # 3. Listing should return exactly this attachment
        attachments = await attachments_service.list_attachments(test_case)
        assert len(attachments) == 1
        assert attachments[0].id == created_attachment.id

        # 4. Download the attachment and verify integrity
        content, filename, content_type = await attachments_service.download_attachment(
            test_case, created_attachment.id
        )
        assert content == sample_file_content
        assert filename == sample_attachment_params.file_name
        assert content_type == sample_attachment_params.content_type

        # 5. Storage usage should reflect the file size
        total_bytes = await attachments_service.get_total_storage_used(test_case)
        assert total_bytes == len(sample_file_content)

        # 6. Soft-delete the attachment
        await attachments_service.delete_attachment(test_case, created_attachment.id)

        # 7. After deletion, listing should be empty and usage 0
        attachments_after_delete = await attachments_service.list_attachments(test_case)
        assert attachments_after_delete == []

        total_bytes_after_delete = await attachments_service.get_total_storage_used(
            test_case
        )
        assert total_bytes_after_delete == 0

        # 8. Verify the file record is soft-deleted in the database
        stmt = select(File).where(File.id == created_attachment.file_id)
        file_result = await session.exec(stmt)
        file_row = file_result.one()
        assert file_row.deleted_at is not None

        # 9. Verify attachment link still exists (historical data)
        stmt = select(CaseAttachment).where(CaseAttachment.id == created_attachment.id)
        att_result = await session.exec(stmt)
        att_row = att_result.one()
        assert att_row is not None

    async def test_file_deduplication(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
        sample_attachment_params: CaseAttachmentCreate,
        sample_file_content: bytes,
        session: AsyncSession,
    ) -> None:
        """Test that identical files are deduplicated in storage."""
        # Create two cases
        case1 = await cases_service.create_case(case_create_params)
        case2_params = CaseCreate(
            summary="Second Case",
            description="Another case for deduplication test",
            status=CaseStatus.NEW,
            priority=CasePriority.LOW,
            severity=CaseSeverity.LOW,
        )
        case2 = await cases_service.create_case(case2_params)

        with patch(
            "tracecat.storage.FileSecurityValidator.validate_file",
            return_value={
                "filename": sample_attachment_params.file_name,
                "content_type": sample_attachment_params.content_type,
            },
        ):
            # Upload same file to both cases
            attachment1 = await attachments_service.create_attachment(
                case1, sample_attachment_params
            )
            attachment2 = await attachments_service.create_attachment(
                case2, sample_attachment_params
            )

        # Both attachments should reference the same file record
        assert attachment1.file_id == attachment2.file_id
        assert attachment1.file.sha256 == attachment2.file.sha256

        # But should be separate attachment records
        assert attachment1.id != attachment2.id

        # Verify only one file record exists in database
        stmt = select(File).where(File.sha256 == compute_sha256(sample_file_content))
        file_result = await session.exec(stmt)
        files = file_result.all()
        assert len(files) == 1

    async def test_case_events_created(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
        sample_attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that attachment operations create proper case events."""
        test_case = await cases_service.create_case(case_create_params)

        with patch(
            "tracecat.storage.FileSecurityValidator.validate_file",
            return_value={
                "filename": sample_attachment_params.file_name,
                "content_type": sample_attachment_params.content_type,
            },
        ):
            # Upload attachment
            attachment = await attachments_service.create_attachment(
                test_case, sample_attachment_params
            )

            # Delete attachment
            await attachments_service.delete_attachment(test_case, attachment.id)

        # Check that events were created
        stmt = (
            select(CaseEvent)
            .where(CaseEvent.case_id == test_case.id)
            .order_by(col(CaseEvent.created_at))
        )
        events_result = await session.exec(stmt)
        events = events_result.all()

        # Should have at least 3 events: case created, attachment uploaded, attachment deleted
        assert len(events) >= 3

        # Check for case creation event
        case_created_events = [
            e for e in events if e.type == CaseEventType.CASE_CREATED
        ]
        assert len(case_created_events) == 1

        # Check for attachment events
        attachment_created_events = [
            e for e in events if e.type == CaseEventType.ATTACHMENT_CREATED
        ]
        attachment_deleted_events = [
            e for e in events if e.type == CaseEventType.ATTACHMENT_DELETED
        ]
        assert len(attachment_created_events) == 1
        assert len(attachment_deleted_events) == 1

    async def test_download_nonexistent_attachment(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
    ) -> None:
        """Test downloading a non-existent attachment raises proper error."""
        test_case = await cases_service.create_case(case_create_params)

        fake_attachment_id = uuid.uuid4()

        with pytest.raises(TracecatNotFoundError, match="Attachment .* not found"):
            await attachments_service.download_attachment(test_case, fake_attachment_id)

    async def test_delete_nonexistent_attachment(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
    ) -> None:
        """Test deleting a non-existent attachment raises proper error."""
        test_case = await cases_service.create_case(case_create_params)

        fake_attachment_id = uuid.uuid4()

        with pytest.raises(TracecatNotFoundError, match="Attachment .* not found"):
            await attachments_service.delete_attachment(test_case, fake_attachment_id)

    async def test_security_validation_failure(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
    ) -> None:
        """Test that file security validation failures are properly handled."""
        test_case = await cases_service.create_case(case_create_params)

        # Create params for a file that should fail validation
        malicious_params = CaseAttachmentCreate(
            file_name="malicious.exe",
            content_type="application/x-executable",
            size=100,
            content=b"MZ\x90\x00" + b"fake executable content",  # PE header
        )

        # Should raise ValueError due to security validation
        with pytest.raises(ValueError, match="not allowed for security reasons"):
            await attachments_service.create_attachment(test_case, malicious_params)

        # Test SVG rejection
        svg_params = CaseAttachmentCreate(
            file_name="image.svg",
            content_type="image/svg+xml",
            size=100,
            content=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert("XSS")</script></svg>',
        )

        # Should raise ValueError due to SVG being blocked
        with pytest.raises(ValueError, match="not allowed for security reasons"):
            await attachments_service.create_attachment(test_case, svg_params)

    async def test_multiple_attachments_storage_usage(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test storage usage calculation with multiple attachments."""
        test_case = await cases_service.create_case(case_create_params)

        # Create multiple different files
        files = [
            (b"First file content", "file1.txt"),
            (b"Second file content is longer", "file2.txt"),
            (b"Third", "file3.txt"),
        ]

        total_expected_size = 0

        with patch(
            "tracecat.storage.FileSecurityValidator.validate_file",
            side_effect=lambda content, filename, declared_content_type: {
                "filename": filename,
                "content_type": declared_content_type,
            },
        ):
            for content, filename in files:
                params = CaseAttachmentCreate(
                    file_name=filename,
                    content_type="text/plain",
                    size=len(content),
                    content=content,
                )
                await attachments_service.create_attachment(test_case, params)
                total_expected_size += len(content)

        # Check total storage usage
        total_bytes = await attachments_service.get_total_storage_used(test_case)
        assert total_bytes == total_expected_size

        # List should show all attachments
        attachments = await attachments_service.list_attachments(test_case)
        assert len(attachments) == 3

    async def test_get_attachment_download_url(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
        sample_attachment_params: CaseAttachmentCreate,
        sample_file_content: bytes,
    ) -> None:
        """Test generating presigned download URLs for attachments."""
        # 1. Create a case
        test_case = await cases_service.create_case(case_create_params)

        # 2. Upload an attachment
        with patch(
            "tracecat.storage.FileSecurityValidator.validate_file",
            return_value={
                "filename": sample_attachment_params.file_name,
                "content_type": sample_attachment_params.content_type,
            },
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, sample_attachment_params
            )

        # 3. Generate presigned download URL
        (
            presigned_url,
            filename,
            content_type,
        ) = await attachments_service.get_attachment_download_url(
            test_case, created_attachment.id
        )

        # Verify the response
        assert isinstance(presigned_url, str)
        assert presigned_url.startswith("http")  # Should be a valid URL
        assert filename == sample_attachment_params.file_name
        assert content_type == sample_attachment_params.content_type

        # Verify the URL contains expected MinIO components
        assert "localhost" in presigned_url or "minio" in presigned_url
        assert (
            created_attachment.file.sha256 in presigned_url
        )  # Storage key contains SHA256

        # 4. Test preview mode for images
        # Create an image attachment
        image_params = CaseAttachmentCreate(
            file_name="image.png",
            content_type="image/png",
            size=100,
            content=b"\x89PNG\r\n\x1a\n" + b"fake png content",
        )

        with patch(
            "tracecat.storage.FileSecurityValidator.validate_file",
            return_value={
                "filename": image_params.file_name,
                "content_type": image_params.content_type,
            },
        ):
            image_attachment = await attachments_service.create_attachment(
                test_case, image_params
            )

        # Test preview mode
        (
            preview_url,
            preview_filename,
            preview_content_type,
        ) = await attachments_service.get_attachment_download_url(
            test_case, image_attachment.id, preview=True
        )

        assert isinstance(preview_url, str)
        assert preview_filename == image_params.file_name
        assert preview_content_type == image_params.content_type

    async def test_get_attachment_download_url_nonexistent(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
    ) -> None:
        """Test generating presigned URL for non-existent attachment raises proper error."""
        test_case = await cases_service.create_case(case_create_params)
        fake_attachment_id = uuid.uuid4()

        with pytest.raises(TracecatNotFoundError, match="Attachment .* not found"):
            await attachments_service.get_attachment_download_url(
                test_case, fake_attachment_id
            )

    async def test_download_attachment_still_works(
        self,
        cases_service: CasesService,
        attachments_service: CaseAttachmentService,
        case_create_params: CaseCreate,
        sample_attachment_params: CaseAttachmentCreate,
        sample_file_content: bytes,
    ) -> None:
        """Test that the original download_attachment method still works alongside presigned URLs."""
        # 1. Create a case
        test_case = await cases_service.create_case(case_create_params)

        # 2. Upload an attachment
        with patch(
            "tracecat.storage.FileSecurityValidator.validate_file",
            return_value={
                "filename": sample_attachment_params.file_name,
                "content_type": sample_attachment_params.content_type,
            },
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, sample_attachment_params
            )

        # 3. Verify both download methods work
        # Direct download
        content, filename, content_type = await attachments_service.download_attachment(
            test_case, created_attachment.id
        )
        assert content == sample_file_content
        assert filename == sample_attachment_params.file_name
        assert content_type == sample_attachment_params.content_type

        # Presigned URL generation
        (
            presigned_url,
            url_filename,
            url_content_type,
        ) = await attachments_service.get_attachment_download_url(
            test_case, created_attachment.id
        )
        assert isinstance(presigned_url, str)
        assert url_filename == sample_attachment_params.file_name
        assert url_content_type == sample_attachment_params.content_type
