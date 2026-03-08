import uuid
from collections import Counter
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES, SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import (
    CaseCommentCreate,
    CaseCommentUpdate,
    CaseCreate,
)
from tracecat.cases.service import CaseCommentsService, CasesService
from tracecat.db.models import AuditEvent as DBAuditEvent
from tracecat.db.models import Case, CaseEvent
from tracecat.exceptions import TracecatAuthorizationError, TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def stub_case_duration_sync() -> Iterator[None]:
    with patch(
        "tracecat.cases.service.CaseDurationService.sync_case_durations",
        new=AsyncMock(return_value=None),
    ):
        yield


async def _load_case_events(
    session: AsyncSession, case_id: uuid.UUID
) -> list[CaseEvent]:
    result = await session.execute(
        select(CaseEvent)
        .where(CaseEvent.case_id == case_id)
        .order_by(CaseEvent.created_at, CaseEvent.surrogate_id)
    )
    return list(result.scalars().all())


async def _load_audit_events(session: AsyncSession) -> list[DBAuditEvent]:
    bind = session.bind
    if isinstance(bind, AsyncConnection):
        engine: AsyncEngine = bind.engine
    elif isinstance(bind, AsyncEngine):
        engine = bind
    else:
        raise AssertionError("Expected async session to be bound to an engine")

    async with AsyncSession(engine, expire_on_commit=False) as persisted_session:
        result = await persisted_session.execute(
            select(DBAuditEvent).order_by(DBAuditEvent.created_at, DBAuditEvent.id)
        )
        return list(result.scalars().all())


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(session: AsyncSession) -> None:
    """Test that service initialization requires a workspace ID."""
    # Create a role without workspace_id (but with organization_id to pass org check)
    role_without_workspace = Role(
        type="service",
        user_id=uuid.uuid4(),
        workspace_id=None,
        organization_id=uuid.uuid4(),
        service_id="tracecat-service",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
    )

    # Attempt to create service without workspace should raise error
    with pytest.raises(TracecatAuthorizationError):
        CaseCommentsService(session=session, role=role_without_workspace)


