"""Unit tests for the ApprovalService."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from temporalio.exceptions import ApplicationError

from tracecat.ee.approvals import (
    ApprovalCreate,
    ApprovalsService,
    ApprovalUpdate,
    CreateApprovalActivityInputs,
    UpdateApprovalActivityInputs,
)
from tracecat.ee.enums import ApprovalStatus, ApprovalType
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(
    session: AsyncSession,
) -> None:
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
        ApprovalsService(session=session, role=role_without_workspace)


@pytest.fixture
async def approvals_service(session: AsyncSession, svc_role: Role) -> ApprovalsService:
    """Create an approvals service instance for testing."""
    return ApprovalsService(session=session, role=svc_role)


@pytest.fixture
def approval_create_params() -> ApprovalCreate:
    """Sample approval creation parameters."""
    return ApprovalCreate(
        session_id="test_session_123",
        type=ApprovalType.TOOL_CALL,
        status=ApprovalStatus.PENDING,
        data={"tool": "test_tool", "args": {"param": "value"}},
        actor="test_user",
    )


@pytest.fixture
def approval_update_params() -> ApprovalUpdate:
    """Sample approval update parameters."""
    return ApprovalUpdate(
        status=ApprovalStatus.APPROVED,
        data={"result": "approved", "timestamp": "2024-01-01T00:00:00Z"},
        actor="approver_user",
    )


@pytest.mark.anyio
class TestApprovalsServiceCRUD:
    """Test CRUD operations for ApprovalsService."""

    async def test_create_and_get_approval(
        self,
        approvals_service: ApprovalsService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test creating and retrieving an approval."""
        # Create approval
        created_approval = await approvals_service.create_approval(
            approval_create_params
        )
        assert created_approval.session_id == approval_create_params.session_id
        assert created_approval.type == approval_create_params.type
        assert created_approval.status == approval_create_params.status
        assert created_approval.data == approval_create_params.data
        assert created_approval.actor == approval_create_params.actor
        assert created_approval.owner_id == approvals_service.workspace_id

        # Retrieve approval
        retrieved_approval = await approvals_service.get_approval(created_approval.id)
        assert retrieved_approval is not None
        assert retrieved_approval.id == created_approval.id
        assert retrieved_approval.session_id == approval_create_params.session_id
        assert retrieved_approval.type == approval_create_params.type
        assert retrieved_approval.status == approval_create_params.status
        assert retrieved_approval.data == approval_create_params.data
        assert retrieved_approval.actor == approval_create_params.actor

    async def test_get_nonexistent_approval(
        self, approvals_service: ApprovalsService
    ) -> None:
        """Test retrieving a non-existent approval returns None."""
        nonexistent_id = uuid.uuid4()
        approval = await approvals_service.get_approval(nonexistent_id)
        assert approval is None

    async def test_update_approval(
        self,
        approvals_service: ApprovalsService,
        approval_create_params: ApprovalCreate,
        approval_update_params: ApprovalUpdate,
    ) -> None:
        """Test updating an approval."""
        # Create approval
        created_approval = await approvals_service.create_approval(
            approval_create_params
        )

        # Update approval
        updated_approval = await approvals_service.update_approval(
            created_approval, approval_update_params
        )

        assert updated_approval.id == created_approval.id
        assert updated_approval.status == approval_update_params.status
        assert updated_approval.data == approval_update_params.data
        assert updated_approval.actor == approval_update_params.actor
        # Unchanged fields should remain the same
        assert updated_approval.session_id == created_approval.session_id
        assert updated_approval.type == created_approval.type

    async def test_update_approval_partial(
        self,
        approvals_service: ApprovalsService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test partial update of an approval."""
        # Create approval
        created_approval = await approvals_service.create_approval(
            approval_create_params
        )

        # Update only status
        partial_update = ApprovalUpdate(status=ApprovalStatus.REJECTED)
        updated_approval = await approvals_service.update_approval(
            created_approval, partial_update
        )

        assert updated_approval.status == ApprovalStatus.REJECTED
        # Other fields should remain unchanged
        assert updated_approval.data == created_approval.data
        assert updated_approval.actor == created_approval.actor

    async def test_list_approvals(
        self,
        approvals_service: ApprovalsService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test listing approvals."""
        # Create multiple approvals
        approval1 = await approvals_service.create_approval(approval_create_params)

        # Create another approval with different session_id
        params2 = ApprovalCreate(
            session_id="test_session_456",
            type=ApprovalType.TOOL_CALL,
            status=ApprovalStatus.APPROVED,
        )
        approval2 = await approvals_service.create_approval(params2)

        # List all approvals
        all_approvals = await approvals_service.list_approvals()
        approval_ids = [a.id for a in all_approvals]
        assert approval1.id in approval_ids
        assert approval2.id in approval_ids

        # List approvals by session_id
        session_approvals = await approvals_service.list_approvals(
            session_id="test_session_123"
        )
        assert len(session_approvals) == 1
        assert session_approvals[0].id == approval1.id

        session_approvals_2 = await approvals_service.list_approvals(
            session_id="test_session_456"
        )
        assert len(session_approvals_2) == 1
        assert session_approvals_2[0].id == approval2.id

    async def test_delete_approval(
        self,
        approvals_service: ApprovalsService,
        approval_create_params: ApprovalCreate,
    ) -> None:
        """Test deleting an approval."""
        # Create approval
        created_approval = await approvals_service.create_approval(
            approval_create_params
        )

        # Delete approval
        await approvals_service.delete_approval(created_approval)

        # Verify it's deleted
        deleted_approval = await approvals_service.get_approval(created_approval.id)
        assert deleted_approval is None


@pytest.mark.anyio
class TestApprovalsServiceMinimalFields:
    """Test ApprovalsService with minimal required fields."""

    async def test_create_approval_minimal_fields(
        self, approvals_service: ApprovalsService
    ) -> None:
        """Test creating an approval with only required fields."""
        minimal_params = ApprovalCreate(
            session_id="minimal_session",
            type=ApprovalType.TOOL_CALL,
        )

        created_approval = await approvals_service.create_approval(minimal_params)

        assert created_approval.session_id == "minimal_session"
        assert created_approval.type == ApprovalType.TOOL_CALL
        assert created_approval.status == ApprovalStatus.PENDING  # Default value
        assert created_approval.data is None
        assert created_approval.actor is None
        assert created_approval.owner_id == approvals_service.workspace_id


@pytest.mark.anyio
class TestApprovalsServiceWorkspaceIsolation:
    """Test workspace isolation for ApprovalsService."""

    async def test_workspace_isolation(
        self, session: AsyncSession, svc_workspace
    ) -> None:
        """Test that approvals are isolated by workspace."""
        # Create two different roles with different workspace IDs
        workspace1_id = svc_workspace.id
        workspace2_id = uuid.uuid4()

        role1 = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=workspace1_id,
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        )

        role2 = Role(
            type="user",
            access_level=AccessLevel.BASIC,
            workspace_id=workspace2_id,
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        )

        service1 = ApprovalsService(session=session, role=role1)
        service2 = ApprovalsService(session=session, role=role2)

        # Create approval in workspace1
        params1 = ApprovalCreate(
            session_id="workspace1_session",
            type=ApprovalType.TOOL_CALL,
        )
        approval1 = await service1.create_approval(params1)

        # Service2 should not be able to get approval1
        retrieved = await service2.get_approval(approval1.id)
        assert retrieved is None

        # Service1 should be able to get approval1
        retrieved = await service1.get_approval(approval1.id)
        assert retrieved is not None
        assert retrieved.id == approval1.id

        # List approvals should only show workspace-specific approvals
        service1_approvals = await service1.list_approvals()
        assert len(service1_approvals) >= 1
        assert all(a.owner_id == workspace1_id for a in service1_approvals)

        service2_approvals = await service2.list_approvals()
        # Should not include approval1
        assert approval1.id not in [a.id for a in service2_approvals]


