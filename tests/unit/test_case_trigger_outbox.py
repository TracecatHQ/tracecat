"""Case trigger outbox: rows commit with the case, runs start once, rows go last."""

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.event_schemas import (
    ClosedEvent,
    CommentCreatedEvent,
    CreatedEvent,
    UpdatedEvent,
)
from tracecat.cases.events import CaseEventsService
from tracecat.cases.schemas import CaseCommentWorkflowStatus, CaseCreate
from tracecat.cases.service import CasesService
from tracecat.cases.triggers.dispatch import (
    CaseTriggerOutboxDispatcher,
    case_trigger_exec_id,
    list_outbox_row_ids,
)
from tracecat.cases.triggers.types import CaseCommentWorkflowTrigger
from tracecat.db.models import (
    Case,
    CaseComment,
    CaseTrigger,
    CaseTriggerOutbox,
    Workflow,
)
from tracecat.identifiers.workflow import WorkflowUUID

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def stub_case_duration_sync() -> Iterator[None]:
    with patch(
        "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
        return_value=None,
    ):
        yield


@pytest.fixture
def bypass_session(session: AsyncSession) -> Iterator[None]:
    """Route the dispatcher's privileged sessions to the test transaction."""

    @contextlib.asynccontextmanager
    async def _session() -> AsyncIterator[AsyncSession]:
        # Like the real context manager, roll back when the block raises.
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

    with patch(
        "tracecat.cases.triggers.dispatch.get_async_session_bypass_rls_context_manager",
        _session,
    ):
        yield


