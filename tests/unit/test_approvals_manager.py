"""Tests for ApprovalManager and Temporal activities."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import ToolCallPart
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.agent.activities import (
    ApplyApprovalResultsActivityInputs,
    ApprovalDecisionPayload,
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
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import AgentSession, Approval, User

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def patched_approval_service(session: AsyncSession, svc_role: Role):
    """Fixture that patches ApprovalService.with_session to use the test session.

    This is needed because the test session uses SAVEPOINT isolation, so the
    workspace created in the test is not visible to new database connections.
    By patching with_session, we ensure the ApprovalService uses the same
    session and can see the workspace.
    """

    @asynccontextmanager
    async def mock_with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[ApprovalService, None]:
        yield ApprovalService(session, role=role or svc_role)

    with patch.object(ApprovalService, "with_session", mock_with_session):
        yield


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
async def agent_context(mock_agent_session: AgentSession):
    """Set up and tear down AgentContext for tests."""
    AgentContext.set(session_id=mock_agent_session.id)
    yield
    # Context is automatically reset between tests


@pytest.fixture
async def approval_manager(svc_role: Role, agent_context) -> ApprovalManager:
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
        mock_agent_session: AgentSession,
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

    async def test_validate_responses_success(
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

    async def test_validate_responses_accepts_partial_approvals(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Partial submissions are valid: approvals are decided one by one."""
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }

        # Provide only partial approvals
        approvals: ApprovalMap = {
            "call_123": True,
            # call_456 will be decided in a later submission
        }

        # Should not raise
        approval_manager.validate_responses(approvals)

    async def test_validate_responses_unexpected_approvals(
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

    async def test_validate_responses_none_value(
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

    async def test_validate_responses_empty_approvals(
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

    async def test_validate_responses_no_pending(
        self,
        approval_manager: ApprovalManager,
    ) -> None:
        """Test validation fails when there are no pending approvals."""
        approval_manager._expected_tool_calls = {}

        approvals: ApprovalMap = {"call_123": True}

        with pytest.raises(ValueError, match="No pending approvals"):
            approval_manager.validate_responses(approvals)

    async def test_is_ready(
        self,
        approval_manager: ApprovalManager,
    ) -> None:
        """Test is_ready() reflects the manager status."""
        assert not approval_manager.is_ready()

        approval_manager._status = approval_manager._status.__class__.READY
        assert approval_manager.is_ready()

    async def test_set_approvals(
        self,
        approval_manager: ApprovalManager,
    ) -> None:
        """Test set() updates approval state and status."""
        approvals: ApprovalMap = {"call_123": True, "call_456": False}
        approved_by = uuid.uuid4()

        approval_manager.set(approvals, approved_by=approved_by)

        assert approval_manager._approvals == approvals
        assert approval_manager.is_ready()
        assert approval_manager._approved_by_by_tool_call_id == {
            "call_123": approved_by,
            "call_456": approved_by,
        }

    async def test_set_accumulates_partial_decisions(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Parallel approvals decided one by one only resume when complete."""
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }
        first_approver = uuid.uuid4()
        second_approver = uuid.uuid4()

        approval_manager.set({"call_123": True}, approved_by=first_approver)

        assert not approval_manager.is_ready()
        assert approval_manager._approvals == {"call_123": True}

        approval_manager.set({"call_456": False}, approved_by=second_approver)

        assert approval_manager.is_ready()
        assert approval_manager._approvals == {"call_123": True, "call_456": False}
        assert approval_manager._approved_by_by_tool_call_id == {
            "call_123": first_approver,
            "call_456": second_approver,
        }

    async def test_set_resubmission_overwrites_decision(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """Re-deciding the same tool call is idempotent (last write wins)."""
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }

        approval_manager.set({"call_123": True})
        approval_manager.set({"call_123": False})

        assert not approval_manager.is_ready()
        assert approval_manager._approvals == {"call_123": False}

    async def test_handle_decisions_emits_unique_tool_call_ids(
        self,
        approval_manager: ApprovalManager,
        sample_tool_calls: list[ToolCallPart],
    ) -> None:
        """The persistence upsert cannot carry one conflict key twice.

        Resubmitting a decision is the only way a duplicate could plausibly
        reach the activity, so drive that and assert the payload collapses it.
        """
        approval_manager._expected_tool_calls = {
            tc.tool_call_id: tc for tc in sample_tool_calls
        }
        approval_manager.set({"call_123": True})
        approval_manager.set({"call_123": False})
        approval_manager.set({"call_456": True})

        execute_activity = AsyncMock(return_value=None)
        with patch(
            "tracecat_ee.agent.approvals.service.workflow.execute_activity",
            execute_activity,
        ):
            await approval_manager.handle_decisions()

        execute_activity.assert_awaited_once()
        assert execute_activity.await_args is not None
        payload = execute_activity.await_args.kwargs["arg"]
        submitted = [decision.tool_call_id for decision in payload.decisions]
        assert sorted(submitted) == ["call_123", "call_456"]
        assert len(submitted) == len(set(submitted))
        # Last write wins, so the resubmitted deny is what persists.
        decided = {d.tool_call_id: d.approved for d in payload.decisions}
        assert decided == {"call_123": False, "call_456": True}


@pytest.mark.anyio
@pytest.mark.usefixtures("patched_approval_service")
class TestRecordApprovalRequestsActivity:
    async def test_record_new_approvals(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_agent_session: AgentSession,
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
            session_id=mock_agent_session.id,
            approvals=approvals,
        )

        # Execute the activity logic directly
        await ApprovalManager.record_approval_requests(input_data)

        # Verify records were created
        async with ApprovalService.with_session(role=svc_role) as service:
            created_approvals = await service.list_approvals_for_session(
                mock_agent_session.id
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
        mock_agent_session: AgentSession,
    ) -> None:
        """Test recording approvals updates existing records."""
        # Create initial approval
        async with ApprovalService.with_session(role=svc_role) as service:
            initial = await service.create_approval(
                ApprovalCreate(
                    session_id=mock_agent_session.id,
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
            session_id=mock_agent_session.id,
            approvals=approvals,
        )

        await ApprovalManager.record_approval_requests(input_data)

        # Verify record was updated and reset to pending
        async with ApprovalService.with_session(role=svc_role) as service:
            updated = await service.get_approval_by_session_and_tool(
                session_id=mock_agent_session.id,
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
        mock_agent_session: AgentSession,
    ) -> None:
        """Test recording empty approval list is a no-op."""
        input_data = PersistApprovalsActivityInputs(
            role=svc_role,
            session_id=mock_agent_session.id,
            approvals=[],
        )

        # Should not raise
        await ApprovalManager.record_approval_requests(input_data)

    async def test_record_approvals_string_args(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_agent_session: AgentSession,
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
            session_id=mock_agent_session.id,
            approvals=approvals,
        )

        await ApprovalManager.record_approval_requests(input_data)

        async with ApprovalService.with_session(role=svc_role) as service:
            approval = await service.get_approval_by_session_and_tool(
                session_id=mock_agent_session.id,
                tool_call_id="call_123",
            )
            assert approval is not None
            assert approval.tool_call_args == {"json": "string"}

    async def test_record_approvals_strips_internal_proxy_metadata(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_agent_session: AgentSession,
    ) -> None:
        """Test recording approval args removes Tracecat-internal metadata."""
        approvals = [
            ToolApprovalPayload(
                tool_call_id="call_123",
                tool_name="test_tool",
                args={
                    "url": "https://example.com",
                    "__tracecat": {"tool_call_id": "call_123"},
                },
            ),
        ]

        input_data = PersistApprovalsActivityInputs(
            role=svc_role,
            session_id=mock_agent_session.id,
            approvals=approvals,
        )

        await ApprovalManager.record_approval_requests(input_data)

        async with ApprovalService.with_session(role=svc_role) as service:
            approval = await service.get_approval_by_session_and_tool(
                session_id=mock_agent_session.id,
                tool_call_id="call_123",
            )
            assert approval is not None
            assert approval.tool_call_args == {"url": "https://example.com"}


@pytest.mark.anyio
class TestApplyApprovalDecisionsActivity:
    async def test_apply_approval_decisions(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_agent_session: AgentSession,
        test_user: User,
    ) -> None:
        """Test applying approval decisions updates database records."""
        # Use same session throughout to avoid transaction isolation issues
        service = ApprovalService(session=session, role=svc_role)

        # Create pending approvals
        approval1 = await service.create_approval(
            ApprovalCreate(
                session_id=mock_agent_session.id,
                tool_call_id="call_123",
                tool_name="tool_1",
                tool_call_args={},
            )
        )
        approval2 = await service.create_approval(
            ApprovalCreate(
                session_id=mock_agent_session.id,
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
        mock_agent_session: AgentSession,
        test_user: User,
    ) -> None:
        """Test applying approval with override args stores decision dict."""
        service = ApprovalService(session=session, role=svc_role)

        approval = await service.create_approval(
            ApprovalCreate(
                session_id=mock_agent_session.id,
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
        mock_agent_session: AgentSession,
        test_user: User,
    ) -> None:
        """Test applying decision creates placeholder if approval doesn't exist."""
        service = ApprovalService(session=session, role=svc_role)

        # Check that approval doesn't exist yet
        approval = await service.get_approval_by_session_and_tool(
            session_id=mock_agent_session.id,
            tool_call_id="call_999",
        )
        assert approval is None

        # Create placeholder record
        approval = await service.create_approval(
            ApprovalCreate(
                session_id=mock_agent_session.id,
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
        mock_agent_session: AgentSession,
    ) -> None:
        """Test applying empty decisions list is a no-op."""
        input_data = ApplyApprovalResultsActivityInputs(
            role=svc_role,
            session_id=mock_agent_session.id,
            decisions=[],
        )

        # Should not raise
        await ApprovalManager.apply_approval_decisions(input_data)


@pytest.mark.anyio
@pytest.mark.usefixtures("patched_approval_service")
class TestApplyApprovalDecisionMetadata:
    async def test_apply_approval_decision_merges_metadata(
        self,
        svc_role: Role,
        mock_agent_session: AgentSession,
    ) -> None:
        input_data = ApplyApprovalResultsActivityInputs(
            role=svc_role,
            session_id=mock_agent_session.id,
            decisions=[
                ApprovalDecisionPayload(
                    tool_call_id="call_meta_1",
                    approved=True,
                    decision=True,
                    decision_metadata={
                        "source": "slack",
                        "actor": {"user_id": "U123", "display_name": "jordan"},
                    },
                )
            ],
        )

        await ApprovalManager.apply_approval_decisions(input_data)

        async with ApprovalService.with_session(role=svc_role) as service:
            approval = await service.get_approval_by_session_and_tool(
                session_id=mock_agent_session.id,
                tool_call_id="call_meta_1",
            )
            assert approval is not None
            assert approval.status == ApprovalStatus.APPROVED
            assert approval.decision == {
                "value": True,
                "metadata": {
                    "source": "slack",
                    "actor": {"user_id": "U123", "display_name": "jordan"},
                },
            }


@pytest.mark.anyio
@pytest.mark.usefixtures("patched_approval_service")
class TestApplyApprovalDecisionsUpsert:
    """Pins the ON CONFLICT DO UPDATE persistence contract."""

    async def test_apply_approval_none_decision_preserves_stored_decision(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_agent_session: AgentSession,
    ) -> None:
        """decision=None must not overwrite a stored decision with JSON null."""
        service = ApprovalService(session=session, role=svc_role)
        await service.create_approval(
            ApprovalCreate(
                session_id=mock_agent_session.id,
                tool_call_id="call_preserve",
                tool_name="tool_1",
                tool_call_args={},
            )
        )

        stored = {"kind": "tool-approved", "override_args": {"a": 1}}
        await ApprovalManager.apply_approval_decisions(
            ApplyApprovalResultsActivityInputs(
                role=svc_role,
                session_id=mock_agent_session.id,
                decisions=[
                    ApprovalDecisionPayload(
                        tool_call_id="call_preserve",
                        approved=True,
                        decision=stored,
                    )
                ],
            )
        )

        await ApprovalManager.apply_approval_decisions(
            ApplyApprovalResultsActivityInputs(
                role=svc_role,
                session_id=mock_agent_session.id,
                decisions=[
                    ApprovalDecisionPayload(
                        tool_call_id="call_preserve",
                        approved=False,
                        reason="cancelled",
                        decision=None,
                    )
                ],
            )
        )

        approval = await service.get_approval_by_session_and_tool(
            session_id=mock_agent_session.id,
            tool_call_id="call_preserve",
        )
        assert approval is not None
        await session.refresh(approval)
        # Status/reason advance, but the stored decision survives.
        assert approval.status == ApprovalStatus.REJECTED
        assert approval.reason == "cancelled"
        assert approval.decision == stored

    async def test_apply_approval_last_writer_wins(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_agent_session: AgentSession,
    ) -> None:
        """Approve then deny for the same tool call results in deny."""
        service = ApprovalService(session=session, role=svc_role)

        for approved, decision, reason in (
            (True, True, "ok"),
            (False, False, "revoked"),
        ):
            await ApprovalManager.apply_approval_decisions(
                ApplyApprovalResultsActivityInputs(
                    role=svc_role,
                    session_id=mock_agent_session.id,
                    decisions=[
                        ApprovalDecisionPayload(
                            tool_call_id="call_lww",
                            approved=approved,
                            reason=reason,
                            decision=decision,
                        )
                    ],
                )
            )

        approval = await service.get_approval_by_session_and_tool(
            session_id=mock_agent_session.id,
            tool_call_id="call_lww",
        )
        assert approval is not None
        await session.refresh(approval)
        assert approval.status == ApprovalStatus.REJECTED
        assert approval.reason == "revoked"
        assert approval.decision is False

    async def test_apply_approval_sets_updated_at_on_conflict(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_agent_session: AgentSession,
    ) -> None:
        """The conflict path must set updated_at explicitly.

        onupdate does not fire for ON CONFLICT DO UPDATE. This asserts the
        column is written, not that it advances: func.now() is transaction-start
        time and this fixture runs the whole test in one transaction, so no
        unit test here can observe it move. In production each activity call
        commits its own transaction, so retries do stamp a new value.
        """
        service = ApprovalService(session=session, role=svc_role)
        approval = await service.create_approval(
            ApprovalCreate(
                session_id=mock_agent_session.id,
                tool_call_id="call_touch",
                tool_name="tool_1",
                tool_call_args={},
            )
        )

        await ApprovalManager.apply_approval_decisions(
            ApplyApprovalResultsActivityInputs(
                role=svc_role,
                session_id=mock_agent_session.id,
                decisions=[
                    ApprovalDecisionPayload(
                        tool_call_id="call_touch",
                        approved=True,
                        decision=True,
                    )
                ],
            )
        )

        await session.refresh(approval)
        assert approval.updated_at is not None
        assert approval.status == ApprovalStatus.APPROVED


@pytest.mark.anyio
class TestApplyApprovalDecisionsConcurrency:
    async def test_concurrent_apply_for_missing_approval(
        self,
        svc_role: Role,
    ) -> None:
        """Two concurrent applies for a MISSING row must both succeed with one row.

        Uses real independent sessions (no patched_approval_service) so each
        call runs in its own transaction, which is what SELECT FOR UPDATE could
        not guard: it cannot lock a row that does not exist.

        A barrier holds both calls open until each has issued its read, so the
        interleaving is deterministic. Without it asyncio.gather may serialize
        the two transactions and the race never occurs.
        """
        session_id = uuid.uuid4()
        tool_call_id = "call_concurrent"
        both_started = asyncio.Barrier(2)

        # Commit the agent session globally so independent connections satisfy the FK.
        async with get_async_session_context_manager() as setup:
            setup.add(
                AgentSession(
                    id=session_id,
                    title="Concurrency Test Session",
                    workspace_id=svc_role.workspace_id,
                    entity_type="workflow",
                    entity_id=uuid.uuid4(),
                )
            )
            await setup.commit()

        def make_input(approved: bool, reason: str):
            return ApplyApprovalResultsActivityInputs(
                role=svc_role,
                session_id=session_id,
                decisions=[
                    ApprovalDecisionPayload(
                        tool_call_id=tool_call_id,
                        approved=approved,
                        reason=reason,
                        decision=approved,
                    )
                ],
            )

        real_execute = AsyncSession.execute

        async def barriered_execute(self, statement, *args, **kwargs):
            """Sync both transactions after their first statement, before commit."""
            result = await real_execute(self, statement, *args, **kwargs)
            if not getattr(self, "_hit_barrier", False):
                self._hit_barrier = True
                await both_started.wait()
            return result

        try:
            with patch.object(AsyncSession, "execute", barriered_execute):
                results = await asyncio.gather(
                    ApprovalManager.apply_approval_decisions(
                        make_input(True, "attempt_a")
                    ),
                    ApprovalManager.apply_approval_decisions(
                        make_input(False, "attempt_b")
                    ),
                    return_exceptions=True,
                )
            errors = [r for r in results if isinstance(r, BaseException)]
            assert not errors, f"concurrent applies raised: {errors}"

            async with get_async_session_context_manager() as verify:
                rows = (
                    (
                        await verify.execute(
                            select(Approval).where(
                                Approval.workspace_id == svc_role.workspace_id,
                                Approval.session_id == session_id,
                                Approval.tool_call_id == tool_call_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].tool_name == "unknown"
        finally:
            async with get_async_session_context_manager() as cleanup:
                await cleanup.execute(
                    delete(Approval).where(
                        Approval.workspace_id == svc_role.workspace_id,
                        Approval.session_id == session_id,
                    )
                )
                await cleanup.execute(
                    delete(AgentSession).where(AgentSession.id == session_id)
                )
                await cleanup.commit()