@pytest.mark.anyio
class TestTemporalActivities:
    """Test Temporal activities for ApprovalsService."""

    async def test_create_approval_activity(self, svc_role: Role) -> None:
        """Test the create_approval_activity."""
        input_params = CreateApprovalActivityInputs(
            role=svc_role,
            params=ApprovalCreate(
                session_id="activity_session",
                type=ApprovalType.TOOL_CALL,
                status=ApprovalStatus.PENDING,
            ),
        )

        with patch.object(ApprovalsService, "with_session") as mock_with_session:
            mock_service = MagicMock()
            mock_approval = MagicMock()
            mock_approval.id = uuid.uuid4()
            mock_service.create_approval = AsyncMock(return_value=mock_approval)
            mock_service.logger = MagicMock()

            # Mock the context manager
            mock_context_manager = AsyncMock()
            mock_context_manager.__aenter__.return_value = mock_service
            mock_context_manager.__aexit__.return_value = None
            mock_with_session.return_value = mock_context_manager

            result = await ApprovalsService.create_approval_activity(input_params)

            assert result == mock_approval.id
            mock_service.create_approval.assert_called_once_with(input_params.params)
            mock_service.logger.info.assert_called_once()

    async def test_update_approval_activity_success(self, svc_role: Role) -> None:
        """Test the update_approval_activity with existing approval."""
        approval_id = uuid.uuid4()
        input_params = UpdateApprovalActivityInputs(
            role=svc_role,
            approval_id=approval_id,
            params=ApprovalUpdate(status=ApprovalStatus.APPROVED),
        )

        with patch.object(ApprovalsService, "with_session") as mock_with_session:
            mock_service = MagicMock()
            mock_approval = MagicMock()
            mock_approval.id = approval_id
            mock_service.get_approval = AsyncMock(return_value=mock_approval)
            mock_service.update_approval = AsyncMock(return_value=mock_approval)

            # Mock the context manager
            mock_context_manager = AsyncMock()
            mock_context_manager.__aenter__.return_value = mock_service
            mock_context_manager.__aexit__.return_value = None
            mock_with_session.return_value = mock_context_manager

            result = await ApprovalsService.update_approval_activity(input_params)

            assert result == approval_id
            mock_service.get_approval.assert_called_once_with(approval_id)
            mock_service.update_approval.assert_called_once_with(
                mock_approval, input_params.params
            )

    async def test_update_approval_activity_not_found(self, svc_role: Role) -> None:
        """Test the update_approval_activity with non-existent approval."""
        approval_id = uuid.uuid4()
        input_params = UpdateApprovalActivityInputs(
            role=svc_role,
            approval_id=approval_id,
            params=ApprovalUpdate(status=ApprovalStatus.APPROVED),
        )

        with patch.object(ApprovalsService, "with_session") as mock_with_session:
            mock_service = MagicMock()
            mock_service.get_approval = AsyncMock(return_value=None)

            # Mock the context manager
            mock_context_manager = AsyncMock()
            mock_context_manager.__aenter__.return_value = mock_service
            mock_context_manager.__aexit__.return_value = None
            mock_with_session.return_value = mock_context_manager

            with pytest.raises(ApplicationError) as exc_info:
                await ApprovalsService.update_approval_activity(input_params)

            assert "Approval not found" in str(exc_info.value)
            assert exc_info.value.non_retryable is True
            mock_service.get_approval.assert_called_once_with(approval_id)