@pytest.fixture
async def test_case(session: AsyncSession, svc_role: Role) -> Case:
    return await CasesService(session=session, role=svc_role).create_case(
        CaseCreate(
            summary="Outbox test case",
            description="Case trigger outbox test",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )


async def _add_trigger(
    session: AsyncSession,
    role: Role,
    *,
    event_types: list[str],
    status: str = "online",
    tag_filters: list[str] | None = None,
) -> CaseTrigger:
    assert role.workspace_id is not None
    workflow = Workflow(
        title=f"Outbox trigger {uuid.uuid4().hex[:8]}",
        description="Test workflow",
        status="offline",
        workspace_id=role.workspace_id,
    )
    session.add(workflow)
    await session.flush()
    trigger = CaseTrigger(
        workspace_id=role.workspace_id,
        workflow_id=workflow.id,
        status=status,
        event_types=event_types,
        tag_filters=tag_filters or [],
    )
    session.add(trigger)
    await session.commit()
    return trigger


async def _outbox_rows(
    session: AsyncSession, case: Case | uuid.UUID
) -> list[CaseTriggerOutbox]:
    case_id = case if isinstance(case, uuid.UUID) else case.id
    result = await session.scalars(
        select(CaseTriggerOutbox)
        .where(CaseTriggerOutbox.case_id == case_id)
        .order_by(CaseTriggerOutbox.surrogate_id)
    )
    return list(result.all())


@pytest.mark.anyio
class TestOutboxWrite:
    async def test_no_row_without_online_trigger(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """Workspaces without a matching online trigger write no rows."""
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_trigger(
            session, svc_role, event_types=["case_created"], status="offline"
        )
        events = CaseEventsService(session=session, role=svc_role)

        await events.create_event(test_case, CreatedEvent())
        await session.commit()

        assert await _outbox_rows(session, test_case) == []

    async def test_row_for_matching_online_trigger(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A matching online trigger adds one row in the event's transaction."""
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)

        event = await events.create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        rows = await _outbox_rows(session, test_case)
        assert [row.event_id for row in rows] == [event.id]
        assert rows[0].extras is None

        case_id = test_case.id
        await session.rollback()
        assert await _outbox_rows(session, case_id) == []

    async def test_case_closed_matches_status_changed_trigger(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """Close and reopen events also fire `status_changed` subscriptions."""
        await _add_trigger(session, svc_role, event_types=["status_changed"])
        events = CaseEventsService(session=session, role=svc_role)

        event = await events.create_event(
            test_case, ClosedEvent(old=CaseStatus.NEW, new=CaseStatus.CLOSED)
        )
        await session.commit()

        assert [row.event_id for row in await _outbox_rows(session, test_case)] == [
            event.id
        ]

    async def test_workflow_originated_event_writes_no_row(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """Events made by a workflow run never trigger, so they need no row."""
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)

        await events.create_event(
            test_case,
            UpdatedEvent(field="summary", old="a", new="b", wf_exec_id="wf_1/exec_1"),
        )
        await session.commit()

        assert await _outbox_rows(session, test_case) == []

    async def test_publish_case_trigger_false_writes_no_row(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)

        await events.create_event(
            test_case,
            UpdatedEvent(field="summary", old="a", new="b"),
            publish_case_trigger=False,
        )
        await session.commit()

        assert await _outbox_rows(session, test_case) == []


def _definition() -> SimpleNamespace:
    return SimpleNamespace(
        workflow=SimpleNamespace(version=1),
        version=1,
        content={"stub": True},
        registry_lock=None,
    )


@contextlib.contextmanager
def _dispatch_mocks(start: AsyncMock) -> Iterator[MagicMock]:
    service = MagicMock()
    service.create_workflow_execution_wait_for_start = start
    with (
        patch(
            "tracecat.cases.triggers.dispatch.is_org_entitled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "tracecat.cases.triggers.dispatch.WorkflowDefinitionsService.get_definition_by_workflow_id",
            new=AsyncMock(return_value=_definition()),
        ),
        patch(
            "tracecat.cases.triggers.dispatch.DSLInput.model_validate",
            return_value=MagicMock(),
        ),
        patch(
            "tracecat.cases.triggers.dispatch.WorkflowExecutionsService.connect",
            new=AsyncMock(return_value=service),
        ),
    ):
        yield service


@pytest.mark.anyio
@pytest.mark.usefixtures("bypass_session")
class TestOutboxDispatch:
    async def test_row_deleted_after_start_and_repeat_is_skipped(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A repeated start for the same (event, workflow) is rejected, not rerun.

        The run ID is fixed per (event, workflow), so a retried row maps to the
        same run and Temporal's REJECT_DUPLICATE stops a second run.
        """
        trigger = await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)
        event = await events.create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        await session.commit()
        expected_exec_id = case_trigger_exec_id(event.id, trigger.workflow_id)

        start = AsyncMock()
        with _dispatch_mocks(start):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        assert start.await_count == 1
        assert start.await_args is not None
        assert start.await_args.kwargs["wf_exec_id"] == expected_exec_id
        assert await _outbox_rows(session, test_case) == []

        # A crash before the delete repeats the row; the duplicate start is
        # rejected and the row is still deleted.
        session.add(
            CaseTriggerOutbox(
                workspace_id=test_case.workspace_id,
                case_id=test_case.id,
                event_id=event.id,
            )
        )
        await session.commit()
        duplicate = AsyncMock(
            side_effect=WorkflowAlreadyStartedError(expected_exec_id, "DSLWorkflow")
        )
        with _dispatch_mocks(duplicate):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        assert duplicate.await_args is not None
        assert duplicate.await_args.kwargs["wf_exec_id"] == expected_exec_id
        assert await _outbox_rows(session, test_case) == []

    async def test_failed_start_keeps_row(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """Rows are deleted only after every start for them was accepted."""
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)
        await events.create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        await session.commit()

        case_id = test_case.id
        start = AsyncMock(side_effect=[RuntimeError("temporal unavailable"), None])
        with _dispatch_mocks(start), pytest.raises(RuntimeError):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        # The other trigger was still attempted, and the row stays for a retry.
        assert start.await_count == 2
        assert len(await _outbox_rows(session, case_id)) == 1

    async def test_failed_row_moves_behind_newer_rows(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """Rows that keep failing cannot fill every batch and starve newer rows."""
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)
        for summary in ("a", "b"):
            await events.create_event(
                test_case, UpdatedEvent(field="summary", old=summary, new="z")
            )
            await session.commit()
        first, second = await list_outbox_row_ids()

        start = AsyncMock(side_effect=RuntimeError("temporal unavailable"))
        with _dispatch_mocks(start), pytest.raises(RuntimeError):
            await CaseTriggerOutboxDispatcher().dispatch_row(first)

        assert await list_outbox_row_ids() == [second, first]
        assert await list_outbox_row_ids(limit=1) == [second]

    async def test_permanent_start_error_is_dropped(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A start that fails on its input every time is dropped, not retried."""
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)
        await events.create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        await session.commit()

        start = AsyncMock(side_effect=[OverflowError("timeout too large"), None])
        with _dispatch_mocks(start):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        assert start.await_count == 2
        assert await _outbox_rows(session, test_case) == []

    async def test_tag_filters_and_offline_triggers_are_respected(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        matching = await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_trigger(
            session,
            svc_role,
            event_types=["case_updated"],
            tag_filters=["needs-tag"],
        )
        await _add_trigger(
            session, svc_role, event_types=["case_updated"], status="offline"
        )
        events = CaseEventsService(session=session, role=svc_role)
        await events.create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        await session.commit()

        start = AsyncMock()
        with _dispatch_mocks(start):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        assert [call.kwargs["wf_id"] for call in start.await_args_list] == [
            WorkflowUUID.new(matching.workflow_id)
        ]

    async def test_comment_workflow_uses_preassigned_exec_id(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """The picked workflow starts once with its ID; a same-workflow trigger is skipped."""
        trigger = await _add_trigger(session, svc_role, event_types=["comment_created"])
        comment = CaseComment(
            workspace_id=test_case.workspace_id,
            case_id=test_case.id,
            content="Run this",
            workflow_id=trigger.workflow_id,
            workflow_wf_exec_id="wf_abc/exec_def",
            workflow_status=CaseCommentWorkflowStatus.RUNNING.value,
        )
        session.add(comment)
        await session.flush()
        comment_workflow = CaseCommentWorkflowTrigger(
            workflow_id=trigger.workflow_id,
            wf_exec_id="wf_abc/exec_def",
            comment_id=comment.id,
            comment="Run this",
            triggered_by_type="service",
        )
        events = CaseEventsService(session=session, role=svc_role)
        await events.create_event(
            test_case,
            CommentCreatedEvent(comment_id=comment.id, thread_root_id=comment.id),
            comment_workflow=comment_workflow,
        )
        await session.commit()

        start = AsyncMock()
        with _dispatch_mocks(start):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        assert start.await_count == 1
        assert start.await_args is not None
        assert start.await_args.kwargs["wf_exec_id"] == "wf_abc/exec_def"
        assert start.await_args.kwargs["payload"]["comment"] == "Run this"
        assert await _outbox_rows(session, test_case) == []

    async def test_disabled_case_triggers_write_no_rows(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The kill switch stops picked comment workflows and configured triggers alike."""
        picked = await _add_trigger(
            session, svc_role, event_types=["comment_created"], status="offline"
        )
        await _add_trigger(
            session, svc_role, event_types=["comment_created", "case_updated"]
        )
        comment = CaseComment(
            workspace_id=test_case.workspace_id,
            case_id=test_case.id,
            content="Run this",
            workflow_id=picked.workflow_id,
            workflow_wf_exec_id="wf_abc/exec_def",
            workflow_status=CaseCommentWorkflowStatus.RUNNING.value,
        )
        session.add(comment)
        await session.flush()
        monkeypatch.setattr(
            "tracecat.cases.triggers.outbox.config.TRACECAT__CASE_TRIGGERS_ENABLED",
            False,
        )
        events = CaseEventsService(session=session, role=svc_role)
        await events.create_event(
            test_case,
            CommentCreatedEvent(comment_id=comment.id, thread_root_id=comment.id),
            comment_workflow=CaseCommentWorkflowTrigger(
                workflow_id=picked.workflow_id,
                wf_exec_id="wf_abc/exec_def",
                comment_id=comment.id,
                comment="Run this",
                triggered_by_type="service",
            ),
        )
        await events.create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        await session.commit()

        assert await _outbox_rows(session, test_case) == []

    async def test_missing_comment_workflow_definition_marks_comment_failed(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A permanent problem is recorded on the comment and the row is dropped."""
        trigger = await _add_trigger(session, svc_role, event_types=["case_closed"])
        comment = CaseComment(
            workspace_id=test_case.workspace_id,
            case_id=test_case.id,
            content="Run this",
            workflow_id=trigger.workflow_id,
            workflow_wf_exec_id="wf_abc/exec_def",
            workflow_status=CaseCommentWorkflowStatus.RUNNING.value,
        )
        session.add(comment)
        await session.flush()
        events = CaseEventsService(session=session, role=svc_role)
        await events.create_event(
            test_case,
            CommentCreatedEvent(comment_id=comment.id, thread_root_id=comment.id),
            comment_workflow=CaseCommentWorkflowTrigger(
                workflow_id=trigger.workflow_id,
                wf_exec_id="wf_abc/exec_def",
                comment_id=comment.id,
                comment="Run this",
                triggered_by_type="service",
            ),
        )
        await session.commit()

        start = AsyncMock()
        with (
            _dispatch_mocks(start),
            patch(
                "tracecat.cases.triggers.dispatch.WorkflowDefinitionsService.get_definition_by_workflow_id",
                new=AsyncMock(return_value=None),
            ),
        ):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        start.assert_not_awaited()
        await session.refresh(comment)
        assert comment.workflow_status == CaseCommentWorkflowStatus.FAILED.value
        assert await _outbox_rows(session, test_case) == []

    async def test_invalid_trigger_definition_disables_trigger(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        trigger = await _add_trigger(session, svc_role, event_types=["case_updated"])
        events = CaseEventsService(session=session, role=svc_role)
        await events.create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        await session.commit()

        start = AsyncMock()
        with (
            _dispatch_mocks(start),
            patch(
                "tracecat.cases.triggers.dispatch.WorkflowDefinitionsService.get_definition_by_workflow_id",
                new=AsyncMock(return_value=None),
            ),
        ):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        start.assert_not_awaited()
        await session.refresh(trigger)
        assert trigger.status == "offline"
        assert await _outbox_rows(session, test_case) == []


def test_case_trigger_exec_id_is_stable_per_event_and_workflow() -> None:
    event_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    exec_id = case_trigger_exec_id(event_id, workflow_id)
    assert exec_id == case_trigger_exec_id(event_id, workflow_id)
    assert exec_id != case_trigger_exec_id(event_id, uuid.uuid4())
    assert exec_id != case_trigger_exec_id(uuid.uuid4(), workflow_id)
    assert exec_id.startswith("wf_")
    assert "/exec_" in exec_id
