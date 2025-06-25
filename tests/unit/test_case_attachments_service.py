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
