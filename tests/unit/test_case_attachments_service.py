import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseAttachmentCreate
from tracecat.cases.service import CaseAttachmentService
from tracecat.db.schemas import Case, File
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatException

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def attachments_service(
    session: AsyncSession, svc_role: Role
) -> CaseAttachmentService:
    """Create a CaseAttachmentService instance for testing."""
    return CaseAttachmentService(session=session, role=svc_role)


@pytest.fixture
async def admin_attachments_service(
    session: AsyncSession, svc_admin_role: Role
) -> CaseAttachmentService:
    """Create a CaseAttachmentService instance with admin role for testing."""
    return CaseAttachmentService(session=session, role=svc_admin_role)


@pytest.fixture
async def test_case(session: AsyncSession, svc_role: Role) -> Case:
    """Insert a Case row directly using SQLModel for attachment tests."""
    case = Case(
        owner_id=svc_role.workspace_id if svc_role.workspace_id else uuid.uuid4(),
        summary="Attachment Service Test Case",
        description="Testing the attachment service in isolation",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


@pytest.fixture
def attachment_params() -> CaseAttachmentCreate:
    """Return minimal, valid attachment parameters."""
    content = b"Attachment service body"
    return CaseAttachmentCreate(
        file_name="service.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )


@pytest.fixture
def png_attachment_params() -> CaseAttachmentCreate:
    """Return PNG file attachment parameters for binary file testing."""
    # Simple PNG file header + minimal data
    png_content = (
        b"\x89PNG\r\n\x1a\n"  # PNG signature
        b"\x00\x00\x00\rIHDR"  # IHDR chunk
        b"\x00\x00\x00\x01\x00\x00\x00\x01"  # 1x1 pixel
        b"\x08\x02\x00\x00\x00\x90wS\xde"  # Color type, etc.
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x00\x00\x00\x01\x00\x01"
        b"\x02\x1a\x0b\xa5"  # Minimal IDAT chunk
        b"\x00\x00\x00\x00IEND\xaeB`\x82"  # IEND chunk
    )
    return CaseAttachmentCreate(
        file_name="test.png",
        content_type="image/png",
        size=len(png_content),
        content=png_content,
    )


@pytest.mark.anyio
class TestCaseAttachmentService:
    async def test_create_attachment_calls_storage_upload(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
    ) -> None:
        """Verify that upload_file is invoked during attachment creation."""
        # Patch storage functions that interact with external systems
        with (
            patch(
                "tracecat.storage.upload_file", new_callable=AsyncMock
            ) as mock_upload,
            patch("tracecat.storage.compute_sha256", return_value="fakehash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

            mock_upload.assert_awaited_once()  # Ensure the file was uploaded
            assert created_attachment.file.sha256 == "fakehash"
            assert created_attachment.case_id == test_case.id

    async def test_create_attachment_storage_failure_rollback(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that database rollback occurs when storage upload fails."""

        with (
            patch("tracecat.storage.compute_sha256", return_value="fakehash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
            patch(
                "tracecat.storage.upload_file",
                new_callable=AsyncMock,
                side_effect=Exception("Storage upload failed"),
            ),
        ):
            with pytest.raises(TracecatException, match="Failed to upload file"):
                await attachments_service.create_attachment(
                    test_case, attachment_params
                )

            # Verify no file record was created due to rollback
            stmt = select(File).where(File.sha256 == "fakehash")
            result = await session.exec(stmt)
            files = result.all()
            assert len(files) == 0

    async def test_upload_delete_reupload_cycle(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test complete upload -> delete -> reupload cycle with file restoration."""
        original_hash = "original_file_hash"

        # Mock storage operations
        mock_upload = AsyncMock()
        mock_delete = AsyncMock()

        with (
            patch("tracecat.storage.upload_file", mock_upload),
            patch("tracecat.storage.delete_file", mock_delete),
            patch("tracecat.storage.compute_sha256", return_value=original_hash),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            # Step 1: Upload file
            attachment1 = await attachments_service.create_attachment(
                test_case, attachment_params
            )
            assert mock_upload.call_count == 1
            assert attachment1.file.sha256 == original_hash
            assert attachment1.file.deleted_at is None

            # Verify file exists in database
            stmt = select(File).where(File.sha256 == original_hash)
            result = await session.exec(stmt)
            file_record = result.first()
            assert file_record is not None
            assert file_record.deleted_at is None

            # Step 2: Delete attachment (soft delete)
            await attachments_service.delete_attachment(test_case, attachment1.id)
            assert mock_delete.call_count == 1

            # Verify file is soft-deleted by re-fetching it
            stmt = select(File).where(File.sha256 == original_hash)
            result = await session.exec(stmt)
            file_record = result.first()
            assert file_record is not None
            assert file_record.deleted_at is not None

            # Verify attachment is no longer accessible
            deleted_attachment = await attachments_service.get_attachment(
                test_case, attachment1.id
            )
            assert deleted_attachment is None

            # Step 3: Re-upload same file (should restore)
            attachment2 = await attachments_service.create_attachment(
                test_case, attachment_params
            )

            # Should have uploaded again since file was deleted
            assert mock_upload.call_count == 2

            # Should restore the same file record - re-fetch it
            stmt = select(File).where(File.sha256 == original_hash)
            result = await session.exec(stmt)
            file_record = result.first()
            assert file_record is not None
            assert file_record.deleted_at is None
            assert attachment2.file.sha256 == original_hash

            # Should reuse the same attachment record (service behavior)
            assert attachment2.id == attachment1.id
            assert attachment2.case_id == test_case.id

    async def test_upload_delete_reupload_different_case(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Test that file can be reused across different cases after deletion."""
        # Create second case
        case2 = Case(
            owner_id=svc_role.workspace_id if svc_role.workspace_id else uuid.uuid4(),
            summary="Second Test Case",
            description="For testing file reuse",
            status=CaseStatus.NEW,
            priority=CasePriority.LOW,
            severity=CaseSeverity.LOW,
        )
        session.add(case2)
        await session.commit()
        await session.refresh(case2)

        original_hash = "shared_file_hash"
        mock_upload = AsyncMock()
        mock_delete = AsyncMock()

        with (
            patch("tracecat.storage.upload_file", mock_upload),
            patch("tracecat.storage.delete_file", mock_delete),
            patch("tracecat.storage.compute_sha256", return_value=original_hash),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            # Upload to first case
            attachment1 = await attachments_service.create_attachment(
                test_case, attachment_params
            )
            assert mock_upload.call_count == 1

            # Delete from first case
            await attachments_service.delete_attachment(test_case, attachment1.id)
            assert mock_delete.call_count == 1

            # Upload same file to second case (should restore file)
            attachment2 = await attachments_service.create_attachment(
                case2, attachment_params
            )

            # Should NOT re-upload - file record still exists (soft-deleted)
            assert mock_upload.call_count == 1
            assert attachment2.file.sha256 == original_hash
            assert attachment2.case_id == case2.id

    async def test_download_attachment_without_corruption(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        png_attachment_params: CaseAttachmentCreate,
    ) -> None:
        """Test that binary files (PNG) download without corruption."""
        original_content = png_attachment_params.content
        original_hash = "png_file_hash"

        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value=original_hash),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": png_attachment_params.file_name,
                    "content_type": png_attachment_params.content_type,
                },
            ),
        ):
            # Upload PNG file
            attachment = await attachments_service.create_attachment(
                test_case, png_attachment_params
            )

        # Mock download returning exact original content
        with (
            patch(
                "tracecat.storage.download_file",
                new_callable=AsyncMock,
                return_value=original_content,
            ),
            patch("tracecat.storage.compute_sha256", return_value=original_hash),
        ):
            # Download and verify integrity
            (
                downloaded_content,
                filename,
                content_type,
            ) = await attachments_service.download_attachment(test_case, attachment.id)

            # Verify content is identical
            assert downloaded_content == original_content
            assert filename == png_attachment_params.file_name
            assert content_type == png_attachment_params.content_type

            # Verify PNG signature is intact (critical for binary files)
            assert downloaded_content.startswith(b"\x89PNG\r\n\x1a\n")

    async def test_download_large_binary_file_integrity(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
    ) -> None:
        """Test download integrity for larger binary files."""
        # Create a larger binary file with pattern
        large_content = b"\x00\x01\x02\x03" * 1024  # 4KB of binary pattern
        large_hash = "large_binary_hash"

        params = CaseAttachmentCreate(
            file_name="large_binary.bin",
            content_type="application/octet-stream",
            size=len(large_content),
            content=large_content,
        )

        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value=large_hash),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": params.file_name,
                    "content_type": params.content_type,
                },
            ),
        ):
            attachment = await attachments_service.create_attachment(test_case, params)

        # Mock download returning exact content
        with (
            patch(
                "tracecat.storage.download_file",
                new_callable=AsyncMock,
                return_value=large_content,
            ),
            patch("tracecat.storage.compute_sha256", return_value=large_hash),
        ):
            (
                downloaded_content,
                filename,
                content_type,
            ) = await attachments_service.download_attachment(test_case, attachment.id)

            # Verify exact byte-for-byte match
            assert downloaded_content == large_content
            assert len(downloaded_content) == len(large_content)

            # Verify pattern integrity at start and end
            assert downloaded_content[:4] == b"\x00\x01\x02\x03"
            assert downloaded_content[-4:] == b"\x00\x01\x02\x03"

    async def test_download_with_corrupted_content_detection(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
    ) -> None:
        """Test that corrupted downloads are properly detected."""
        original_hash = "original_hash"
        corrupted_hash = "corrupted_hash"

        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value=original_hash),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

        # Mock download returning corrupted content
        corrupted_content = b"This is corrupted content"
        with (
            patch(
                "tracecat.storage.download_file",
                new_callable=AsyncMock,
                return_value=corrupted_content,
            ),
            patch("tracecat.storage.compute_sha256", return_value=corrupted_hash),
        ):
            with pytest.raises(TracecatException, match="File integrity check failed"):
                await attachments_service.download_attachment(test_case, attachment.id)

    async def test_multiple_uploads_same_content_deduplication(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that uploading same content multiple times uses deduplication."""
        same_hash = "duplicate_content_hash"
        mock_upload = AsyncMock()

        with (
            patch("tracecat.storage.upload_file", mock_upload),
            patch("tracecat.storage.compute_sha256", return_value=same_hash),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            # First upload
            attachment1 = await attachments_service.create_attachment(
                test_case, attachment_params
            )
            assert mock_upload.call_count == 1

            # Second upload of same content
            attachment2 = await attachments_service.create_attachment(
                test_case, attachment_params
            )

            # Should not upload again due to deduplication
            assert mock_upload.call_count == 1

            # Should reuse same file record
            assert attachment1.file_id == attachment2.file_id
            assert attachment1.file.sha256 == attachment2.file.sha256 == same_hash

            # Should return the same attachment record (service behavior)
            assert attachment1.id == attachment2.id

    async def test_list_attachments_excludes_deleted_files(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Ensure list_attachments excludes soft-deleted files."""
        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value="fakehash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

        # Manually soft-delete the file
        stmt = select(File).where(File.id == created_attachment.file_id)
        result = await session.exec(stmt)
        file_record = result.one()
        assert file_record is not None  # Ensure file exists before modifying
        file_record.deleted_at = datetime.now(UTC)
        await session.commit()

        # List should now be empty
        attachments = await attachments_service.list_attachments(test_case)
        assert attachments == []

    async def test_get_attachment_returns_none_for_deleted_file(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that get_attachment returns None for soft-deleted files."""
        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value="fakehash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

        # Manually soft-delete the file
        stmt = select(File).where(File.id == created_attachment.file_id)
        result = await session.exec(stmt)
        file_record = result.one()
        assert file_record is not None  # Ensure file exists before modifying
        file_record.deleted_at = datetime.now(UTC)
        await session.commit()

        # get_attachment should return None
        result = await attachments_service.get_attachment(
            test_case, created_attachment.id
        )
        assert result is None

    async def test_delete_attachment_authorization_creator_can_delete(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that file creator can delete their attachment."""

        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value="fakehash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

        # Creator should be able to delete
        with patch("tracecat.storage.delete_file", new_callable=AsyncMock):
            await attachments_service.delete_attachment(
                test_case, created_attachment.id
            )

        # Verify file is marked as deleted in database
        stmt = select(File).where(File.id == created_attachment.file_id)
        result = await session.exec(stmt)
        file_record = result.one()
        assert file_record.deleted_at is not None

    async def test_delete_attachment_authorization_admin_can_delete(
        self,
        admin_attachments_service: CaseAttachmentService,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that admin can delete any attachment."""

        # Create attachment with regular user
        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value="fakehash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

        # Admin should be able to delete it
        with patch("tracecat.storage.delete_file", new_callable=AsyncMock):
            await admin_attachments_service.delete_attachment(
                test_case, created_attachment.id
            )

        # Verify file is marked as deleted in database
        stmt = select(File).where(File.id == created_attachment.file_id)
        result = await session.exec(stmt)
        file_record = result.one()
        assert file_record.deleted_at is not None

    async def test_delete_attachment_authorization_non_creator_cannot_delete(
        self,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
        svc_workspace,
    ) -> None:
        """Test that non-creator basic user cannot delete attachment."""
        # Create attachment with one user
        creator_role = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=svc_workspace.id,
            user_id=uuid.uuid4(),  # Different user
            service_id="tracecat-api",
        )
        creator_service = CaseAttachmentService(session=session, role=creator_role)

        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value="fakehash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            created_attachment = await creator_service.create_attachment(
                test_case, attachment_params
            )

        # Different basic user should not be able to delete
        different_user_role = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=svc_workspace.id,
            user_id=uuid.uuid4(),  # Different user
            service_id="tracecat-api",
        )
        different_user_service = CaseAttachmentService(
            session=session, role=different_user_role
        )

        with pytest.raises(
            TracecatAuthorizationError, match="You don't have permission to delete"
        ):
            await different_user_service.delete_attachment(
                test_case, created_attachment.id
            )

    async def test_download_attachment_integrity_check_failure(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
    ) -> None:
        """Test that download fails when file integrity check fails."""

        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", return_value="original_hash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            created_attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

        # Mock download returning corrupted content
        with (
            patch(
                "tracecat.storage.download_file",
                new_callable=AsyncMock,
                return_value=b"corrupted content",
            ),
            patch(
                "tracecat.storage.compute_sha256",
                return_value="different_hash",  # Different hash indicates corruption
            ),
        ):
            with pytest.raises(TracecatException, match="File integrity check failed"):
                await attachments_service.download_attachment(
                    test_case, created_attachment.id
                )

    async def test_get_total_storage_used_excludes_deleted(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        session: AsyncSession,
    ) -> None:
        """Test that storage usage calculation excludes soft-deleted files."""
        # Create two attachments
        content1 = b"First file content"
        content2 = b"Second file content"

        params1 = CaseAttachmentCreate(
            file_name="file1.txt",
            content_type="text/plain",
            size=len(content1),
            content=content1,
        )
        params2 = CaseAttachmentCreate(
            file_name="file2.txt",
            content_type="text/plain",
            size=len(content2),
            content=content2,
        )

        with (
            patch("tracecat.storage.upload_file", new_callable=AsyncMock),
            patch("tracecat.storage.compute_sha256", side_effect=["hash1", "hash2"]),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                side_effect=[
                    {"filename": "file1.txt", "content_type": "text/plain"},
                    {"filename": "file2.txt", "content_type": "text/plain"},
                ],
            ),
        ):
            attachment1 = await attachments_service.create_attachment(
                test_case, params1
            )
            await attachments_service.create_attachment(test_case, params2)

        # Initial storage should include both files
        total_bytes = await attachments_service.get_total_storage_used(test_case)
        assert total_bytes == len(content1) + len(content2)

        # Soft-delete one file
        stmt = select(File).where(File.id == attachment1.file_id)
        result = await session.exec(stmt)
        file1 = result.one()
        assert file1 is not None  # Ensure file exists before modifying
        file1.deleted_at = datetime.now(UTC)
        await session.commit()

        # Storage should now only include the non-deleted file
        total_bytes_after_delete = await attachments_service.get_total_storage_used(
            test_case
        )
        assert total_bytes_after_delete == len(content2)
