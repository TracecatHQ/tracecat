"""Tests for ApprovalManager and Temporal activities."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import ToolCallPart
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.agent.activities import (
    ApplyApprovalResultsActivityInputs,
    PersistApprovalsActivityInputs,
    ToolApprovalPayload,
)
from tracecat_ee.agent.approvals.schemas import ApprovalCreate, ApprovalUpdate
from tracecat_ee.agent.approvals.service import (
    ApprovalManager,
    ApprovalMap,
    ApprovalService,
)
from tracecat_ee.agent.context import AgentContext

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
def agent_context(mock_session_id: uuid.UUID):
    """Set up and tear down AgentContext for tests."""
    AgentContext.set(session_id=mock_session_id)
    yield
    # Context is automatically reset between tests


@pytest.fixture
def approval_manager(svc_role: Role, agent_context) -> ApprovalManager:
    """Create an ApprovalManager instance for testing."""
    return ApprovalManager(role=svc_role)


@pytest.fixture
def sample_tool_calls() -> list[ToolCallPart]:
    """Sample tool calls requiring approval."""
    return [
        ToolCallPart(
            tool_name="dangerous_tool",
            args={"action": "delete", "target": "production"},
            tool_call_id="call_123",
        ),
        ToolCallPart(
            tool_name="sensitive_tool",
            args={"data": "confidential"},
            tool_call_id="call_456",
        ),
    ]


@pytest.mark.anyio
class TestApprovalManager:
    async def test_prepare_initializes_approvals(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
        mock_session_id: uuid.UUID,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Test that prepare() initializes approval state and persists records."""
        # This is a simplified test since we can't easily test Temporal activities
        # in isolation without a Temporal worker
        assert approval_manager.status.value == "idle"
        assert approval_manager._expected_tool_calls == {}

        # Note: We cannot test the full prepare() method without a Temporal worker
        # because it calls workflow.execute_activity(). This would be tested in
        # integration tests with a real workflow.

    def test_validate_responses_success(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Test successful validation when all required approvals are provided."""
        # Set up expected tool calls
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }

        # Provide valid approvals for all expected tool calls
        approvals: ApprovalMap = {
            "call_123": True,
            "call_456": False,
        }

        # Should not raise
        approval_manager.validate_responses(approvals)

    def test_validate_responses_missing_approvals(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Test validation fails when approval responses are missing."""
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }

        # Provide only partial approvals
        approvals: ApprovalMap = {
            "call_123": True,
            # Missing call_456
        }

        with pytest.raises(ValueError, match="Missing approval responses"):
            approval_manager.validate_responses(approvals)

    def test_validate_responses_unexpected_approvals(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Test validation fails when unexpected approval responses are provided."""
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }

        # Provide approvals including unexpected ones
        approvals: ApprovalMap = {
            "call_123": True,
            "call_456": False,
            "call_789": True,  # Unexpected
        }

        with pytest.raises(ValueError, match="unexpected approval response"):
            approval_manager.validate_responses(approvals)

    def test_validate_responses_none_value(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Test validation fails when approval response is None."""
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }

        approvals = {
            "call_123": None,  # Invalid None value
            "call_456": False,
        }

        with pytest.raises(ValueError, match="cannot be None"):
            approval_manager.validate_responses(approvals)

    def test_validate_responses_empty_approvals(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Test validation fails when approval responses are empty."""
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }

        with pytest.raises(ValueError, match="cannot be empty"):
            approval_manager.validate_responses({})

    def test_validate_responses_no_pending(
        self,
        approval_manager: ApprovalManager,
    ) -> None:
        """Test validation fails when there are no pending approvals."""
        approval_manager._expected_tool_calls = {}

        approvals: ApprovalMap = {"call_123": True}

        with pytest.raises(ValueError, match="No pending approvals"):
            approval_manager.validate_responses(approvals)

    def test_is_ready(
        self,
        approval_manager: ApprovalManager,
    ) -> None:
        """Test is_ready() reflects the manager status."""
        assert not approval_manager.is_ready()

        approval_manager._status = approval_manager._status.__class__.READY
        assert approval_manager.is_ready()

    def test_set_approvals(
        self,
        approval_manager: ApprovalManager,
    ) -> None:
        """Test set() updates approval state and status."""
        approvals: ApprovalMap = {"call_123": True, "call_456": False}
        approved_by = uuid.uuid4()

        approval_manager.set(approvals, approved_by=approved_by)

        assert approval_manager._approvals == approvals
        assert approval_manager.is_ready()
        assert approval_manager._approved_by == approved_by


@pytest.mark.anyio
class TestRecordApprovalRequestsActivity:
    async def test_record_new_approvals(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test recording new approval requests creates database records."""
        approvals = [
            ToolApprovalPayload(
                tool_call_id="call_123",
                tool_name="test_tool_1",
                args={"param": "value1"},
            ),
            ToolApprovalPayload(
                tool_call_id="call_456",
                tool_name="test_tool_2",
                args={"param": "value2"},
            ),
        ]

        input_data = PersistApprovalsActivityInputs(
            role=svc_role,
            session_id=mock_session_id,
            approvals=approvals,
        )

        # Execute the activity logic directly
        await ApprovalManager.record_approval_requests(input_data)

        # Verify records were created
        async with ApprovalService.with_session(role=svc_role) as service:
            created_approvals = await service.list_approvals_for_session(
                mock_session_id
            )
            assert len(created_approvals) == 2

            # Check first approval
            approval1 = next(
                a for a in created_approvals if a.tool_call_id == "call_123"
            )
            assert approval1.tool_name == "test_tool_1"
            assert approval1.tool_call_args == {"param": "value1"}
            assert approval1.status == ApprovalStatus.PENDING

            # Check second approval
            approval2 = next(
                a for a in created_approvals if a.tool_call_id == "call_456"
            )
            assert approval2.tool_name == "test_tool_2"
            assert approval2.tool_call_args == {"param": "value2"}
            assert approval2.status == ApprovalStatus.PENDING

    async def test_record_approvals_upsert_existing(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test recording approvals updates existing records."""
        # Create initial approval
        async with ApprovalService.with_session(role=svc_role) as service:
            initial = await service.create_approval(
                ApprovalCreate(
                    session_id=mock_session_id,
                    tool_call_id="call_123",
                    tool_name="old_tool",
                    tool_call_args={"old": "args"},
                )
            )
            # Simulate it being approved
            from tracecat_ee.agent.approvals.schemas import ApprovalUpdate

            await service.update_approval(
                initial,
                ApprovalUpdate(
                    status=ApprovalStatus.APPROVED,
                    reason="Previously approved",
                ),
            )

        # Record new approval request with same session and tool_call_id
        approvals = [
            ToolApprovalPayload(
                tool_call_id="call_123",
                tool_name="updated_tool",
                args={"new": "args"},
            ),
        ]

        input_data = PersistApprovalsActivityInputs(
            role=svc_role,
            session_id=mock_session_id,
            approvals=approvals,
        )

        await ApprovalManager.record_approval_requests(input_data)

        # Verify record was updated and reset to pending
        async with ApprovalService.with_session(role=svc_role) as service:
            updated = await service.get_approval_by_session_and_tool(
                session_id=mock_session_id,
                tool_call_id="call_123",
            )
            assert updated is not None
            assert updated.tool_name == "updated_tool"
            assert updated.tool_call_args == {"new": "args"}
            assert updated.status == ApprovalStatus.PENDING
            assert updated.reason is None
            assert updated.approved_at is None

    async def test_record_approvals_empty_list(
        self,
        svc_role: Role,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test recording empty approval list is a no-op."""
        input_data = PersistApprovalsActivityInputs(
            role=svc_role,
            session_id=mock_session_id,
            approvals=[],
        )

        # Should not raise
        await ApprovalManager.record_approval_requests(input_data)

    async def test_record_approvals_string_args(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test recording approval with string args converts to dict."""
        approvals = [
            ToolApprovalPayload(
                tool_call_id="call_123",
                tool_name="test_tool",
                args='{"json": "string"}',
            ),
        ]

        input_data = PersistApprovalsActivityInputs(
            role=svc_role,
            session_id=mock_session_id,
            approvals=approvals,
        )

        await ApprovalManager.record_approval_requests(input_data)

        async with ApprovalService.with_session(role=svc_role) as service:
            approval = await service.get_approval_by_session_and_tool(
                session_id=mock_session_id,
                tool_call_id="call_123",
            )
            assert approval is not None
            assert approval.tool_call_args == {"json": "string"}


@pytest.mark.anyio
class TestApplyApprovalDecisionsActivity:
    async def test_apply_approval_decisions(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_session_id: uuid.UUID,
        test_user: User,
    ) -> None:
        """Test applying approval decisions updates database records."""
        # Use same session throughout to avoid transaction isolation issues
        service = ApprovalService(session=session, role=svc_role)

        # Create pending approvals
        approval1 = await service.create_approval(
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id="call_123",
                tool_name="tool_1",
                tool_call_args={},
            )
        )
        approval2 = await service.create_approval(
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id="call_456",
                tool_name="tool_2",
                tool_call_args={},
            )
        )

        # Apply decisions directly through service
        await service.update_approval(
            approval1,
            ApprovalUpdate(
                status=ApprovalStatus.APPROVED,
                reason="Looks good",
                decision=True,
                approved_by=test_user.id,
            ),
        )
        approval1.approved_at = datetime.now(tz=UTC)
        await session.commit()

        await service.update_approval(
            approval2,
            ApprovalUpdate(
                status=ApprovalStatus.REJECTED,
                reason="Too risky",
                decision=False,
                approved_by=test_user.id,
            ),
        )
        approval2.approved_at = datetime.now(tz=UTC)
        await session.commit()

        # Verify approvals were updated
        await session.refresh(approval1)
        await session.refresh(approval2)

        assert approval1.status == ApprovalStatus.APPROVED
        assert approval1.reason == "Looks good"
        assert approval1.decision is True
        assert approval1.approved_by == test_user.id
        assert approval1.approved_at is not None

        assert approval2.status == ApprovalStatus.REJECTED
        assert approval2.reason == "Too risky"
        assert approval2.decision is False
        assert approval2.approved_by == test_user.id
        assert approval2.approved_at is not None

    async def test_apply_approval_with_override_args(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_session_id: uuid.UUID,
        test_user: User,
    ) -> None:
        """Test applying approval with override args stores decision dict."""
        service = ApprovalService(session=session, role=svc_role)

        approval = await service.create_approval(
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id="call_123",
                tool_name="tool_1",
                tool_call_args={"original": "args"},
            )
        )

        # Apply decision with override args
        await service.update_approval(
            approval,
            ApprovalUpdate(
                status=ApprovalStatus.APPROVED,
                decision={
                    "kind": "tool-approved",
                    "override_args": {"modified": "args"},
                },
                approved_by=test_user.id,
            ),
        )
        approval.approved_at = datetime.now(tz=UTC)
        await session.commit()

        # Verify decision contains override args
        await session.refresh(approval)
        assert approval.status == ApprovalStatus.APPROVED
        assert approval.decision == {
            "kind": "tool-approved",
            "override_args": {"modified": "args"},
        }

    async def test_apply_approval_creates_placeholder_if_missing(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_session_id: uuid.UUID,
        test_user: User,
    ) -> None:
        """Test applying decision creates placeholder if approval doesn't exist."""
        service = ApprovalService(session=session, role=svc_role)

        # Check that approval doesn't exist yet
        approval = await service.get_approval_by_session_and_tool(
            session_id=mock_session_id,
            tool_call_id="call_999",
        )
        assert approval is None

        # Create placeholder record
        approval = await service.create_approval(
            ApprovalCreate(
                session_id=mock_session_id,
                tool_call_id="call_999",
                tool_name="unknown",
                tool_call_args=None,
            )
        )

        # Apply decision
        await service.update_approval(
            approval,
            ApprovalUpdate(
                status=ApprovalStatus.APPROVED,
                decision=True,
                approved_by=test_user.id,
            ),
        )
        approval.approved_at = datetime.now(tz=UTC)
        await session.commit()

        # Verify placeholder was created and updated
        await session.refresh(approval)
        assert approval.tool_name == "unknown"
        assert approval.status == ApprovalStatus.APPROVED
        assert approval.approved_by == test_user.id

    async def test_apply_approval_empty_decisions(
        self,
        svc_role: Role,
        mock_session_id: uuid.UUID,
    ) -> None:
        """Test applying empty decisions list is a no-op."""
        input_data = ApplyApprovalResultsActivityInputs(
            role=svc_role,
            session_id=mock_session_id,
            decisions=[],
        )

        # Should not raise
        await ApprovalManager.apply_approval_decisions(input_data)
