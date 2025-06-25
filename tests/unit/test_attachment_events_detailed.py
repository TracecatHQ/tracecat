"""Comprehensive tests for attachment event data validation."""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseAttachmentCreate, CaseCreate
from tracecat.cases.service import CaseAttachmentService, CasesService
from tracecat.db.schemas import Case, CaseEvent
from tracecat.types.auth import AccessLevel, Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    """Create a CasesService instance for testing."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def attachments_service(
    session: AsyncSession, svc_role: Role
) -> CaseAttachmentService:
    """Create a CaseAttachmentService instance for testing."""
    return CaseAttachmentService(session=session, role=svc_role)


@pytest.fixture
async def test_case(cases_service: CasesService) -> Case:
    """Create a test case for attachment event testing."""
    case_params = CaseCreate(
        summary="Attachment Event Test Case",
        description="Testing attachment event data validation",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    return await cases_service.create_case(case_params)


@pytest.fixture
def attachment_params() -> CaseAttachmentCreate:
    """Create test attachment parameters."""
    content = b"Event test attachment content"
    return CaseAttachmentCreate(
        file_name="event_test.txt",
        content_type="text/plain",
        size=len(content),
        content=content,
    )


@pytest.mark.anyio
class TestAttachmentEventData:
    """Test attachment event data validation and correctness."""

    async def test_attachment_created_event_data_validation(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that attachment created event contains correct data fields."""
        with (
            patch("tracecat.storage.upload_file"),
            patch("tracecat.storage.compute_sha256", return_value="test_hash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
        ):
            # Create attachment
            attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

        # Get the attachment created event
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == test_case.id,
                CaseEvent.type == CaseEventType.ATTACHMENT_CREATED,
            )
            .order_by(col(CaseEvent.created_at).desc())
        )
        result = await session.exec(stmt)
        created_event = result.first()

        assert created_event is not None
        assert created_event.type == CaseEventType.ATTACHMENT_CREATED

        # Verify event data contains all required fields
        event_data = created_event.data
        assert "attachment_id" in event_data
        assert "file_name" in event_data
        assert "content_type" in event_data
        assert "size" in event_data

        # Verify event data matches attachment
        assert event_data["attachment_id"] == str(attachment.id)
        assert event_data["file_name"] == attachment_params.file_name
        assert event_data["content_type"] == attachment_params.content_type
        assert event_data["size"] == attachment_params.size

        # Verify user attribution
        assert created_event.user_id == attachments_service.role.user_id
        assert created_event.case_id == test_case.id

    async def test_attachment_deleted_event_data_validation(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that attachment deleted event contains correct data fields."""
        with (
            patch("tracecat.storage.upload_file"),
            patch("tracecat.storage.compute_sha256", return_value="test_hash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
            patch("tracecat.storage.delete_file"),
        ):
            # Create attachment
            attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

            # Delete attachment
            await attachments_service.delete_attachment(test_case, attachment.id)

        # Get the attachment deleted event
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == test_case.id,
                CaseEvent.type == CaseEventType.ATTACHMENT_DELETED,
            )
            .order_by(col(CaseEvent.created_at).desc())
        )
        result = await session.exec(stmt)
        deleted_event = result.first()

        assert deleted_event is not None
        assert deleted_event.type == CaseEventType.ATTACHMENT_DELETED

        # Verify event data contains required fields
        event_data = deleted_event.data
        assert "attachment_id" in event_data
        assert "file_name" in event_data

        # Verify event data matches attachment
        assert event_data["attachment_id"] == str(attachment.id)
        assert event_data["file_name"] == attachment_params.file_name

        # Verify user attribution
        assert deleted_event.user_id == attachments_service.role.user_id
        assert deleted_event.case_id == test_case.id

    async def test_attachment_events_workflow_context_handling(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that attachment events handle workflow context correctly."""
        with (
            patch("tracecat.storage.upload_file"),
            patch("tracecat.storage.compute_sha256", return_value="test_hash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
            patch("tracecat.storage.delete_file"),
        ):
            # Create attachment (default context behavior)
            attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

            # Delete attachment (default context behavior)
            await attachments_service.delete_attachment(test_case, attachment.id)

        # Get both events
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == test_case.id,
                CaseEvent.type.in_(
                    [CaseEventType.ATTACHMENT_CREATED, CaseEventType.ATTACHMENT_DELETED]
                ),
            )
            .order_by(col(CaseEvent.created_at))
        )
        result = await session.exec(stmt)
        events = result.all()

        # Should have both created and deleted events
        assert len(events) >= 2

        # Find the attachment events
        created_event = next(
            e for e in events if e.type == CaseEventType.ATTACHMENT_CREATED
        )
        deleted_event = next(
            e for e in events if e.type == CaseEventType.ATTACHMENT_DELETED
        )

        # Verify wf_exec_id field exists in data (should be None in test context)
        assert "wf_exec_id" in created_event.data
        assert "wf_exec_id" in deleted_event.data

    async def test_attachment_events_user_attribution(
        self,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
        svc_workspace,
    ) -> None:
        """Test that attachment events are attributed to the correct user."""
        # Create two different users/services
        user1_id = uuid.uuid4()
        user2_id = uuid.uuid4()

        user1_role = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=svc_workspace.id,
            user_id=user1_id,
            service_id="tracecat-api",
        )

        user2_role = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=svc_workspace.id,
            user_id=user2_id,
            service_id="tracecat-api",
        )

        service1 = CaseAttachmentService(session=session, role=user1_role)
        service2 = CaseAttachmentService(session=session, role=user2_role)

        with (
            patch("tracecat.storage.upload_file"),
            patch("tracecat.storage.compute_sha256", side_effect=["hash1", "hash2"]),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
            patch("tracecat.storage.delete_file"),
        ):
            # User1 creates attachment
            attachment1 = await service1.create_attachment(test_case, attachment_params)

            # Create different attachment content for user2
            params2 = CaseAttachmentCreate(
                file_name="user2_file.txt",
                content_type="text/plain",
                size=10,
                content=b"user2 data",
            )
            attachment2 = await service2.create_attachment(test_case, params2)

        # Get all attachment created events
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == test_case.id,
                CaseEvent.type == CaseEventType.ATTACHMENT_CREATED,
            )
            .order_by(col(CaseEvent.created_at))
        )
        result = await session.exec(stmt)
        events = result.all()

        assert len(events) == 2

        # Verify user attribution
        event1 = events[0]
        event2 = events[1]

        assert event1.user_id == user1_id
        assert event2.user_id == user2_id
        assert event1.data["attachment_id"] == str(attachment1.id)
        assert event2.data["attachment_id"] == str(attachment2.id)

    async def test_attachment_event_timestamps(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        attachment_params: CaseAttachmentCreate,
        session: AsyncSession,
    ) -> None:
        """Test that attachment events have proper timestamps."""
        start_time = datetime.now(UTC)

        with (
            patch("tracecat.storage.upload_file"),
            patch("tracecat.storage.compute_sha256", return_value="test_hash"),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                return_value={
                    "filename": attachment_params.file_name,
                    "content_type": attachment_params.content_type,
                },
            ),
            patch("tracecat.storage.delete_file"),
        ):
            # Create attachment
            attachment = await attachments_service.create_attachment(
                test_case, attachment_params
            )

            # Delete attachment
            await attachments_service.delete_attachment(test_case, attachment.id)

        end_time = datetime.now(UTC)

        # Get both events
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == test_case.id,
                CaseEvent.type.in_(
                    [CaseEventType.ATTACHMENT_CREATED, CaseEventType.ATTACHMENT_DELETED]
                ),
            )
            .order_by(col(CaseEvent.created_at))
        )
        result = await session.exec(stmt)
        events = result.all()

        assert len(events) >= 2

        # Verify timestamps are within expected range
        for event in events:
            assert start_time <= event.created_at <= end_time
            assert event.updated_at is not None

        # Verify created event comes before deleted event
        created_event = next(
            e for e in events if e.type == CaseEventType.ATTACHMENT_CREATED
        )
        deleted_event = next(
            e for e in events if e.type == CaseEventType.ATTACHMENT_DELETED
        )
        assert created_event.created_at <= deleted_event.created_at

    async def test_multiple_attachments_event_data_uniqueness(
        self,
        attachments_service: CaseAttachmentService,
        test_case: Case,
        session: AsyncSession,
    ) -> None:
        """Test that multiple attachments create distinct events with correct data."""
        # Create multiple different attachments
        attachments_data = [
            (b"First file content", "file1.txt", "text/plain"),
            (b"Second file with different content", "file2.txt", "text/plain"),
            (b"Third", "file3.txt", "text/plain"),
        ]

        with (
            patch("tracecat.storage.upload_file"),
            patch(
                "tracecat.storage.compute_sha256",
                side_effect=["hash1", "hash2", "hash3"],
            ),
            patch(
                "tracecat.storage.FileSecurityValidator.validate_file",
                side_effect=lambda content, filename, declared_content_type: {
                    "filename": filename,
                    "content_type": declared_content_type,
                },
            ),
        ):
            created_attachments = []
            for content, filename, content_type in attachments_data:
                params = CaseAttachmentCreate(
                    file_name=filename,
                    content_type=content_type,
                    size=len(content),
                    content=content,
                )
                attachment = await attachments_service.create_attachment(
                    test_case, params
                )
                created_attachments.append((attachment, params))

        # Get all attachment created events
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == test_case.id,
                CaseEvent.type == CaseEventType.ATTACHMENT_CREATED,
            )
            .order_by(col(CaseEvent.created_at))
        )
        result = await session.exec(stmt)
        events = result.all()

        assert len(events) == 3

        # Verify each event has unique and correct data
        for event, (attachment, params) in zip(
            events, created_attachments, strict=False
        ):
            assert event.data["attachment_id"] == str(attachment.id)
            assert event.data["file_name"] == params.file_name
            assert event.data["content_type"] == params.content_type
            assert event.data["size"] == params.size

        # Verify all attachment IDs are unique
        attachment_ids = [event.data["attachment_id"] for event in events]
        assert len(set(attachment_ids)) == 3  # All unique
