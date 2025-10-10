"""Unit tests for workflow executions service.

Objectives
----------
1. Test synthetic workflow failure event generation
2. Test that workflow executions without failures don't create synthetic events
3. Test that workflows with both action and workflow failures show both event types
4. Test edge cases and error conditions in event processing
5. Test workspace timeout resolution logic

"""

import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.api.enums.v1 import EventType, PendingActivityState
from temporalio.client import Client, WorkflowHandle

from tracecat.db.schemas import Workspace
from tracecat.identifiers.workflow import WorkflowExecutionID
from tracecat.types.auth import Role
from tracecat.workflow.executions.enums import (
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.models import (
    EventFailure,
    WorkflowExecutionEventCompact,
)
from tracecat.workflow.executions.service import (
    WF_FAILURE_REF,
    WorkflowExecutionsService,
)
from tracecat.workspaces.service import WorkspaceService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def mock_client() -> Mock:
    """Create a mock Temporal client."""
    return Mock(spec=Client)


@pytest.fixture
def mock_role(svc_workspace) -> Role:
    """Create a test role for the service."""
    return Role(
        type="service",
        workspace_id=svc_workspace.id,
        user_id=None,
        service_id="tracecat-service",
    )


@pytest.fixture
def workspace_with_unlimited_timeout(svc_workspace) -> Workspace:
    """Create a workspace with unlimited timeout enabled."""
    svc_workspace.settings = {
        "workflow_unlimited_timeout_enabled": True,
        "workflow_default_timeout_seconds": None,
    }
    return svc_workspace


@pytest.fixture
def workspace_with_default_timeout(svc_workspace) -> Workspace:
    """Create a workspace with a default timeout."""
    svc_workspace.settings = {
        "workflow_unlimited_timeout_enabled": False,
        "workflow_default_timeout_seconds": 600,
    }
    return svc_workspace


@pytest.fixture
def workflow_exec_id() -> WorkflowExecutionID:
    """Create a test workflow execution ID."""
    return "test-workflow-execution-123"


@pytest.fixture
def workflow_executions_service(
    mock_client: Mock, mock_role: Role
) -> WorkflowExecutionsService:
    """Create a WorkflowExecutionsService instance with mocked client."""
    return WorkflowExecutionsService(client=mock_client, role=mock_role)


def create_mock_history_event(
    event_id: int,
    event_type: EventType,
    event_time_seconds: int = 1640995200,  # 2022-01-01 00:00:00 UTC
    **attributes,
) -> Mock:
    """Create a mock Temporal history event."""
    event = Mock()
    event.event_id = event_id
    event.event_type = event_type

    # Mock timestamp
    mock_timestamp = Mock()
    mock_timestamp.ToDatetime.return_value = datetime.datetime.fromtimestamp(
        event_time_seconds, tz=datetime.UTC
    )
    event.event_time = mock_timestamp

    # Add attributes based on event type
    if event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
        mock_failure = Mock()
        mock_failure.message = attributes.get(
            "failure_message", "Workflow execution failed"
        )
        mock_failure.cause = attributes.get("failure_cause")

        failed_attrs = Mock()
        failed_attrs.failure = mock_failure
        event.workflow_execution_failed_event_attributes = failed_attrs

    elif event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
        scheduled_attrs = Mock()
        scheduled_attrs.activity_id = attributes.get("activity_id", "test-activity")
        scheduled_attrs.activity_type = Mock()
        scheduled_attrs.activity_type.name = attributes.get(
            "activity_name", "test_action"
        )
        event.activity_task_scheduled_event_attributes = scheduled_attrs

    elif event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
        mock_failure = Mock()
        mock_failure.message = attributes.get("failure_message", "Activity task failed")
        mock_failure.cause = attributes.get("failure_cause")

        failed_attrs = Mock()
        failed_attrs.failure = mock_failure
        failed_attrs.scheduled_event_id = attributes.get("scheduled_event_id", 1)
        event.activity_task_failed_event_attributes = failed_attrs

    elif event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
        completed_attrs = Mock()
        completed_attrs.scheduled_event_id = attributes.get("scheduled_event_id", 1)
        event.activity_task_completed_event_attributes = completed_attrs

    return event


@pytest.mark.anyio
class TestWorkflowExecutionEvents:
    """Test workflow execution events functionality."""

    async def test_workflow_failure_synthetic_event_creation(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that workflow failure creates a synthetic event with correct attributes."""
        # Create mock workflow failure event
        failure_event = create_mock_history_event(
            event_id=100,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,  # type: ignore
            failure_message="Workflow execution failed due to timeout",
            failure_cause={
                "type": "TimeoutError",
                "details": "Workflow timed out after 30 minutes",
            },
        )

        # Mock the workflow handle and history events
        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            """Mock async generator for history events."""
            yield failure_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        # Mock EventFailure.from_history_event
        with patch(
            "tracecat.workflow.executions.service.EventFailure.from_history_event"
        ) as mock_event_failure:
            mock_failure = EventFailure(
                message="Workflow execution failed due to timeout",
                cause={
                    "type": "TimeoutError",
                    "details": "Workflow timed out after 30 minutes",
                },
            )
            mock_event_failure.return_value = mock_failure

            # Call the method under test
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

            # Assertions
            assert len(events) == 1
            event = events[0]

            # Verify synthetic event properties
            assert event.action_ref == WF_FAILURE_REF
            assert event.action_name == WF_FAILURE_REF
            assert event.status == WorkflowExecutionEventStatus.FAILED
            assert event.source_event_id == 100
            assert event.action_error == mock_failure

            # Verify timestamps are set correctly
            expected_time = datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC)
            assert event.schedule_time == expected_time
            assert event.start_time == expected_time
            assert event.close_time == expected_time

            # Verify EventFailure.from_history_event was called
            mock_event_failure.assert_called_once_with(failure_event)

    async def test_workflow_execution_without_failures_no_synthetic_events(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that successful workflow execution doesn't create synthetic events."""
        # Create mock successful activity events
        scheduled_event = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="action-1",
            activity_name="test_action",
        )

        completed_event = create_mock_history_event(
            event_id=2,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
        )
        completed_event.activity_task_completed_event_attributes = Mock()
        completed_event.activity_task_completed_event_attributes.scheduled_event_id = 1

        # Mock the workflow handle and history events
        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            """Mock async generator for history events."""
            yield scheduled_event
            yield completed_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        # Mock WorkflowExecutionEventCompact.from_source_event
        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_compact_event = Mock(spec=WorkflowExecutionEventCompact)
            mock_compact_event.action_ref = "action-1"
            mock_compact_event.action_name = "test_action"
            mock_compact_event.status = WorkflowExecutionEventStatus.COMPLETED
            mock_compact_event.stream_id = None
            mock_compact_event.child_wf_count = 0
            mock_compact_event.loop_index = None
            mock_compact_event.action_result = None
            mock_compact_event.child_wf_wait_strategy = None
            mock_compact_event.schedule_time = None
            mock_compact_event.start_time = None
            mock_compact_event.close_time = None
            mock_from_source.return_value = mock_compact_event

            # Mock other dependencies
            with patch(
                "tracecat.workflow.executions.service.get_source_event_id"
            ) as mock_get_source:
                mock_get_source.return_value = 1

                with patch(
                    "tracecat.workflow.executions.service.is_start_event"
                ) as mock_is_start:
                    with patch(
                        "tracecat.workflow.executions.service.is_close_event"
                    ) as mock_is_close:
                        with patch(
                            "tracecat.workflow.executions.service.is_error_event"
                        ) as mock_is_error:
                            mock_is_start.return_value = False
                            mock_is_close.return_value = True
                            mock_is_error.return_value = False

                            with patch(
                                "tracecat.workflow.executions.service.get_result"
                            ) as mock_get_result:
                                mock_get_result.return_value = {"result": "success"}

                                # Call the method under test
                                events = await workflow_executions_service.list_workflow_execution_events_compact(
                                    workflow_exec_id
                                )

                                # Assertions
                                assert len(events) == 1
                                event = events[0]

                                # Verify no synthetic workflow failure event was created
                                assert event.action_ref != WF_FAILURE_REF
                                assert event.action_name != WF_FAILURE_REF
                                assert (
                                    event.status
                                    == WorkflowExecutionEventStatus.COMPLETED
                                )

    async def test_workflow_with_both_action_and_workflow_failures(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that workflow with both action failure and workflow failure shows both events."""
        # Create mock events: scheduled action, failed action, workflow failure
        scheduled_event = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="action-1",
            activity_name="failing_action",
        )

        action_failed_event = create_mock_history_event(
            event_id=2,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED,  # type: ignore
            failure_message="Action failed",
            scheduled_event_id=1,
        )

        workflow_failed_event = create_mock_history_event(
            event_id=3,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,  # type: ignore
            failure_message="Workflow execution failed",
        )

        # Mock the workflow handle and history events
        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            """Mock async generator for history events."""
            yield scheduled_event
            yield action_failed_event
            yield workflow_failed_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        # Mock WorkflowExecutionEventCompact.from_source_event
        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_action_event = Mock(spec=WorkflowExecutionEventCompact)
            mock_action_event.action_ref = "action-1"
            mock_action_event.action_name = "failing_action"
            mock_action_event.status = WorkflowExecutionEventStatus.FAILED
            mock_action_event.stream_id = None
            mock_action_event.child_wf_count = 0
            mock_action_event.loop_index = None
            mock_action_event.action_result = None
            mock_action_event.child_wf_wait_strategy = None
            mock_action_event.schedule_time = None
            mock_action_event.start_time = None
            mock_action_event.close_time = None
            mock_action_event.action_error = None
            mock_from_source.return_value = mock_action_event

            # Mock EventFailure.from_history_event
            with patch(
                "tracecat.workflow.executions.service.EventFailure.from_history_event"
            ) as mock_event_failure:
                mock_action_failure = EventFailure(message="Action failed", cause=None)
                mock_workflow_failure = EventFailure(
                    message="Workflow execution failed", cause=None
                )
                mock_event_failure.side_effect = [
                    mock_action_failure,
                    mock_workflow_failure,
                ]

                # Mock other dependencies
                with patch(
                    "tracecat.workflow.executions.service.get_source_event_id"
                ) as mock_get_source:
                    mock_get_source.return_value = 1

                    with patch(
                        "tracecat.workflow.executions.service.is_start_event"
                    ) as mock_is_start:
                        with patch(
                            "tracecat.workflow.executions.service.is_close_event"
                        ) as mock_is_close:
                            with patch(
                                "tracecat.workflow.executions.service.is_error_event"
                            ) as mock_is_error:
                                mock_is_start.return_value = False
                                mock_is_close.return_value = False
                                mock_is_error.return_value = True

                                # Call the method under test
                                events = await workflow_executions_service.list_workflow_execution_events_compact(
                                    workflow_exec_id
                                )

                                # Assertions
                                assert len(events) == 2

                                # Find action failure and workflow failure events
                                action_event = next(
                                    (
                                        e
                                        for e in events
                                        if e.action_ref != WF_FAILURE_REF
                                    ),
                                    None,
                                )
                                workflow_event = next(
                                    (
                                        e
                                        for e in events
                                        if e.action_ref == WF_FAILURE_REF
                                    ),
                                    None,
                                )

                                assert action_event is not None
                                assert workflow_event is not None

                                # Verify action failure event
                                assert action_event.action_ref == "action-1"
                                assert action_event.action_name == "failing_action"
                                assert (
                                    action_event.status
                                    == WorkflowExecutionEventStatus.FAILED
                                )
                                assert action_event.action_error == mock_action_failure

                                # Verify workflow failure synthetic event
                                assert workflow_event.action_ref == WF_FAILURE_REF
                                assert workflow_event.action_name == WF_FAILURE_REF
                                assert (
                                    workflow_event.status
                                    == WorkflowExecutionEventStatus.FAILED
                                )
                                assert (
                                    workflow_event.action_error == mock_workflow_failure
                                )

    async def test_workflow_failure_event_with_different_timestamps(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that workflow failure synthetic event uses correct timestamps."""
        failure_timestamp = 1640995260  # 2022-01-01 00:01:00 UTC

        failure_event = create_mock_history_event(
            event_id=50,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,  # type: ignore
            event_time_seconds=failure_timestamp,
            failure_message="Custom failure message",
        )

        # Mock the workflow handle and history events
        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            """Mock async generator for history events."""
            yield failure_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        # Mock EventFailure.from_history_event
        with patch(
            "tracecat.workflow.executions.service.EventFailure.from_history_event"
        ) as mock_event_failure:
            mock_failure = EventFailure(message="Custom failure message", cause=None)
            mock_event_failure.return_value = mock_failure

            # Call the method under test
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

            # Assertions
            assert len(events) == 1
            event = events[0]

            # Verify timestamps match the failure event timestamp
            expected_time = datetime.datetime.fromtimestamp(
                failure_timestamp, tz=datetime.UTC
            )
            assert event.schedule_time == expected_time
            assert event.start_time == expected_time
            assert event.close_time == expected_time

    async def test_empty_workflow_execution_history(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test handling of empty workflow execution history."""
        # Mock the workflow handle with empty history
        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            """Mock async generator with no events."""
            # Async generator that yields nothing
            if False:  # Make this an async generator that never yields
                yield

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        # Call the method under test
        events = (
            await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )
        )

        # Assertions
        assert len(events) == 0

    async def test_workflow_failure_event_with_cause(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test workflow failure synthetic event creation with detailed cause information."""
        complex_cause = {
            "type": "ApplicationError",
            "details": "Database connection failed",
            "stack_trace": ["line1", "line2", "line3"],
            "retry_count": 3,
        }

        failure_event = create_mock_history_event(
            event_id=200,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,  # type: ignore
            failure_message="Database operation failed",
            failure_cause=complex_cause,
        )

        # Mock the workflow handle and history events
        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            """Mock async generator for history events."""
            yield failure_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        # Mock EventFailure.from_history_event
        with patch(
            "tracecat.workflow.executions.service.EventFailure.from_history_event"
        ) as mock_event_failure:
            mock_failure = EventFailure(
                message="Database operation failed", cause=complex_cause
            )
            mock_event_failure.return_value = mock_failure

            # Call the method under test
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

            # Assertions
            assert len(events) == 1
            event = events[0]

            # Verify the failure object contains the complex cause
            assert event.action_error == mock_failure
            if event.action_error:
                assert event.action_error.message == "Database operation failed"
                assert event.action_error.cause == complex_cause

    async def test_pending_activity_marks_started(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that pending activities from describe mark the event as started."""

        scheduled_event = create_mock_history_event(
            event_id=10,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="action-42",
            activity_name="pending_action",
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()

        pending_activity = Mock()
        pending_activity.activity_id = "action-42"
        pending_activity.scheduled_event_id = 10
        pending_activity.state = PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
        pending_activity.last_started_time = Mock()
        pending_activity.last_started_time.ToDatetime.return_value = (
            datetime.datetime.fromtimestamp(1640995300, tz=datetime.UTC)
        )

        mock_handle.describe = AsyncMock(
            return_value=Mock(
                raw_description=Mock(pending_activities=[pending_activity])
            )
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            compact = Mock(spec=WorkflowExecutionEventCompact)
            compact.action_ref = "action-42"
            compact.stream_id = None
            compact.child_wf_count = 0
            compact.loop_index = None
            compact.action_result = None
            compact.child_wf_wait_strategy = None
            compact.schedule_time = datetime.datetime.fromtimestamp(
                1640995290, tz=datetime.UTC
            )
            compact.start_time = None
            compact.close_time = None
            compact.status = WorkflowExecutionEventStatus.SCHEDULED
            mock_from_source.return_value = compact

            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

            assert len(events) == 1
            event = events[0]
            assert event.curr_event_type == WorkflowEventType.ACTIVITY_TASK_STARTED
            assert event.status == WorkflowExecutionEventStatus.STARTED
            assert event.start_time == datetime.datetime.fromtimestamp(
                1640995300, tz=datetime.UTC
            )

    async def test_pending_activity_not_started_keeps_scheduled_state(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Pending activities that are not started must remain scheduled."""
        scheduled_event = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="action-99",
            activity_name="pending_action",
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()

        pending_activity = Mock()
        pending_activity.activity_id = "action-99"
        pending_activity.scheduled_event_id = 11
        pending_activity.state = PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED
        pending_activity.last_started_time = None

        mock_handle.describe = AsyncMock(
            return_value=Mock(
                raw_description=Mock(pending_activities=[pending_activity])
            )
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            compact = Mock(spec=WorkflowExecutionEventCompact)
            compact.action_ref = "action-99"
            compact.stream_id = None
            compact.child_wf_count = 0
            compact.loop_index = None
            compact.action_result = None
            compact.child_wf_wait_strategy = None
            compact.schedule_time = datetime.datetime.fromtimestamp(
                1640995290, tz=datetime.UTC
            )
            compact.start_time = None
            compact.close_time = None
            compact.status = WorkflowExecutionEventStatus.SCHEDULED
            compact.curr_event_type = WorkflowEventType.ACTIVITY_TASK_SCHEDULED
            mock_from_source.return_value = compact

            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

            assert len(events) == 1
            event = events[0]
            assert event.curr_event_type == WorkflowEventType.ACTIVITY_TASK_SCHEDULED
            assert event.status == WorkflowExecutionEventStatus.SCHEDULED
            assert event.start_time is None


# === Timeout Resolution Tests ===


class TestTimeoutResolution:
    """Test workspace-level timeout resolution logic."""

    @pytest.mark.anyio
    async def test_unlimited_timeout_enabled_returns_none(
        self, mock_client: Mock, workspace_with_unlimited_timeout: Workspace
    ) -> None:
        """Test that unlimited timeout enabled returns None (unlimited)."""
        role = Role(
            type="service",
            workspace_id=workspace_with_unlimited_timeout.id,
            user_id=None,
            service_id="tracecat-service",
        )
        service = WorkflowExecutionsService(client=mock_client, role=role)

        with patch.object(WorkspaceService, "with_session") as mock_ws_service:
            mock_svc = Mock()

            async def mock_get_workspace(workspace_id):
                return workspace_with_unlimited_timeout

            mock_svc.get_workspace = mock_get_workspace
            mock_ws_service.return_value.__aenter__.return_value = mock_svc

            result = await service._resolve_execution_timeout(seconds=300)

            assert result is None

    @pytest.mark.anyio
    async def test_workspace_default_timeout_used(
        self, mock_client: Mock, workspace_with_default_timeout: Workspace
    ) -> None:
        """Test that workspace default timeout is used when set."""
        role = Role(
            type="service",
            workspace_id=workspace_with_default_timeout.id,
            user_id=None,
            service_id="tracecat-service",
        )
        service = WorkflowExecutionsService(client=mock_client, role=role)

        with patch.object(WorkspaceService, "with_session") as mock_ws_service:
            mock_svc = Mock()

            async def mock_get_workspace(workspace_id):
                return workspace_with_default_timeout

            mock_svc.get_workspace = mock_get_workspace
            mock_ws_service.return_value.__aenter__.return_value = mock_svc

            result = await service._resolve_execution_timeout(seconds=300)

            assert result == datetime.timedelta(seconds=600)

    @pytest.mark.anyio
    async def test_dsl_timeout_fallback(
        self, mock_client: Mock, svc_workspace: Workspace
    ) -> None:
        """Test that DSL timeout is used when no workspace override."""
        # Workspace with no timeout settings
        svc_workspace.settings = {}

        role = Role(
            type="service",
            workspace_id=svc_workspace.id,
            user_id=None,
            service_id="tracecat-service",
        )
        service = WorkflowExecutionsService(client=mock_client, role=role)

        with patch.object(WorkspaceService, "with_session") as mock_ws_service:
            mock_svc = Mock()

            async def mock_get_workspace(workspace_id):
                return svc_workspace

            mock_svc.get_workspace = mock_get_workspace
            mock_ws_service.return_value.__aenter__.return_value = mock_svc

            result = await service._resolve_execution_timeout(seconds=300)

            assert result == datetime.timedelta(seconds=300)

    @pytest.mark.anyio
    async def test_unlimited_when_no_timeouts(
        self, mock_client: Mock, svc_workspace: Workspace
    ) -> None:
        """Test that unlimited is used when no timeouts are set."""
        # Workspace with no timeout settings
        svc_workspace.settings = {}

        role = Role(
            type="service",
            workspace_id=svc_workspace.id,
            user_id=None,
            service_id="tracecat-service",
        )
        service = WorkflowExecutionsService(client=mock_client, role=role)

        with patch.object(WorkspaceService, "with_session") as mock_ws_service:
            mock_svc = Mock()

            async def mock_get_workspace(workspace_id):
                return svc_workspace

            mock_svc.get_workspace = mock_get_workspace
            mock_ws_service.return_value.__aenter__.return_value = mock_svc

            result = await service._resolve_execution_timeout(seconds=0)

            assert result is None

    @pytest.mark.anyio
    async def test_no_workspace_uses_dsl_timeout(self, mock_client: Mock) -> None:
        """Test that DSL timeout is used when no workspace ID."""
        role = Role(
            type="service",
            workspace_id=None,
            user_id=None,
            service_id="tracecat-service",
        )
        service = WorkflowExecutionsService(client=mock_client, role=role)

        result = await service._resolve_execution_timeout(seconds=300)

        assert result == datetime.timedelta(seconds=300)

    @pytest.mark.anyio
    async def test_precedence_unlimited_overrides_all(
        self, mock_client: Mock, svc_workspace: Workspace
    ) -> None:
        """Test that unlimited timeout overrides workspace default and DSL timeout."""
        svc_workspace.settings = {
            "workflow_unlimited_timeout_enabled": True,
            "workflow_default_timeout_seconds": 600,
        }

        role = Role(
            type="service",
            workspace_id=svc_workspace.id,
            user_id=None,
            service_id="tracecat-service",
        )
        service = WorkflowExecutionsService(client=mock_client, role=role)

        with patch.object(WorkspaceService, "with_session") as mock_ws_service:
            mock_svc = Mock()

            async def mock_get_workspace(workspace_id):
                return svc_workspace

            mock_svc.get_workspace = mock_get_workspace
            mock_ws_service.return_value.__aenter__.return_value = mock_svc

            result = await service._resolve_execution_timeout(seconds=300)

            assert result is None