@pytest.fixture
def test_comment_id() -> uuid.UUID:
    """Return a fixed test comment ID for testing."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def test_user_id() -> uuid.UUID:
    """Return a fixed test user ID for testing."""
    return uuid.uuid4()


@pytest.fixture
async def case_comments_service(
    session: AsyncSession, svc_role: Role
) -> CaseCommentsService:
    """Create a case comments service instance for testing."""
    return CaseCommentsService(session=session, role=svc_role)


@pytest.fixture
async def test_case(session: AsyncSession, svc_role: Role) -> Case:
    """Create a test case for use in comments tests."""
    cases_service = CasesService(session=session, role=svc_role)

    case = await cases_service.create_case(
        CaseCreate(
            summary="Test Case for Comments",
            description="This is a test case for comment testing",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    return case


@pytest.fixture
def comment_create_params() -> CaseCommentCreate:
    """Sample comment creation parameters."""
    return CaseCommentCreate(
        content="This is a test comment",
        parent_id=None,
    )


@pytest.mark.anyio
class TestCaseCommentsService:
    async def test_create_and_get_comment(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test creating and retrieving a comment."""
        existing_audit_count = len(await _load_audit_events(session))
        # Create comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        assert created_comment.content == comment_create_params.content
        assert created_comment.parent_id == comment_create_params.parent_id
        assert created_comment.case_id == test_case.id
        assert created_comment.user_id == case_comments_service.role.user_id
        assert created_comment.workspace_id == case_comments_service.workspace_id

        case_events = await _load_case_events(session, test_case.id)
        assert [event.type for event in case_events] == [
            CaseEventType.CASE_CREATED,
            CaseEventType.COMMENT_CREATED,
        ]
        assert case_events[-1].data["comment_id"] == str(created_comment.id)
        assert case_events[-1].data["parent_id"] is None
        assert case_events[-1].data["thread_root_id"] == str(created_comment.id)

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        assert [event.status for event in audit_events] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.SUCCESS.value,
        ]
        assert all(event.resource_type == "case_comment" for event in audit_events)
        assert audit_events[-1].data["content"] == comment_create_params.content

        # Retrieve comment
        retrieved_comment = await case_comments_service.get_comment(created_comment.id)
        assert retrieved_comment is not None
        assert retrieved_comment.id == created_comment.id
        assert retrieved_comment.content == comment_create_params.content
        assert retrieved_comment.parent_id == comment_create_params.parent_id
        assert retrieved_comment.user_id == case_comments_service.role.user_id

    async def test_list_comments(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test listing comments."""
        existing_audit_count = len(await _load_audit_events(session))
        # Create two comments
        comment1 = await case_comments_service.create_comment(
            test_case, comment_create_params
        )

        # Create a second comment with different content
        comment2 = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(
                content="This is another test comment",
                parent_id=None,
            ),
        )

        # Create a reply to the first comment
        comment3 = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(
                content="This is a reply to the first comment",
                parent_id=comment1.id,
            ),
        )

        # List all comments
        comments = await case_comments_service.list_comments(test_case)
        assert len(comments) == 3

        # Check that all our comments are in the list
        comment_ids = {comment.id for comment in comments}
        assert comment1.id in comment_ids
        assert comment2.id in comment_ids
        assert comment3.id in comment_ids

        # Check parent-child relationship
        for comment in comments:
            if comment.id == comment3.id:
                assert comment.parent_id == comment1.id

        case_events = await _load_case_events(session, test_case.id)
        event_counts = Counter(event.type for event in case_events)
        assert event_counts[CaseEventType.COMMENT_CREATED] == 2
        assert event_counts[CaseEventType.COMMENT_REPLY_CREATED] == 1

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        audit_counts = Counter(event.status for event in audit_events)
        assert audit_counts[AuditEventStatus.ATTEMPT.value] == 3
        assert audit_counts[AuditEventStatus.SUCCESS.value] == 3
        reply_audit = next(
            event
            for event in audit_events
            if event.data["parent_id"] == str(comment1.id)
        )
        assert reply_audit.data["is_reply"] is True
        assert reply_audit.data["thread_root_id"] == str(comment1.id)

    async def test_list_comment_threads(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test listing threaded comments."""
        parent = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(
                content="Thread reply",
                parent_id=parent.id,
            ),
        )

        threads = await case_comments_service.list_comment_threads(test_case)

        assert len(threads) == 1
        thread = threads[0]
        assert thread.comment.id == parent.id
        assert thread.reply_count == 1
        assert len(thread.replies) == 1
        assert thread.replies[0].id == reply.id

    async def test_create_reply_rejects_cross_case_parent(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Reply creation should reject parents from another case."""
        cases_service = CasesService(session=session, role=svc_role)
        existing_audit_count = len(await _load_audit_events(session))
        first_case = await cases_service.create_case(
            case_params := CaseCreate(
                summary="First case",
                description="Case one",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        second_case = await cases_service.create_case(
            case_params.model_copy(update={"summary": "Second case"})
        )
        parent = await case_comments_service.create_comment(
            first_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )

        with pytest.raises(
            TracecatValidationError, match="Parent comment must belong to the same case"
        ):
            await case_comments_service.create_comment(
                second_case,
                CaseCommentCreate(content="Cross-case reply", parent_id=parent.id),
            )

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        create_audits = [event for event in audit_events if event.action == "create"]
        assert [event.status for event in create_audits[-2:]] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.FAILURE.value,
        ]
        assert create_audits[-1].data["parent_id"] == str(parent.id)

    async def test_create_reply_rejects_reply_parent(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Reply creation should reject replies to replies."""
        parent = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        with pytest.raises(
            TracecatValidationError, match="Replies cannot have replies"
        ):
            await case_comments_service.create_comment(
                test_case,
                CaseCommentCreate(content="Nested reply", parent_id=reply.id),
            )

    async def test_update_comment(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test updating a comment."""
        existing_audit_count = len(await _load_audit_events(session))
        # Create a comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )

        # Update parameters
        update_params = CaseCommentUpdate(content="Updated test comment content")

        # Update comment
        updated_comment = await case_comments_service.update_comment(
            created_comment, update_params
        )
        assert updated_comment.content == update_params.content

        # Verify updates persisted
        retrieved_comment = await case_comments_service.get_comment(created_comment.id)
        assert retrieved_comment is not None
        assert retrieved_comment.content == update_params.content

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_UPDATED
        assert case_events[-1].data["comment_id"] == str(created_comment.id)

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        update_audits = [event for event in audit_events if event.action == "update"]
        assert [event.status for event in update_audits] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.SUCCESS.value,
        ]
        assert update_audits[-1].data["content"] == update_params.content

    async def test_update_reply_emits_reply_activity(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
    ) -> None:
        existing_audit_count = len(await _load_audit_events(session))
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        await case_comments_service.update_comment(
            reply,
            CaseCommentUpdate(content="Updated reply"),
        )

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_REPLY_UPDATED

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        update_audits = [event for event in audit_events if event.action == "update"]
        assert update_audits[-1].data["parent_id"] == str(parent.id)
        assert update_audits[-1].data["is_reply"] is True

    async def test_update_comment_rejects_reparenting(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
    ) -> None:
        """Updating parent_id should be rejected."""
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        with pytest.raises(
            TracecatValidationError, match="Changing a comment parent is not supported"
        ):
            await case_comments_service.update_comment(
                reply,
                CaseCommentUpdate(parent_id=parent.id),
            )

    async def test_update_comment_authorization(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        test_user_id: uuid.UUID,
    ) -> None:
        """Test that a user can only update their own comments."""
        existing_audit_count = len(await _load_audit_events(session))
        # Create service with original user
        service1 = CaseCommentsService(session=session, role=svc_role)

        # Create a comment as the first user
        created_comment = await service1.create_comment(
            test_case, comment_create_params
        )

        # Create a different role with a different user ID
        different_role = Role(
            type=svc_role.type,
            user_id=test_user_id,  # Different user ID
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=ADMIN_SCOPES,
        )

        # Create service with different user
        service2 = CaseCommentsService(session=session, role=different_role)

        # Try to update the comment with a different user
        update_params = CaseCommentUpdate(content="Attempted update by different user")

        # Should raise authorization error
        with pytest.raises(TracecatAuthorizationError):
            await service2.update_comment(created_comment, update_params)

        # Verify the comment wasn't updated
        retrieved_comment = await service1.get_comment(created_comment.id)
        assert retrieved_comment is not None
        assert retrieved_comment.content == comment_create_params.content

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        failed_update_audits = [
            event for event in audit_events if event.action == "update"
        ]
        assert [event.status for event in failed_update_audits] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.FAILURE.value,
        ]

    async def test_delete_comment(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test deleting a comment."""
        existing_audit_count = len(await _load_audit_events(session))
        # Create a comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )

        # Delete the comment
        await case_comments_service.delete_comment(created_comment)

        # Verify deletion
        deleted_comment = await case_comments_service.get_comment(created_comment.id)
        assert deleted_comment is None

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_DELETED
        assert case_events[-1].data["delete_mode"] == "hard"

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        delete_audits = [event for event in audit_events if event.action == "delete"]
        assert [event.status for event in delete_audits] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.SUCCESS.value,
        ]
        assert "content" not in delete_audits[-1].data
        assert delete_audits[-1].data["delete_mode"] == "hard"

    async def test_delete_reply_hard_deletes_leaf(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
    ) -> None:
        """Replies are hard deleted."""
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        await case_comments_service.delete_comment(reply)

        deleted_reply = await case_comments_service.get_comment(reply.id)
        assert deleted_reply is None

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_REPLY_DELETED
        assert case_events[-1].data["delete_mode"] == "hard"

    async def test_delete_thread_starter_with_replies_soft_deletes(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
    ) -> None:
        """Top-level comments with replies are soft deleted and rendered as tombstones."""
        existing_audit_count = len(await _load_audit_events(session))
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        await case_comments_service.delete_comment(parent)

        deleted_parent = await case_comments_service.get_comment(parent.id)
        assert deleted_parent is not None
        assert deleted_parent.deleted_at is not None

        threads = await case_comments_service.list_comment_threads(test_case)
        assert len(threads) == 1
        assert threads[0].comment.id == parent.id
        assert threads[0].comment.is_deleted is True
        assert threads[0].comment.content == "Comment deleted"

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_DELETED
        assert case_events[-1].data["delete_mode"] == "soft"

        audit_events = (await _load_audit_events(session))[existing_audit_count:]
        delete_audits = [event for event in audit_events if event.action == "delete"]
        assert delete_audits[-1].data["delete_mode"] == "soft"
        assert "content" not in delete_audits[-1].data

    async def test_delete_comment_authorization(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        test_user_id: uuid.UUID,
    ) -> None:
        """Test that a user can only delete their own comments."""
        # Create service with original user
        service1 = CaseCommentsService(session=session, role=svc_role)

        # Create a comment as the first user
        created_comment = await service1.create_comment(
            test_case, comment_create_params
        )

        # Create a different role with a different user ID
        different_role = Role(
            type=svc_role.type,
            user_id=test_user_id,  # Different user ID
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=ADMIN_SCOPES,
        )

        # Create service with different user
        service2 = CaseCommentsService(session=session, role=different_role)

        # Try to delete the comment with a different user
        # Should raise authorization error
        with pytest.raises(TracecatAuthorizationError):
            await service2.delete_comment(created_comment)

        # Verify the comment wasn't deleted
        retrieved_comment = await service1.get_comment(created_comment.id)
        assert retrieved_comment is not None
