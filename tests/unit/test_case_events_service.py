import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES, SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.durations import CaseDurationAnchorSelection
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.event_schemas import (
    AssigneeChangedEvent,
    CaseEventVariant,
    CommentCreatedEvent,
    CreatedEvent,
    FieldDiff,
    FieldsChangedEvent,
    PriorityChangedEvent,
    StatusChangedEvent,
)
from tracecat.cases.schemas import (
    CaseCreate,
)
from tracecat.cases.service import CaseEventsService, CasesService
from tracecat.db.models import CaseDurationDefinition, CaseEvent
from tracecat.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")

OLD_ASSIGNEE_ID = uuid.uuid4()
NEW_ASSIGNEE_ID = uuid.uuid4()
COMMENT_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def stub_case_duration_sync() -> Iterator[None]:
    with (
        patch(
            "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
            return_value=None,
        ),
        patch(
            "tracecat.cases.events.publish_case_event_payload",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


@pytest.mark.anyio
async def test_events_service_initialization_requires_workspace(
    session: AsyncSession,
) -> None:
    """Test that events service initialization requires a workspace ID."""
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
    async def test_create_event_queues_duration_sync_by_default(
        self,
        case_events_service: CaseEventsService,
        test_case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Case events should enqueue duration sync outside the request transaction."""
        enqueue_calls: list[dict[str, object]] = []

        def fake_enqueue(*args, **kwargs) -> None:
            enqueue_calls.append(kwargs)

        monkeypatch.setattr(
            "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
            fake_enqueue,
        )
        event_data = StatusChangedEvent(
            type=CaseEventType.STATUS_CHANGED,
            old=CaseStatus.NEW,
            new=CaseStatus.IN_PROGRESS,
        )
        await case_events_service.create_event(test_case, event_data)

        assert len(enqueue_calls) == 1
        assert enqueue_calls[0]["workspace_id"] == test_case.workspace_id
        assert enqueue_calls[0]["case_id"] == test_case.id
        assert enqueue_calls[0]["event_type"] == CaseEventType.STATUS_CHANGED.value
        assert enqueue_calls[0]["reason"] == "case_event"
        assert callable(enqueue_calls[0]["inline_fallback"])

    @pytest.mark.parametrize(
        ("event_data", "expected_type", "expected_data", "exact"),
        [
            pytest.param(
                CreatedEvent(type=CaseEventType.CASE_CREATED),
                CaseEventType.CASE_CREATED,
                {},
                False,
                id="case-created",
            ),
            pytest.param(
                StatusChangedEvent(
                    type=CaseEventType.STATUS_CHANGED,
                    old=CaseStatus.NEW,
                    new=CaseStatus.IN_PROGRESS,
                ),
                CaseEventType.STATUS_CHANGED,
                {"old": "new", "new": "in_progress"},
                False,
                id="enum-values",
            ),
            pytest.param(
                AssigneeChangedEvent(
                    type=CaseEventType.ASSIGNEE_CHANGED,
                    old=OLD_ASSIGNEE_ID,
                    new=NEW_ASSIGNEE_ID,
                ),
                CaseEventType.ASSIGNEE_CHANGED,
                {"old": str(OLD_ASSIGNEE_ID), "new": str(NEW_ASSIGNEE_ID)},
                False,
                id="uuid-to-string",
            ),
            pytest.param(
                AssigneeChangedEvent(
                    type=CaseEventType.ASSIGNEE_CHANGED,
                    old=OLD_ASSIGNEE_ID,
                    new=None,
                ),
                CaseEventType.ASSIGNEE_CHANGED,
                {"old": str(OLD_ASSIGNEE_ID), "new": None},
                False,
                id="none-value",
            ),
            pytest.param(
                CommentCreatedEvent(
                    comment_id=COMMENT_ID,
                    parent_id=None,
                    thread_root_id=COMMENT_ID,
                ),
                CaseEventType.COMMENT_CREATED,
                {
                    "comment_id": str(COMMENT_ID),
                    "parent_id": None,
                    "thread_root_id": str(COMMENT_ID),
                    "wf_exec_id": None,
                },
                True,
                id="nested-comment-ids",
            ),
            pytest.param(
                FieldsChangedEvent(
                    type=CaseEventType.FIELDS_CHANGED,
                    changes=[
                        FieldDiff(field="string_field", old=None, new="test_value"),
                        FieldDiff(field="numeric_field", old=None, new=42),
                        FieldDiff(field="boolean_field", old=None, new=True),
                        FieldDiff(field="list_field", old=None, new=["item1", "item2"]),
                        FieldDiff(
                            field="nested_dict",
                            old=None,
                            new={"nested_key": "nested_value", "nested_number": 123},
                        ),
                    ],
                ),
                CaseEventType.FIELDS_CHANGED,
                {
                    "changes": [
                        {"field": "string_field", "old": None, "new": "test_value"},
                        {"field": "numeric_field", "old": None, "new": 42},
                        {"field": "boolean_field", "old": None, "new": True},
                        {"field": "list_field", "old": None, "new": ["item1", "item2"]},
                        {
                            "field": "nested_dict",
                            "old": None,
                            "new": {"nested_key": "nested_value", "nested_number": 123},
                        },
                    ]
                },
                False,
                id="fields-changed-json-values",
            ),
        ],
    )
    async def test_create_event_serializes_data(
        self,
        case_events_service: CaseEventsService,
        test_case,
        event_data: CaseEventVariant,
        expected_type: CaseEventType,
        expected_data: dict[str, Any],
        exact: bool,
    ) -> None:
        created_event = await case_events_service.create_event(test_case, event_data)

        assert created_event.case_id == test_case.id
        assert created_event.type == expected_type
        assert created_event.user_id == case_events_service.role.user_id
        assert created_event.workspace_id == case_events_service.workspace_id
        assert created_event.data is not None
        if exact:
            assert created_event.data == expected_data
        else:
            assert {key: created_event.data[key] for key in expected_data} == (
                expected_data
            )

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
        assert events[2].type == CaseEventType.CASE_CREATED

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
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=ADMIN_SCOPES,
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

    async def test_case_viewed_event_created_once_per_window(
        self,
        case_events_service: CaseEventsService,
        test_case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Case viewed events should be created at most once within the dedupe window."""
        enqueue_calls: list[dict[str, object]] = []

        def fake_enqueue(*args, **kwargs) -> None:
            enqueue_calls.append(kwargs)

        monkeypatch.setattr(
            "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
            fake_enqueue,
        )

        first_event = await case_events_service.create_case_viewed_event(test_case)
        assert first_event is not None
        assert first_event.type == CaseEventType.CASE_VIEWED
        assert first_event.user_id == case_events_service.role.user_id
        # Recorded views enqueue unconditionally; the consumer's event-type
        # filter decides whether any definition cares.
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0]["event_type"] == CaseEventType.CASE_VIEWED.value
        await case_events_service.session.commit()

        duplicate_event = await case_events_service.create_case_viewed_event(
            test_case, dedupe_window=timedelta(minutes=5)
        )
        assert duplicate_event is None
        assert len(enqueue_calls) == 1

    async def test_case_viewed_event_syncs_duration_definitions(
        self,
        case_events_service: CaseEventsService,
        test_case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """case_viewed-anchored definitions get async materialization on view,
        keeping the read path free of synchronous duration recomputes."""
        enqueue_calls: list[dict[str, object]] = []

        def fake_enqueue(*args, **kwargs) -> None:
            enqueue_calls.append(kwargs)

        assert case_events_service.role.workspace_id is not None
        case_events_service.session.add(
            CaseDurationDefinition(
                workspace_id=case_events_service.role.workspace_id,
                name="Viewed duration",
                start_event_type=CaseEventType.CASE_VIEWED,
                start_timestamp_path="created_at",
                start_field_filters={},
                start_selection=CaseDurationAnchorSelection.FIRST,
                end_event_type=CaseEventType.CASE_CLOSED,
                end_timestamp_path="created_at",
                end_field_filters={},
                end_selection=CaseDurationAnchorSelection.FIRST,
            )
        )
        await case_events_service.session.flush()
        monkeypatch.setattr(
            "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
            fake_enqueue,
        )

        event = await case_events_service.create_case_viewed_event(test_case)

        assert event is not None
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0] | {"inline_fallback": None} == {
            "workspace_id": test_case.workspace_id,
            "case_id": test_case.id,
            "event_type": CaseEventType.CASE_VIEWED.value,
            "reason": "case_event",
            "inline_fallback": None,
        }
        assert callable(enqueue_calls[0]["inline_fallback"])

    async def test_case_viewed_event_created_after_window_elapsed(
        self,
        case_events_service: CaseEventsService,
        test_case,
    ) -> None:
        """A new case viewed event should be created once the dedupe window expires."""
        first_event = await case_events_service.create_case_viewed_event(test_case)
        assert first_event is not None
        await case_events_service.session.commit()

        # Move the first event outside the dedupe window.
        await case_events_service.session.execute(
            sa.update(CaseEvent)
            .where(CaseEvent.id == first_event.id)
            .values(created_at=datetime.now(UTC) - timedelta(minutes=10))
        )
        await case_events_service.session.commit()

        new_event = await case_events_service.create_case_viewed_event(
            test_case, dedupe_window=timedelta(minutes=5)
        )
        assert new_event is not None
        assert new_event.type == CaseEventType.CASE_VIEWED
        await case_events_service.session.commit()
