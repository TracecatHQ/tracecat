import uuid

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import (
    AssigneeChangedEvent,
    CaseCreate,
    ClosedEvent,
    CreatedEvent,
    FieldDiff,
    FieldsChangedEvent,
    PriorityChangedEvent,
    ReopenedEvent,
    SeverityChangedEvent,
    StatusChangedEvent,
    UpdatedEvent,
)
from tracecat.cases.service import CaseEventsService, CasesService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_events_service_initialization_requires_workspace(
    session: AsyncSession,
) -> None:
    """Test that events service initialization requires a workspace ID."""
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
        CaseEventsService(session=session, role=role_without_workspace)


@pytest.fixture
async def case_events_service(
    session: AsyncSession, svc_role: Role
) -> CaseEventsService:
    """Create a case events service instance for testing."""
    return CaseEventsService(session=session, role=svc_role)


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    """Create a cases service instance for testing."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def test_case(cases_service: CasesService):
    """Create a test case for event testing."""
    case_params = CaseCreate(
        summary="Test Case for Events",
        description="This is a test case for event testing",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    return await cases_service.create_case(case_params)


@pytest.mark.anyio
class TestCaseEventsService:
    async def test_create_case_created_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a case created event."""
        event_data = CreatedEvent(type=CaseEventType.CASE_CREATED)

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.CASE_CREATED
        assert created_event.user_id == case_events_service.role.user_id
        assert created_event.owner_id == case_events_service.workspace_id
        assert created_event.data is not None

    async def test_create_status_changed_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a status changed event."""
        event_data = StatusChangedEvent(
            type=CaseEventType.STATUS_CHANGED,
            old=CaseStatus.NEW,
            new=CaseStatus.IN_PROGRESS,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.STATUS_CHANGED
        assert created_event.user_id == case_events_service.role.user_id
        assert created_event.data["old"] == "new"
        assert created_event.data["new"] == "in_progress"

    async def test_create_priority_changed_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a priority changed event."""
        event_data = PriorityChangedEvent(
            type=CaseEventType.PRIORITY_CHANGED,
            old=CasePriority.LOW,
            new=CasePriority.HIGH,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.PRIORITY_CHANGED
        assert created_event.data["old"] == "low"
        assert created_event.data["new"] == "high"

    async def test_create_severity_changed_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a severity changed event."""
        event_data = SeverityChangedEvent(
            type=CaseEventType.SEVERITY_CHANGED,
            old=CaseSeverity.LOW,
            new=CaseSeverity.CRITICAL,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.SEVERITY_CHANGED
        assert created_event.data["old"] == "low"
        assert created_event.data["new"] == "critical"

    async def test_create_assignee_changed_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating an assignee changed event."""
        from_assignee_id = uuid.uuid4()
        to_assignee_id = uuid.uuid4()

        event_data = AssigneeChangedEvent(
            type=CaseEventType.ASSIGNEE_CHANGED,
            old=from_assignee_id,
            new=to_assignee_id,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.ASSIGNEE_CHANGED
        assert created_event.data["old"] == str(from_assignee_id)
        assert created_event.data["new"] == str(to_assignee_id)

    async def test_create_assignee_removed_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating an assignee removed event."""
        from_assignee_id = uuid.uuid4()

        event_data = AssigneeChangedEvent(
            type=CaseEventType.ASSIGNEE_CHANGED,
            old=from_assignee_id,
            new=None,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.ASSIGNEE_CHANGED
        assert created_event.data["old"] == str(from_assignee_id)
        assert created_event.data["new"] is None

    async def test_create_case_closed_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a case closed event."""
        event_data = ClosedEvent(
            type=CaseEventType.CASE_CLOSED,
            old=test_case.status,
            new=CaseStatus.CLOSED,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.CASE_CLOSED
        assert created_event.data["old"] == test_case.status.value
        assert created_event.data["new"] == CaseStatus.CLOSED.value
        # Verify the workflow execution ID is included if present
        assert "wf_exec_id" in created_event.data

    async def test_create_case_reopened_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a case reopened event."""
        test_case.status = CaseStatus.CLOSED
        await case_events_service.session.commit()

        event_data = ReopenedEvent(
            type=CaseEventType.CASE_REOPENED,
            old=CaseStatus.CLOSED,
            new=CaseStatus.IN_PROGRESS,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.CASE_REOPENED
        assert created_event.data["old"] == CaseStatus.CLOSED.value
        assert created_event.data["new"] == CaseStatus.IN_PROGRESS.value
        # Verify the workflow execution ID is included if present
        assert "wf_exec_id" in created_event.data

    async def test_create_case_summary_updated_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a case summary updated event."""
        old_summary = test_case.summary
        new_summary = "Updated case summary for event test"
        event_data = UpdatedEvent(
            type=CaseEventType.CASE_UPDATED,
            field="summary",
            old=old_summary,
            new=new_summary,
        )
        created_event = await case_events_service.create_event(test_case, event_data)
        assert created_event.type == CaseEventType.CASE_UPDATED
        assert created_event.data["field"] == "summary"
        assert created_event.data["old"] == old_summary
        assert created_event.data["new"] == new_summary

    async def test_create_fields_changed_event(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating a fields changed event."""
        event_data = FieldsChangedEvent(
            type=CaseEventType.FIELDS_CHANGED,
            changes=[
                FieldDiff(field="field1", old="old_value", new="new_value"),
                FieldDiff(field="field2", old=123, new=456),
                FieldDiff(field="field3", old=None, new="added"),
            ],
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == CaseEventType.FIELDS_CHANGED
        assert len(created_event.data["changes"]) == 3
        assert created_event.data["changes"][0]["field"] == "field1"
        assert created_event.data["changes"][0]["old"] == "old_value"
        assert created_event.data["changes"][0]["new"] == "new_value"
        assert created_event.data["changes"][2]["field"] == "field3"
        assert created_event.data["changes"][2]["old"] is None
        assert created_event.data["changes"][2]["new"] == "added"

    async def test_list_events_empty_case(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test listing events for a case with only the initial creation event."""
        events = await case_events_service.list_events(test_case)
        # Should have exactly 1 event - the automatic case creation event
        assert len(events) == 1
        assert events[0].type == CaseEventType.CASE_CREATED

    async def test_list_events_with_multiple_events(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test listing events for a case with multiple events."""
        # Add additional events beyond the automatic case creation event
        event1_data = StatusChangedEvent(
            type=CaseEventType.STATUS_CHANGED,
            old=CaseStatus.NEW,
            new=CaseStatus.IN_PROGRESS,
        )

        event2_data = PriorityChangedEvent(
            type=CaseEventType.PRIORITY_CHANGED,
            old=CasePriority.LOW,
            new=CasePriority.HIGH,
        )

        created_event1 = await case_events_service.create_event(test_case, event1_data)
        created_event2 = await case_events_service.create_event(test_case, event2_data)

        events = await case_events_service.list_events(test_case)

        # Should have 3 events: case creation + 2 additional events
        assert len(events) == 3

        # The manually created events should be in the list
        event_ids = {event.id for event in events}
        assert created_event1.id in event_ids
        assert created_event2.id in event_ids

        # Events should be ordered by creation time (newest first)
        assert events[0].id == created_event2.id  # Priority change (most recent)
        assert events[1].id == created_event1.id  # Status change
        # events[2] should be the automatic case creation event

    async def test_list_events_ordering(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test that events are returned in descending order of creation time."""
        import asyncio

        await asyncio.sleep(0.01)

        event_data = StatusChangedEvent(
            type=CaseEventType.STATUS_CHANGED,
            old=CaseStatus.NEW,
            new=CaseStatus.IN_PROGRESS,
        )
        created_event = await case_events_service.create_event(test_case, event_data)

        events = await case_events_service.list_events(test_case)

        # Should have 2 events: automatic case creation + manually created status change
        assert len(events) == 2
        assert events[0].id == created_event.id  # Most recent event first
        assert events[0].type == CaseEventType.STATUS_CHANGED
        assert (
            events[1].type == CaseEventType.CASE_CREATED
        )  # Original case creation event

    async def test_create_event_with_workflow_execution_id(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test creating an event with workflow execution ID."""
        wf_exec_id = "workflow-123.execution-456"

        event_data = StatusChangedEvent(
            type=CaseEventType.STATUS_CHANGED,
            old=CaseStatus.NEW,
            new=CaseStatus.IN_PROGRESS,
            wf_exec_id=wf_exec_id,
        )

        await case_events_service.create_event(test_case, event_data)

        events = await case_events_service.list_events(test_case)
        # Should have 2 events: automatic case creation + status change with workflow ID
        assert len(events) == 2
        # Find the event with the workflow execution ID
        wf_event = next(
            (e for e in events if e.data.get("wf_exec_id") == wf_exec_id), None
        )
        assert wf_event is not None
        assert wf_event.data["wf_exec_id"] == wf_exec_id

    async def test_create_event_with_different_user_ids(
        self,
        case_events_service: CaseEventsService,
        test_case,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Test creating events with different user IDs."""
        # Create an event with the original service (original user)
        event1_data = StatusChangedEvent(
            type=CaseEventType.STATUS_CHANGED,
            old=CaseStatus.NEW,
            new=CaseStatus.IN_PROGRESS,
        )
        created_event1 = await case_events_service.create_event(test_case, event1_data)

        # Create a service with a different user
        different_user_id = uuid.uuid4()
        different_role = Role(
            type="service",
            user_id=different_user_id,
            workspace_id=svc_role.workspace_id,
            service_id=svc_role.service_id,
            access_level=AccessLevel.BASIC,
        )
        different_service = CaseEventsService(session=session, role=different_role)

        # Create an event with the different service (different user)
        event2_data = PriorityChangedEvent(
            type=CaseEventType.PRIORITY_CHANGED,
            old=CasePriority.MEDIUM,
            new=CasePriority.HIGH,
        )
        created_event2 = await different_service.create_event(test_case, event2_data)

        assert created_event1.user_id == case_events_service.role.user_id
        assert created_event2.user_id == different_user_id
        assert created_event1.user_id != created_event2.user_id

        events = await case_events_service.list_events(test_case)
        # Should have 3 events: automatic case creation + 2 manually created events
        assert len(events) == 3

    async def test_event_data_serialization(
        self, case_events_service: CaseEventsService, test_case
    ) -> None:
        """Test that event data is properly serialized and stored."""
        complex_changes = [
            FieldDiff(field="string_field", old=None, new="test_value"),
            FieldDiff(field="numeric_field", old=None, new=42),
            FieldDiff(field="boolean_field", old=None, new=True),
            FieldDiff(field="list_field", old=None, new=["item1", "item2", "item3"]),
            FieldDiff(
                field="nested_dict",
                old=None,
                new={"nested_key": "nested_value", "nested_number": 123},
            ),
        ]

        event_data = FieldsChangedEvent(
            type=CaseEventType.FIELDS_CHANGED,
            changes=complex_changes,
        )

        created_event = await case_events_service.create_event(test_case, event_data)

        retrieved_changes = created_event.data["changes"]
        assert len(retrieved_changes) == 5
        assert retrieved_changes[0]["new"] == "test_value"
        assert retrieved_changes[1]["new"] == 42
        assert retrieved_changes[2]["new"] is True
        assert retrieved_changes[3]["new"] == ["item1", "item2", "item3"]
        assert retrieved_changes[4]["new"]["nested_key"] == "nested_value"
        assert retrieved_changes[4]["new"]["nested_number"] == 123

    async def test_list_events_for_different_cases(
        self,
        case_events_service: CaseEventsService,
        cases_service: CasesService,
        test_case,
    ) -> None:
        """Test that listing events only returns events for the specified case."""
        case_params2 = CaseCreate(
            summary="Second Test Case",
            description="Another test case",
            status=CaseStatus.NEW,
            priority=CasePriority.HIGH,
            severity=CaseSeverity.MEDIUM,
        )
        test_case2 = await cases_service.create_case(case_params2)

        event1_data = CreatedEvent(type=CaseEventType.CASE_CREATED)
        await case_events_service.create_event(test_case, event1_data)

        event2_data = CreatedEvent(type=CaseEventType.CASE_CREATED)
        await case_events_service.create_event(test_case2, event2_data)

        events1 = await case_events_service.list_events(test_case)
        # Should have 2 events for test_case: automatic creation + manually created
        assert len(events1) == 2
        # Verify events belong to the correct case
        assert all(event.case_id == test_case.id for event in events1)

        events2 = await case_events_service.list_events(test_case2)
        # Should have 2 events for test_case2: automatic creation + manually created
        assert len(events2) == 2
        # Verify events belong to the correct case
        assert all(event.case_id == test_case2.id for event in events2)

        assert events1[0].case_id != events2[0].case_id
