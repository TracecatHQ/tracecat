import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
)
from tracecat.cases.schemas import CaseCreate, CaseTaskCreate, CaseTaskUpdate
from tracecat.cases.service import CaseEventsService, CasesService, CaseTasksService
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def tasks_service(session: AsyncSession, svc_role: Role) -> CaseTasksService:
    return CaseTasksService(session=session, role=svc_role)


@pytest.fixture
async def events_service(session: AsyncSession, svc_role: Role) -> CaseEventsService:
    return CaseEventsService(session=session, role=svc_role)


@pytest.fixture
async def test_case(cases_service: CasesService):
    params = CaseCreate(
        summary="Case for task events",
        description="desc",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    return await cases_service.create_case(params)


@pytest.mark.anyio
class TestCaseTaskEvents:
    async def test_task_created_event(
        self,
        test_case,
        tasks_service: CaseTasksService,
        events_service: CaseEventsService,
    ) -> None:
        # Initially, only the case_created event exists
        events = await events_service.list_events(test_case)
        assert len(events) == 1
        assert events[0].type == CaseEventType.CASE_CREATED

        # Create a task
        task = await tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(title="T1", description="d1"),
        )

        events = await events_service.list_events(test_case)
        # Now should include task_created
        assert any(e.type == CaseEventType.TASK_CREATED for e in events)
        latest = events[0]
        assert latest.type == CaseEventType.TASK_CREATED
        assert latest.data["task_id"] == str(task.id)
        assert latest.data["title"] == "T1"

    async def test_task_status_events(
        self,
        test_case,
        tasks_service: CaseTasksService,
        events_service: CaseEventsService,
    ) -> None:
        task = await tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(title="T2"),
        )

        # Status change
        await tasks_service.update_task(
            task.id,
            CaseTaskUpdate(status=CaseTaskStatus.IN_PROGRESS),
        )
        events = await events_service.list_events(test_case)
        assert any(e.type == CaseEventType.TASK_STATUS_CHANGED for e in events)

        # Verify the status change event has correct data
        status_event = next(
            e for e in events if e.type == CaseEventType.TASK_STATUS_CHANGED
        )
        assert status_event.data["old"] == "todo"
        assert status_event.data["new"] == "in_progress"

    async def test_task_priority_and_workflow_events(
        self,
        test_case,
        tasks_service: CaseTasksService,
        events_service: CaseEventsService,
    ) -> None:
        task = await tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(title="T3", description="old", priority=CasePriority.LOW),
        )

        # Priority change
        await tasks_service.update_task(
            task.id,
            CaseTaskUpdate(priority=CasePriority.HIGH),
        )
        events = await events_service.list_events(test_case)
        assert any(e.type == CaseEventType.TASK_PRIORITY_CHANGED for e in events)
        priority_event = next(
            e for e in events if e.type == CaseEventType.TASK_PRIORITY_CHANGED
        )
        assert priority_event.data["old"] == "low"
        assert priority_event.data["new"] == "high"

        # Title/description changes should not generate events
        await tasks_service.update_task(
            task.id,
            CaseTaskUpdate(title="T3-new", description="new"),
        )
        events = await events_service.list_events(test_case)
        # Should still have the same number of events (no new ones for title/description)
        task_events = [e for e in events if e.type.value.startswith("task_")]
        # Should have: created, priority_changed (2 total)
        assert len(task_events) == 2

    async def test_task_deleted_event(
        self,
        test_case,
        tasks_service: CaseTasksService,
        events_service: CaseEventsService,
    ) -> None:
        task = await tasks_service.create_task(
            test_case.id,
            CaseTaskCreate(title="T4"),
        )
        await tasks_service.delete_task(task.id)
        events = await events_service.list_events(test_case)
        # Latest should be task_deleted
        assert events[0].type == CaseEventType.TASK_DELETED
        assert events[0].data["task_id"] == str(task.id)
        assert events[0].data["title"] == "T4"
