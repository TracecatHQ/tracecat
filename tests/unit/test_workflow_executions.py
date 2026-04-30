"""Unit tests for workflow executions service.

Objectives
----------
1. Test synthetic workflow failure event generation
2. Test synthetic workflow completion event generation
3. Test that workflow executions without failures don't create synthetic events
4. Test that workflows with both action and workflow failures show both event types
5. Test edge cases and error conditions in event processing
6. Test workspace timeout resolution logic

"""

import datetime
import hashlib
import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import orjson
import pytest
from temporalio.api.enums.v1 import EventType, ParentClosePolicy, PendingActivityState
from temporalio.api.failure.v1 import Failure
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import Client, WorkflowHandle
from temporalio.converter import DefaultPayloadConverter

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import Workspace
from tracecat.dsl.common import AgentActionMemo, ChildWorkflowMemo, DSLInput
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext, StreamID
from tracecat.identifiers.workflow import (
    ExecutionUUID,
    WorkflowExecutionID,
    WorkflowUUID,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import ExternalObject, InlineObject, ObjectRef
from tracecat.workflow.executions.common import UnreadableTemporalPayload
from tracecat.workflow.executions.enums import (
    TriggerType,
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import (
    EventFailure,
    WorkflowExecutionEventCompact,
)
from tracecat.workflow.executions.service import (
    WF_COMPLETED_REF,
    WF_FAILURE_REF,
    WF_TRIGGER_REF,
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
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
    service = WorkflowExecutionsService(client=mock_client, role=mock_role)
    # Event-compaction tests in this module focus on history transformation, not
    # workspace visibility behavior (covered in dedicated workspace-scoping tests).
    service._is_execution_visible_in_workspace = Mock(return_value=True)
    return service


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
    event.task_id = attributes.get("task_id", event_id)

    # Mock timestamp
    event.event_time = create_mock_timestamp(event_time_seconds)

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

    elif event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
        started_attrs = Mock()
        started_attrs.input = attributes.get("workflow_input", Mock())
        event.workflow_execution_started_event_attributes = started_attrs

    return event


def create_mock_timestamp(event_time_seconds: int = 1640995200) -> Mock:
    """Create a mock Temporal timestamp."""
    mock_timestamp = Mock()
    mock_timestamp.ToDatetime.return_value = datetime.datetime.fromtimestamp(
        event_time_seconds, tz=datetime.UTC
    )
    return mock_timestamp


def create_mock_child_workflow_initiated_event(
    event_id: int,
    *,
    workflow_type_name: str = "DSLWorkflow",
    workflow_id: WorkflowExecutionID | None = None,
) -> Mock:
    """Create a mock child workflow initiated event."""
    if workflow_id is None:
        wf_id = WorkflowUUID.new_uuid4()
        exec_id = ExecutionUUID.new_uuid4()
        workflow_id = cast(WorkflowExecutionID, f"{wf_id.short()}/{exec_id.short()}")

    event = Mock()
    event.event_id = event_id
    event.event_type = EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
    event.task_id = event_id
    event.event_time = create_mock_timestamp()

    attrs = Mock()
    attrs.workflow_id = workflow_id
    attrs.workflow_type = Mock()
    attrs.workflow_type.name = workflow_type_name
    attrs.parent_close_policy = ParentClosePolicy.PARENT_CLOSE_POLICY_TERMINATE
    attrs.memo = Mock()
    attrs.input = Mock()
    event.start_child_workflow_execution_initiated_event_attributes = attrs
    return event


def create_mock_child_workflow_completed_event(
    event_id: int,
    *,
    initiated_event_id: int,
) -> Mock:
    """Create a mock child workflow completed event."""
    event = Mock()
    event.event_id = event_id
    event.event_type = EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED
    event.task_id = event_id
    event.event_time = create_mock_timestamp()

    attrs = Mock()
    attrs.initiated_event_id = initiated_event_id
    attrs.result = Mock()
    event.child_workflow_execution_completed_event_attributes = attrs
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
            "tracecat.workflow.executions.service.EventFailure.from_history_event",
            new_callable=AsyncMock,
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
            mock_event_failure.assert_awaited_once_with(failure_event)

    async def test_workflow_completed_synthetic_event_creation(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that workflow completion creates a synthetic event with result."""
        completed_event = create_mock_history_event(
            event_id=101,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED,  # type: ignore
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            """Mock async generator for history events."""
            yield completed_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        expected_result = {"status": "ok", "items_processed": 3}
        with patch(
            "tracecat.workflow.executions.service.get_result"
        ) as mock_get_result:
            mock_get_result.return_value = expected_result

            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

            assert len(events) == 1
            event = events[0]

            assert event.action_ref == WF_COMPLETED_REF
            assert event.action_name == WF_COMPLETED_REF
            assert event.status == WorkflowExecutionEventStatus.COMPLETED
            assert event.source_event_id == 101
            assert event.action_result == expected_result

            expected_time = datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC)
            assert event.schedule_time == expected_time
            assert event.start_time == expected_time
            assert event.close_time == expected_time

            mock_get_result.assert_awaited_once_with(completed_event)

    async def test_workflow_trigger_synthetic_event_creation(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that workflow start creates a synthetic trigger event."""
        started_event = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED,  # type: ignore
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield started_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        trigger_payload = {"case_id": "case-123", "severity": "high"}
        trigger_inputs = InlineObject(data=trigger_payload, typename="dict")

        with (
            patch(
                "tracecat.workflow.executions.service.extract_first",
                AsyncMock(return_value={}),
            ),
            patch(
                "tracecat.workflow.executions.service.DSLRunArgs",
                return_value=Mock(trigger_inputs=trigger_inputs),
            ),
        ):
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

        assert len(events) == 1
        event = events[0]

        expected_time = datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC)
        assert event.action_ref == WF_TRIGGER_REF
        assert event.action_name == WF_TRIGGER_REF
        assert event.curr_event_type == WorkflowEventType.WORKFLOW_EXECUTION_STARTED
        assert event.status == WorkflowExecutionEventStatus.COMPLETED
        assert event.action_input == trigger_payload
        assert event.schedule_time == expected_time
        assert event.start_time == expected_time
        assert event.close_time == expected_time

    async def test_workflow_trigger_synthetic_event_creation_without_inputs(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test that workflow trigger event is still emitted when inputs are empty."""
        started_event = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED,  # type: ignore
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield started_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        with (
            patch(
                "tracecat.workflow.executions.service.extract_first",
                AsyncMock(return_value={}),
            ),
            patch(
                "tracecat.workflow.executions.service.DSLRunArgs",
                return_value=Mock(trigger_inputs=None),
            ),
        ):
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

        assert len(events) == 1
        event = events[0]
        assert event.action_ref == WF_TRIGGER_REF
        assert event.action_input is None

    async def test_workflow_trigger_unreadable_payload_emits_sentinel(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Unreadable encrypted trigger payloads should not break compact history."""
        started_event = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED,  # type: ignore
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield started_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        unreadable = UnreadableTemporalPayload(
            error_type="TemporalPayloadCodecError",
            encoding="binary/tracecat-aes256gcm",
            payload_size_bytes=42,
        )

        with (
            patch(
                "tracecat.workflow.executions.service.extract_first",
                AsyncMock(return_value=unreadable),
            ),
            patch("tracecat.workflow.executions.service.DSLRunArgs") as mock_run_args,
        ):
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

        mock_run_args.assert_not_called()
        assert len(events) == 1
        event = events[0]
        assert event.action_ref == WF_TRIGGER_REF
        assert event.action_input == unreadable

    async def test_workflow_trigger_externalized_payload_uses_ref_backend(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Trigger payload resolution uses the backend encoded in the object ref."""
        started_event = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED,  # type: ignore
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield started_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        trigger_payload = {"case_id": "case-123", "severity": "high"}
        serialized_payload = orjson.dumps(trigger_payload)
        trigger_inputs = ExternalObject(
            ref=ObjectRef(
                bucket="workflow-results",
                key="workspace-123/run-123/trigger.json",
                size_bytes=len(serialized_payload),
                sha256=hashlib.sha256(serialized_payload).hexdigest(),
            ),
            typename="dict",
        )

        with (
            patch(
                "tracecat.workflow.executions.service.extract_first",
                AsyncMock(return_value={}),
            ),
            patch(
                "tracecat.workflow.executions.service.DSLRunArgs",
                return_value=Mock(trigger_inputs=trigger_inputs),
            ),
            patch(
                "tracecat.storage.backends.s3.cached_blob_download",
                AsyncMock(return_value=serialized_payload),
            ) as mock_cached_blob_download,
        ):
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

        assert len(events) == 1
        assert events[0].action_input == trigger_payload
        mock_cached_blob_download.assert_awaited_once()

    async def test_compact_trigger_event_sorts_before_later_actions(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Test trigger sentinel ordering relative to later action events."""
        started_event = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED,  # type: ignore
        )
        scheduled_event = create_mock_history_event(
            event_id=2,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="body-1",
            activity_name="test_action",
            event_time_seconds=1640995260,
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield started_event
            yield scheduled_event

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        action_event = WorkflowExecutionEventCompact(
            source_event_id=2,
            schedule_time=datetime.datetime.fromtimestamp(1640995260, tz=datetime.UTC),
            start_time=datetime.datetime.fromtimestamp(1640995260, tz=datetime.UTC),
            close_time=datetime.datetime.fromtimestamp(1640995261, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_COMPLETED,
            status=WorkflowExecutionEventStatus.COMPLETED,
            action_name="core.test",
            action_ref="body",
            action_input=None,
            action_result={"ok": True},
            stream_id=StreamID("<root>"),
        )

        with (
            patch(
                "tracecat.workflow.executions.service.extract_first",
                AsyncMock(return_value={}),
            ),
            patch(
                "tracecat.workflow.executions.service.DSLRunArgs",
                return_value=Mock(trigger_inputs=None),
            ),
            patch(
                "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event",
                AsyncMock(return_value=action_event),
            ),
        ):
            events = await workflow_executions_service.list_workflow_execution_events_compact(
                workflow_exec_id
            )

        assert [event.action_ref for event in events] == [WF_TRIGGER_REF, "body"]

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
                "tracecat.workflow.executions.service.EventFailure.from_history_event",
                new_callable=AsyncMock,
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
            "tracecat.workflow.executions.service.EventFailure.from_history_event",
            new_callable=AsyncMock,
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
            "tracecat.workflow.executions.service.EventFailure.from_history_event",
            new_callable=AsyncMock,
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

    async def test_compact_duplicate_actions_use_latest_source_event_id(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Repeated non-child actions in a stream should keep the latest event."""
        scheduled_1 = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="body-1",
            activity_name="execute_action_activity",
        )
        scheduled_2 = create_mock_history_event(
            event_id=2,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="body-2",
            activity_name="execute_action_activity",
        )
        completed_1 = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )
        completed_2 = create_mock_history_event(
            event_id=12,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=2,
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled_1
            yield scheduled_2
            yield completed_1
            yield completed_2

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        root_stream = StreamID.new("<root>", 0)

        source_1 = WorkflowExecutionEventCompact(
            source_event_id=1,
            schedule_time=datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.transform.reshape",
            action_ref="body",
            stream_id=root_stream,
        )
        source_2 = WorkflowExecutionEventCompact(
            source_event_id=2,
            schedule_time=datetime.datetime.fromtimestamp(1640995201, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.transform.reshape",
            action_ref="body",
            stream_id=root_stream,
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_from_source.side_effect = [source_1, source_2]
            with patch(
                "tracecat.workflow.executions.service.get_result"
            ) as mock_get_result:
                mock_get_result.side_effect = [0, 1]

                events = await workflow_executions_service.list_workflow_execution_events_compact(
                    workflow_exec_id
                )

                assert len(events) == 1
                event = events[0]
                assert event.source_event_id == 2
                assert event.action_result == 1
                assert event.status == WorkflowExecutionEventStatus.COMPLETED
                assert (
                    event.curr_event_type == WorkflowEventType.ACTIVITY_TASK_COMPLETED
                )

    async def test_compact_scheduled_activity_preserves_mask_output_metadata(
        self,
    ) -> None:
        """Compact events keep redaction metadata while exposing only task args."""
        scheduled = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="masked-action",
            activity_name="execute_action_activity",
        )
        action_name = "core.transform.reshape"
        wf_id = WorkflowUUID.new_uuid4()
        exec_id = ExecutionUUID.new_uuid4()
        run_input = RunActionInput(
            task=ActionStatement(
                action=action_name,
                args={"value": "visible-input"},
                ref="masked_action",
                mask_output=True,
            ),
            exec_context={"ACTIONS": {}, "TRIGGER": None},
            run_context=RunContext(
                wf_id=wf_id,
                wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
                wf_run_id=uuid.uuid4(),
                environment="test",
                logical_time=datetime.datetime.now(datetime.UTC),
            ),
            registry_lock=RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={action_name: "tracecat_registry"},
            ),
        )

        with patch(
            "tracecat.workflow.executions.schemas.extract_first",
            AsyncMock(return_value=run_input.model_dump(mode="json")),
        ):
            event = await WorkflowExecutionEventCompact.from_scheduled_activity(
                scheduled
            )

        assert event is not None
        assert event.action_input == {"value": "visible-input"}
        assert event.should_mask_output is True

    async def test_compact_child_workflow_preserves_mask_output_metadata(
        self,
    ) -> None:
        """Child workflow compact events restore redaction metadata from memo."""
        initiated = create_mock_child_workflow_initiated_event(event_id=1)
        unreadable = UnreadableTemporalPayload(
            error_type="decode_error",
            encoding="json/plain",
            payload_size_bytes=0,
        )

        with (
            patch(
                "tracecat.workflow.executions.schemas.ChildWorkflowMemo.from_temporal",
                new_callable=AsyncMock,
            ) as mock_memo,
            patch(
                "tracecat.workflow.executions.schemas.extract_first",
                new_callable=AsyncMock,
            ) as mock_extract_first,
        ):
            mock_memo.return_value = ChildWorkflowMemo(
                action_ref="masked_child",
                mask_output=True,
            )
            mock_extract_first.return_value = unreadable

            event = await WorkflowExecutionEventCompact.from_initiated_child_workflow(
                initiated
            )

        assert event is not None
        assert event.action_ref == "masked_child"
        assert event.should_mask_output is True

    async def test_compact_agent_workflow_preserves_mask_output_metadata(
        self,
    ) -> None:
        """Agent compact events restore redaction metadata from memo."""
        initiated = create_mock_child_workflow_initiated_event(
            event_id=1,
            workflow_type_name="DurableAgentWorkflow",
        )
        unreadable = UnreadableTemporalPayload(
            error_type="decode_error",
            encoding="json/plain",
            payload_size_bytes=0,
        )

        with (
            patch(
                "tracecat.workflow.executions.schemas.AgentActionMemo.from_temporal",
                new_callable=AsyncMock,
            ) as mock_memo,
            patch(
                "tracecat.workflow.executions.schemas.extract_first",
                new_callable=AsyncMock,
            ) as mock_extract_first,
        ):
            mock_memo.return_value = AgentActionMemo(
                action_ref="masked_agent",
                mask_output=True,
            )
            mock_extract_first.return_value = unreadable

            event = await WorkflowExecutionEventCompact.from_initiated_child_workflow(
                initiated
            )

        assert event is not None
        assert event.action_ref == "masked_agent"
        assert event.should_mask_output is True

    async def test_compact_masked_action_result_redacts_leaf_values(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Masked action results preserve shape for JSONPath copy flows."""
        scheduled = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="masked-action",
            activity_name="execute_action_activity",
        )
        completed = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled
            yield completed

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        root_stream = StreamID.new("<root>", 0)

        source = WorkflowExecutionEventCompact(
            source_event_id=1,
            schedule_time=datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.transform.reshape",
            action_ref="masked_action",
            action_input={"value": "visible-input"},
            stream_id=root_stream,
        )
        source.set_mask_output(True)

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_from_source.return_value = source
            with patch(
                "tracecat.workflow.executions.service.get_result"
            ) as mock_get_result:
                mock_get_result.return_value = {
                    "secret": "hidden-output",
                    "nested": {
                        "token": "secret-token",
                        "none_value": None,
                        "empty_object": {},
                        "items": [
                            "first",
                            {"id": 123, "enabled": True},
                            [],
                        ],
                    },
                }

                events = await workflow_executions_service.list_workflow_execution_events_compact(
                    workflow_exec_id
                )

                assert len(events) == 1
                event = events[0]
                assert event.action_input == {"value": "visible-input"}
                assert event.action_result == {
                    "secret": "[REDACTED]",
                    "nested": {
                        "token": "[REDACTED]",
                        "none_value": "[REDACTED]",
                        "empty_object": {},
                        "items": [
                            "[REDACTED]",
                            {"id": "[REDACTED]", "enabled": "[REDACTED]"},
                            [],
                        ],
                    },
                }

    async def test_non_compact_masked_activity_result_redacts_leaf_values(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Masked activity outputs are redacted in the standard execution view."""
        scheduled = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="masked-action",
            activity_name="execute_action_activity",
        )
        completed = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )
        mock_handle = Mock(spec=WorkflowHandle)
        mock_handle.fetch_history = AsyncMock(
            return_value=Mock(events=[scheduled, completed])
        )
        mock_handle.describe = AsyncMock(return_value=Mock())
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        action_name = "core.transform.reshape"
        wf_id = WorkflowUUID.new_uuid4()
        exec_id = ExecutionUUID.new_uuid4()
        run_input = RunActionInput(
            task=ActionStatement(
                action=action_name,
                args={"value": "visible-input"},
                ref="masked_action",
                mask_output=True,
            ),
            exec_context={"ACTIONS": {}, "TRIGGER": None},
            run_context=RunContext(
                wf_id=wf_id,
                wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
                wf_run_id=uuid.uuid4(),
                environment="test",
                logical_time=datetime.datetime.now(datetime.UTC),
            ),
            registry_lock=RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions={action_name: "tracecat_registry"},
            ),
        )

        with (
            patch(
                "tracecat.workflow.executions.schemas.extract_first",
                new_callable=AsyncMock,
            ) as mock_source_extract_first,
            patch(
                "tracecat.workflow.executions.service.extract_first",
                new_callable=AsyncMock,
            ) as mock_result_extract_first,
        ):
            mock_source_extract_first.return_value = run_input.model_dump(mode="json")
            mock_result_extract_first.return_value = {
                "secret": "hidden-output",
                "nested": {"token": "secret-token"},
            }

            events = await workflow_executions_service.list_workflow_execution_events(
                workflow_exec_id
            )

        completed_event = next(
            event
            for event in events
            if event.event_type == WorkflowEventType.ACTIVITY_TASK_COMPLETED
        )
        assert completed_event.result == {
            "secret": "[REDACTED]",
            "nested": {"token": "[REDACTED]"},
        }

    async def test_non_compact_masked_child_workflow_result_redacts_leaf_values(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Masked child workflow outputs are redacted in the standard execution view."""
        initiated = create_mock_child_workflow_initiated_event(event_id=1)
        completed = create_mock_child_workflow_completed_event(
            event_id=11,
            initiated_event_id=1,
        )
        unreadable = UnreadableTemporalPayload(
            error_type="decode_error",
            encoding="json/plain",
            payload_size_bytes=0,
        )
        mock_handle = Mock(spec=WorkflowHandle)
        mock_handle.fetch_history = AsyncMock(
            return_value=Mock(events=[initiated, completed])
        )
        mock_handle.describe = AsyncMock(return_value=Mock())
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )

        with (
            patch(
                "tracecat.workflow.executions.schemas.ChildWorkflowMemo.from_temporal",
                new_callable=AsyncMock,
            ) as mock_memo,
            patch(
                "tracecat.workflow.executions.schemas.extract_first",
                new_callable=AsyncMock,
            ) as mock_source_extract_first,
            patch(
                "tracecat.workflow.executions.service.extract_first",
                new_callable=AsyncMock,
            ) as mock_result_extract_first,
        ):
            mock_memo.return_value = ChildWorkflowMemo(
                action_ref="masked_child",
                mask_output=True,
            )
            mock_source_extract_first.return_value = unreadable
            mock_result_extract_first.return_value = {"secret": "child-output"}

            events = await workflow_executions_service.list_workflow_execution_events(
                workflow_exec_id
            )

        completed_event = next(
            event
            for event in events
            if event.event_type == WorkflowEventType.CHILD_WORKFLOW_EXECUTION_COMPLETED
        )
        assert completed_event.result == {"secret": "[REDACTED]"}

    async def test_compact_duplicate_actions_latest_failure_wins(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Latest failed iteration should replace an earlier successful iteration."""
        scheduled_1 = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="body-1",
            activity_name="execute_action_activity",
        )
        scheduled_2 = create_mock_history_event(
            event_id=2,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="body-2",
            activity_name="execute_action_activity",
        )
        completed_1 = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )
        failed_2 = create_mock_history_event(
            event_id=12,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED,  # type: ignore
            scheduled_event_id=2,
            failure_message="Body failed on latest iteration",
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled_1
            yield scheduled_2
            yield completed_1
            yield failed_2

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        root_stream = StreamID.new("<root>", 0)

        source_1 = WorkflowExecutionEventCompact(
            source_event_id=1,
            schedule_time=datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.transform.reshape",
            action_ref="body",
            stream_id=root_stream,
        )
        source_2 = WorkflowExecutionEventCompact(
            source_event_id=2,
            schedule_time=datetime.datetime.fromtimestamp(1640995201, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.transform.reshape",
            action_ref="body",
            stream_id=root_stream,
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_from_source.side_effect = [source_1, source_2]
            with patch(
                "tracecat.workflow.executions.service.get_result"
            ) as mock_get_result:
                mock_get_result.return_value = "ok"
                with patch(
                    "tracecat.workflow.executions.service.EventFailure.from_history_event",
                    new_callable=AsyncMock,
                ) as mock_event_failure:
                    mock_event_failure.return_value = EventFailure(
                        message="Body failed on latest iteration",
                        cause=None,
                    )

                    events = await workflow_executions_service.list_workflow_execution_events_compact(
                        workflow_exec_id
                    )

                    assert len(events) == 1
                    event = events[0]
                    assert event.source_event_id == 2
                    assert event.status == WorkflowExecutionEventStatus.FAILED
                    assert event.action_error is not None
                    assert (
                        event.action_error.message == "Body failed on latest iteration"
                    )

    async def test_compact_duplicate_actions_in_different_streams_are_distinct(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Same action ref in different streams should keep separate events."""
        scheduled_1 = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="body-root",
            activity_name="execute_action_activity",
        )
        scheduled_2 = create_mock_history_event(
            event_id=2,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="body-stream",
            activity_name="execute_action_activity",
        )
        completed_1 = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )
        completed_2 = create_mock_history_event(
            event_id=12,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=2,
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled_1
            yield scheduled_2
            yield completed_1
            yield completed_2

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        root_stream = StreamID.new("<root>", 0)
        scatter_stream = StreamID.new("scatter", 0)

        source_1 = WorkflowExecutionEventCompact(
            source_event_id=1,
            schedule_time=datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.transform.reshape",
            action_ref="body",
            stream_id=root_stream,
        )
        source_2 = WorkflowExecutionEventCompact(
            source_event_id=2,
            schedule_time=datetime.datetime.fromtimestamp(1640995201, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.transform.reshape",
            action_ref="body",
            stream_id=scatter_stream,
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_from_source.side_effect = [source_1, source_2]
            with patch(
                "tracecat.workflow.executions.service.get_result"
            ) as mock_get_result:
                mock_get_result.side_effect = [10, 20]

                events = await workflow_executions_service.list_workflow_execution_events_compact(
                    workflow_exec_id
                )

                assert len(events) == 2
                stream2result = {
                    event.stream_id: event.action_result for event in events
                }
                assert stream2result[root_stream] == 10
                assert stream2result[scatter_stream] == 20

    async def test_compact_loop_start_exposes_while_iteration(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Do-while metadata should be exposed separately from child-workflow loop_index."""
        scheduled = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="loop-start-1",
            activity_name="noop_loop_start_activity",
        )
        completed = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled
            yield completed

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        root_stream = StreamID.new("<root>", 0)

        source = WorkflowExecutionEventCompact(
            source_event_id=1,
            schedule_time=datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.loop.start",
            action_ref="loop_start",
            stream_id=root_stream,
            loop_index=None,
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_from_source.return_value = source
            with patch(
                "tracecat.workflow.executions.service.get_result"
            ) as mock_get_result:
                mock_get_result.return_value = {"result": {"data": {"iteration": 3}}}

                events = await workflow_executions_service.list_workflow_execution_events_compact(
                    workflow_exec_id
                )

                assert len(events) == 1
                event = events[0]
                assert event.action_name == "core.loop.start"
                assert event.while_iteration == 3
                assert event.while_continue is None
                assert event.loop_index is None

    async def test_compact_loop_end_exposes_while_continue(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Loop end continue/exit metadata is available in compact payload."""
        scheduled = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="loop-end-1",
            activity_name="noop_loop_end_activity",
        )
        completed = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled
            yield completed

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        root_stream = StreamID.new("<root>", 0)

        source = WorkflowExecutionEventCompact(
            source_event_id=1,
            schedule_time=datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.loop.end",
            action_ref="loop_end",
            stream_id=root_stream,
            loop_index=None,
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_from_source.return_value = source
            with patch(
                "tracecat.workflow.executions.service.get_result"
            ) as mock_get_result:
                mock_get_result.return_value = {"result": {"data": {"continue": False}}}

                events = await workflow_executions_service.list_workflow_execution_events_compact(
                    workflow_exec_id
                )

                assert len(events) == 1
                event = events[0]
                assert event.action_name == "core.loop.end"
                assert event.while_iteration is None
                assert event.while_continue is False
                assert event.loop_index is None

    async def test_compact_looped_child_workflow_results_still_aggregate(
        self,
        workflow_executions_service: WorkflowExecutionsService,
        workflow_exec_id: WorkflowExecutionID,
    ) -> None:
        """Looped child workflow events remain list-aggregated in compact view."""
        scheduled_1 = create_mock_history_event(
            event_id=1,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="subflow-1",
            activity_name="execute_action_activity",
        )
        scheduled_2 = create_mock_history_event(
            event_id=2,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,  # type: ignore
            activity_id="subflow-2",
            activity_name="execute_action_activity",
        )
        completed_1 = create_mock_history_event(
            event_id=11,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=1,
        )
        completed_2 = create_mock_history_event(
            event_id=12,
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,  # type: ignore
            scheduled_event_id=2,
        )

        mock_handle = Mock(spec=WorkflowHandle)

        async def mock_fetch_history_events(**kwargs):
            yield scheduled_1
            yield scheduled_2
            yield completed_1
            yield completed_2

        mock_handle.fetch_history_events.return_value = mock_fetch_history_events()
        mock_handle.describe = AsyncMock(
            return_value=Mock(raw_description=Mock(pending_activities=[]))
        )
        workflow_executions_service._client.get_workflow_handle_for = Mock(
            return_value=mock_handle
        )
        root_stream = StreamID.new("<root>", 0)

        source_1 = WorkflowExecutionEventCompact(
            source_event_id=1,
            schedule_time=datetime.datetime.fromtimestamp(1640995200, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.workflow.execute",
            action_ref="run_subflow",
            stream_id=root_stream,
            loop_index=0,
        )
        source_2 = WorkflowExecutionEventCompact(
            source_event_id=2,
            schedule_time=datetime.datetime.fromtimestamp(1640995201, tz=datetime.UTC),
            curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
            status=WorkflowExecutionEventStatus.SCHEDULED,
            action_name="core.workflow.execute",
            action_ref="run_subflow",
            stream_id=root_stream,
            loop_index=1,
        )

        with patch(
            "tracecat.workflow.executions.service.WorkflowExecutionEventCompact.from_source_event"
        ) as mock_from_source:
            mock_from_source.side_effect = [source_1, source_2]
            with patch(
                "tracecat.workflow.executions.service.get_result"
            ) as mock_get_result:
                mock_get_result.side_effect = ["subflow-0", "subflow-1"]

                events = await workflow_executions_service.list_workflow_execution_events_compact(
                    workflow_exec_id
                )

                assert len(events) == 1
                event = events[0]
                assert event.action_result == ["subflow-0", "subflow-1"]
                assert event.child_wf_count == 1
                assert event.loop_index == 0
                assert event.while_iteration is None
                assert event.while_continue is None


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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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


def test_event_failure_extract_root_cause_message_returns_deepest_message() -> None:
    cause = {
        "message": "Activity task failed",
        "cause": {
            "message": "ApplicationError: Workflow dispatch failed",
            "cause": {"message": "Workflow alias 'invalid' not found"},
        },
    }

    result = EventFailure.extract_root_cause_message(cause)

    assert result == "Workflow alias 'invalid' not found"


def test_event_failure_extract_root_cause_message_handles_empty_and_cycles() -> None:
    cyclic_cause: dict[str, Any] = {
        "message": "Top-level failure",
        "cause": {"message": "   "},
    }
    nested = cast(dict[str, Any], cyclic_cause["cause"])
    nested["cause"] = cyclic_cause

    result = EventFailure.extract_root_cause_message(cyclic_cause)

    assert result == "Top-level failure"


@pytest.mark.anyio
async def test_event_failure_from_history_event_populates_root_cause_message() -> None:
    failure_cause = Mock()
    event = create_mock_history_event(
        event_id=1,
        event_type=cast(EventType, EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED),
        failure_message="Workflow execution failed",
        failure_cause=failure_cause,
    )
    nested_cause = {
        "message": "Workflow execution failed",
        "cause": {
            "message": "Activity task failed",
            "cause": {"message": "Workflow alias 'invalid' not found"},
        },
    }

    with patch(
        "tracecat.workflow.executions.schemas.MessageToDict",
        return_value=nested_cause,
    ):
        failure = await EventFailure.from_history_event(event)

    assert failure.message == "Workflow execution failed"
    assert failure.cause is None
    assert failure.root_cause_message == "Workflow alias 'invalid' not found"


@pytest.mark.anyio
async def test_event_failure_from_history_event_decodes_encoded_attribute_messages() -> (
    None
):
    failure_proto = Failure(message="Encoded failure")
    failure_proto.application_failure_info.type = "EncodedFailure"
    failure_proto.encoded_attributes.CopyFrom(
        DefaultPayloadConverter().to_payloads(
            [
                {
                    "message": "Outer failure with Authorization: Bearer abc123",
                    "stack_trace": "",
                }
            ]
        )[0]
    )
    failure_proto.cause.message = "Encoded failure"
    failure_proto.cause.application_failure_info.type = "RootFailure"
    failure_proto.cause.encoded_attributes.CopyFrom(
        DefaultPayloadConverter().to_payloads(
            [
                {
                    "message": "Root failure with api_key=topsecret",
                    "stack_trace": "",
                }
            ]
        )[0]
    )
    event = HistoryEvent(
        event_id=1,
        event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,
    )
    event.workflow_execution_failed_event_attributes.failure.CopyFrom(failure_proto)

    failure = await EventFailure.from_history_event(event)

    assert failure.message == "Outer failure with Authorization: Bearer [REDACTED]"
    assert failure.root_cause_message == "Root failure with api_key=[REDACTED]"
    assert failure.cause is None


@pytest.mark.anyio
async def test_event_failure_from_history_event_sanitizes_sensitive_data() -> None:
    failure_cause = Mock()
    event = create_mock_history_event(
        event_id=1,
        event_type=cast(EventType, EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED),
        failure_message="Request failed with Authorization: Bearer abc123",
        failure_cause=failure_cause,
    )
    nested_cause = {
        "message": "Outer failure",
        "cause": {
            "message": "Call failed: api_key=topsecret&foo=bar",
            "cause": {"message": "postgresql://user:password@localhost/db"},
        },
    }

    with patch(
        "tracecat.workflow.executions.schemas.MessageToDict",
        return_value=nested_cause,
    ):
        failure = await EventFailure.from_history_event(event)

    assert failure.message == "Request failed with Authorization: Bearer [REDACTED]"
    assert failure.root_cause_message == "postgresql://user:[REDACTED]@localhost/db"
    assert failure.cause is None


@pytest.mark.anyio
async def test_event_failure_from_history_event_include_raw_cause() -> None:
    failure_cause = Mock()
    event = create_mock_history_event(
        event_id=1,
        event_type=cast(EventType, EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED),
        failure_message="Workflow execution failed",
        failure_cause=failure_cause,
    )
    nested_cause = {"message": "raw-cause"}

    with patch(
        "tracecat.workflow.executions.schemas.MessageToDict",
        return_value=nested_cause,
    ):
        failure = await EventFailure.from_history_event(event, include_raw_cause=True)

    assert failure.cause == nested_cause


class TestWorkflowStartAcknowledgement:
    @pytest.mark.anyio
    async def test_create_workflow_execution_wait_for_start_acknowledges_temporal_start(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        mock_client.start_workflow = AsyncMock(return_value=Mock(spec=WorkflowHandle))

        dsl = DSLInput.model_validate(
            {
                "title": "Webhook test workflow",
                "description": "Test workflow",
                "entrypoint": {"ref": "start"},
                "actions": [{"ref": "start", "action": "core.noop"}],
                "config": {"enable_runtime_tests": False},
            }
        )
        wf_id = WorkflowUUID.new("wf_4itKqkgCZrLhgYiq5L211X")

        with patch.object(
            service, "_resolve_execution_timeout", AsyncMock(return_value=None)
        ):
            response = await cast(
                Any, service
            ).create_workflow_execution_wait_for_start(
                dsl=dsl,
                wf_id=wf_id,
                payload=None,
                trigger_type=TriggerType.WEBHOOK,
            )

        assert response["wf_id"] == wf_id
        assert response["wf_exec_id"].startswith(f"{wf_id.short()}/exec_")
        mock_client.start_workflow.assert_awaited_once()
        assert (
            mock_client.start_workflow.await_args.kwargs["id"] == response["wf_exec_id"]
        )


@pytest.mark.anyio
async def test_list_executions_paginated_emits_prev_cursor_history(
    workflow_executions_service: WorkflowExecutionsService,
    mock_client: Mock,
) -> None:
    first_page_items = [Mock(id="wf_first/exec_1")]
    second_page_items = [Mock(id="wf_first/exec_2")]
    calls: list[bytes | None] = []

    class _Iterator:
        def __init__(self, items: list[Any], next_page_token: bytes | None) -> None:
            self.current_page = items
            self.next_page_token = next_page_token

        async def fetch_next_page(self, *, page_size: int) -> None:
            assert page_size == 1

    def _list_workflows(
        *,
        query: str | None = None,
        page_size: int,
        next_page_token: bytes | None = None,
    ) -> _Iterator:
        _ = query
        assert page_size == 1
        calls.append(next_page_token)
        if next_page_token is None:
            return _Iterator(first_page_items, b"page-2")
        if next_page_token == b"page-2":
            return _Iterator(second_page_items, b"page-3")
        raise AssertionError(f"Unexpected page token: {next_page_token!r}")

    mock_client.list_workflows = Mock(side_effect=_list_workflows)

    first_page = await workflow_executions_service.list_executions_paginated(
        pagination=CursorPaginationParams(limit=1),
    )
    assert first_page.prev_cursor is None
    assert first_page.has_previous is False
    assert first_page.next_cursor is not None

    second_page = await workflow_executions_service.list_executions_paginated(
        pagination=CursorPaginationParams(limit=1, cursor=first_page.next_cursor),
    )
    assert second_page.items == second_page_items
    assert second_page.prev_cursor is not None
    assert second_page.has_previous is True

    rewound_page = await workflow_executions_service.list_executions_paginated(
        pagination=CursorPaginationParams(limit=1, cursor=second_page.prev_cursor),
    )
    assert rewound_page.items == first_page_items
    assert rewound_page.has_previous is False
    assert rewound_page.prev_cursor is None
    assert calls == [None, b"page-2", None]
