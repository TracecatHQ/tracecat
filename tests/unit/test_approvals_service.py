"""Tests for ApprovalService CRUD operations."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_ee.agent.approvals.models import ApprovalCreate, ApprovalUpdate
from tracecat_ee.agent.approvals.service import ApprovalService, SessionInfo

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.auth.types import Role
from tracecat.db.models import User

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def mock_session_id() -> uuid.UUID:
    """Return a fixed session ID for testing."""
    return uuid.uuid4()


@pytest.fixture
async def test_user(session: AsyncSession, svc_role: Role) -> User:
    """Create a test user for approval tests."""
    user_id = svc_role.user_id if svc_role.user_id else uuid.uuid4()
    user = User(
        id=user_id,
        email=f"test-{uuid.uuid4()}@example.com",
        hashed_password="test_password",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def approvals_service(session: AsyncSession, svc_role: Role) -> ApprovalService:
    """Create an approvals service instance for testing."""
    return ApprovalService(session=session, role=svc_role)


@pytest.fixture
def approval_create_params(mock_session_id: uuid.UUID) -> ApprovalCreate:
    """Sample approval creation parameters."""
    return ApprovalCreate(
        session_id=mock_session_id,
        tool_call_id="test_tool_call_123",
        tool_name="test_tool",
        tool_call_args={"param1": "value1", "param2": 42},
    )


@pytest.mark.anyio
class TestApprovalService:
    async def test_create_and_get_approval(
        self,
        approvals_service: ApprovalService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test creating and retrieving a single approval."""
        # Create approval
        created = await approvals_service.create_approval(approval_create_params)
        assert created.session_id == approval_create_params.session_id
        assert created.tool_call_id == approval_create_params.tool_call_id
        assert created.tool_name == approval_create_params.tool_name
        assert created.tool_call_args == approval_create_params.tool_call_args
        assert created.status == ApprovalStatus.PENDING
        assert created.owner_id == approvals_service.workspace_id
        assert created.approved_by is None
        assert created.approved_at is None

        # Retrieve approval by ID
        retrieved = await approvals_service.get_approval(created.id)
        assert retrieved.id == created.id
        assert retrieved.session_id == approval_create_params.session_id
        assert retrieved.tool_call_id == approval_create_params.tool_call_id

    async def test_create_approvals_batch(
        self,
        approvals_service: ApprovalService,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test batch creating multiple approvals."""
        approvals = [
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id=f"tool_call_{i}",
                tool_name=f"tool_{i}",
                tool_call_args={"index": i},
            )
            for i in range(3)
        ]

        created = await approvals_service.create_approvals(approvals)
        assert len(created) == 3
        for i, approval in enumerate(created):
            assert approval.tool_call_id == f"tool_call_{i}"
            assert approval.tool_name == f"tool_{i}"
            assert approval.status == ApprovalStatus.PENDING

    async def test_create_approvals_empty_list(
        self,
        approvals_service: ApprovalService,
    ) -> None:
        """Test batch create with empty list returns empty list."""
        created = await approvals_service.create_approvals([])
        assert created == []

    async def test_get_approval_by_session_and_tool(
        self,
        approvals_service: ApprovalService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test retrieving approval by session and tool call ID."""
        created = await approvals_service.create_approval(approval_create_params)

        # Retrieve by session and tool call ID
        retrieved = await approvals_service.get_approval_by_session_and_tool(
            session_id=approval_create_params.session_id,
            tool_call_id=approval_create_params.tool_call_id,
        )
        assert retrieved is not None
        assert retrieved.id == created.id

    async def test_get_approval_by_session_and_tool_not_found(
        self,
        approvals_service: ApprovalService,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test retrieving non-existent approval returns None."""
        retrieved = await approvals_service.get_approval_by_session_and_tool(
            session_id=mock_session_id,
            tool_call_id="nonexistent_tool_call",
        )
        assert retrieved is None

    async def test_list_approvals_for_session(
        self,
        approvals_service: ApprovalService,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test listing all approvals for a session."""
        # Create multiple approvals for the same session
        approvals = [
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id=f"tool_call_{i}",
                tool_name=f"tool_{i}",
                tool_call_args={"index": i},
            )
            for i in range(3)
        ]
        await approvals_service.create_approvals(approvals)

        # Create approval for different session (should not be included)
        other_session_id = uuid.uuid4()
        await approvals_service.create_approval(
            ApprovalCreate(
                session_id=other_session_id,
                tool_call_id="other_tool_call",
                tool_name="other_tool",
                tool_call_args={},
            )
        )

        # List approvals for specific session
        session_approvals = await approvals_service.list_approvals_for_session(
            mock_session_id
        )
        assert len(session_approvals) == 3
        assert all(a.session_id == mock_session_id for a in session_approvals)

    async def test_list_sessions_enriched(
        self,
        approvals_service: ApprovalService,
        mock_session_id: uuid.UUID,
        test_user: User,
    ) -> None:
        """Test listing enriched sessions with approval data."""
        # Create approvals for session
        approval = await approvals_service.create_approval(
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id="tool_call_1",
                tool_name="test_tool",
                tool_call_args={},
            )
        )

        # Update approval to set approved_by
        await approvals_service.update_approval(
            approval,
            ApprovalUpdate(
                status=ApprovalStatus.APPROVED,
                approved_by=test_user.id,
            ),
        )

        # List enriched sessions
        sessions = [
            SessionInfo(
                session_id=mock_session_id,
                start_time=approval.created_at,
            )
        ]
        enriched = await approvals_service.list_sessions_enriched(sessions)

        assert len(enriched) == 1
        enriched_session = enriched[0]
        assert enriched_session.id == mock_session_id
        assert len(enriched_session.approvals) == 1
        enriched_approval = enriched_session.approvals[0]
        assert enriched_approval.approval.id == approval.id
        assert enriched_approval.approved_by is not None
        assert enriched_approval.approved_by.id == test_user.id

    async def test_update_approval(
        self,
        approvals_service: ApprovalService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test updating a single approval."""
        created = await approvals_service.create_approval(approval_create_params)
        assert created.status == ApprovalStatus.PENDING

        # Update approval
        updated = await approvals_service.update_approval(
            created,
            ApprovalUpdate(
                status=ApprovalStatus.APPROVED,
                reason="Approved for testing",
                decision=True,
            ),
        )
        assert updated.id == created.id
        assert updated.status == ApprovalStatus.APPROVED
        assert updated.reason == "Approved for testing"
        assert updated.decision is True

    async def test_update_approvals_batch(
        self,
        approvals_service: ApprovalService,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test batch updating multiple approvals."""
        # Create multiple approvals
        approvals = [
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id=f"tool_call_{i}",
                tool_name=f"tool_{i}",
                tool_call_args={},
            )
            for i in range(3)
        ]
        created = await approvals_service.create_approvals(approvals)

        # Batch update
        updates = {
            created[0].id: ApprovalUpdate(status=ApprovalStatus.APPROVED),
            created[1].id: ApprovalUpdate(status=ApprovalStatus.REJECTED),
            created[2].id: ApprovalUpdate(status=ApprovalStatus.APPROVED),
        }
        updated = await approvals_service.update_approvals(updates)

        assert len(updated) == 3
        assert updated[0].status == ApprovalStatus.APPROVED
        assert updated[1].status == ApprovalStatus.REJECTED
        assert updated[2].status == ApprovalStatus.APPROVED

    async def test_update_approvals_empty_dict(
        self,
        approvals_service: ApprovalService,
    ) -> None:
        """Test batch update with empty dict returns empty list."""
        updated = await approvals_service.update_approvals({})
        assert updated == []

    async def test_delete_approval(
        self,
        approvals_service: ApprovalService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test deleting an approval."""
        created = await approvals_service.create_approval(approval_create_params)

        # Delete approval
        await approvals_service.delete_approval(created)

        # Verify deletion
        retrieved = await approvals_service.get_approval_by_session_and_tool(
            session_id=approval_create_params.session_id,
            tool_call_id=approval_create_params.tool_call_id,
        )
        assert retrieved is None

    async def test_unique_constraint_violation(
        self,
        approvals_service: ApprovalService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test that creating duplicate (owner_id, session_id, tool_call_id) raises error."""
        # Create first approval
        await approvals_service.create_approval(approval_create_params)

        # Attempt to create duplicate should raise IntegrityError
        with pytest.raises(IntegrityError):
            await approvals_service.create_approval(approval_create_params)

    async def test_approval_with_null_args(
        self,
        approvals_service: ApprovalService,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test creating approval with null tool_call_args."""
        params = ApprovalCreate(
            session_id=mock_session_id,
            tool_call_id="tool_call_no_args",
            tool_name="tool_no_args",
            tool_call_args=None,
        )
        created = await approvals_service.create_approval(params)
        assert created.tool_call_args is None
