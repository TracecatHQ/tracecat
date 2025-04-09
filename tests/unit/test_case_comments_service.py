import uuid

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.models import CaseCommentCreate, CaseCommentUpdate
from tracecat.cases.service import CaseCommentsService, CasesService
from tracecat.db.schemas import Case
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(session: AsyncSession) -> None:
    """Test that service initialization requires a workspace ID."""
    # Create a role without workspace_id
    role_without_workspace = Role(
        type="service",
        user_id=uuid.uuid4(),
        workspace_id=None,
        service_id="tracecat-service",
        access_level=AccessLevel.BASIC,
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

    from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
    from tracecat.cases.models import CaseCreate

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
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test creating and retrieving a comment."""
        # Create comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        assert created_comment.content == comment_create_params.content
        assert created_comment.parent_id == comment_create_params.parent_id
        assert created_comment.case_id == test_case.id
        assert created_comment.user_id == case_comments_service.role.user_id
        assert created_comment.owner_id == case_comments_service.workspace_id

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
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test listing comments."""
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
        comment_ids = {comment.id for comment, _ in comments}
        assert comment1.id in comment_ids
        assert comment2.id in comment_ids
        assert comment3.id in comment_ids

        # Check parent-child relationship
        for comment, _ in comments:
            if comment.id == comment3.id:
                assert comment.parent_id == comment1.id

    async def test_update_comment(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test updating a comment."""
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

    async def test_update_comment_authorization(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        test_user_id: uuid.UUID,
    ) -> None:
        """Test that a user can only update their own comments."""
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
            service_id=svc_role.service_id,
            access_level=svc_role.access_level,
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

    async def test_delete_comment(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test deleting a comment."""
        # Create a comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )

        # Delete the comment
        await case_comments_service.delete_comment(created_comment)

        # Verify deletion
        deleted_comment = await case_comments_service.get_comment(created_comment.id)
        assert deleted_comment is None

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
            service_id=svc_role.service_id,
            access_level=svc_role.access_level,
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
