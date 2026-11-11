"""Case trigger outbox: rows commit with the case, runs start once, rows go last."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.testing import ActivityEnvironment

from tracecat.auth.types import Role
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.event_schemas import (
    ClosedEvent,
    CommentCreatedEvent,
    CreatedEvent,
    TagAddedEvent,
    UpdatedEvent,
)
from tracecat.cases.events import CaseEventsService
from tracecat.cases.schemas import CaseCommentWorkflowStatus, CaseCreate
from tracecat.cases.service import CasesService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.cases.triggers.dispatch import (
    OUTBOX_RETRY_DELAY,
    CaseTriggerOutboxDispatcher,
    CaseTriggerRetryNotRecordedError,
    CaseTriggerStartUnknownError,
    case_trigger_exec_id,
    list_outbox_row_ids,
    outbox_has_rows,
)
from tracecat.cases.triggers.dispatcher import (
    dispatch_case_trigger_outbox_batch,
    wait_for_case_trigger_outbox,
)
from tracecat.cases.triggers.types import CaseCommentWorkflowTrigger
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    Case,
    CaseComment,
    CaseEvent,
    CaseTag,
    CaseTrigger,
    CaseTriggerOutbox,
    Workflow,
    Workspace,
)
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.tags.schemas import TagCreate
from tracecat.workflow.executions.service import WorkflowExecutionsService

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
def _dispatch_mocks(
    start: AsyncMock, *, get_definition: AsyncMock | None = None
) -> Iterator[MagicMock]:
    service = MagicMock()
    service.create_workflow_execution_wait_for_start = start
    service.settle_lost_start = AsyncMock(return_value="not_started")
    with (
        patch(
            "tracecat.cases.triggers.dispatch.is_org_entitled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "tracecat.cases.triggers.dispatch.WorkflowDefinitionsService.get_definition_by_workflow_id",
            new=get_definition or AsyncMock(return_value=_definition()),
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


@dataclass(frozen=True, slots=True)
class PickedComment:
    """IDs of a comment that picked a workflow, safe to use after a rollback."""

    case_id: uuid.UUID
    comment_id: uuid.UUID
    workflow_id: uuid.UUID
    exec_id: str


async def _add_picked_comment(
    session: AsyncSession, role: Role, case: Case
) -> PickedComment:
    """Post a comment that picks a workflow, writing its outbox row."""
    picked = await _add_trigger(
        session, role, event_types=["comment_created"], status="offline"
    )
    exec_id = generate_exec_id(WorkflowUUID.new(picked.workflow_id))
    comment = CaseComment(
        workspace_id=case.workspace_id,
        case_id=case.id,
        content="Run this",
        workflow_id=picked.workflow_id,
        workflow_wf_exec_id=exec_id,
        workflow_status=CaseCommentWorkflowStatus.RUNNING.value,
    )
    session.add(comment)
    await session.flush()
    await CaseEventsService(session=session, role=role).create_event(
        case,
        CommentCreatedEvent(comment_id=comment.id, thread_root_id=comment.id),
        comment_workflow=CaseCommentWorkflowTrigger(
            workflow_id=picked.workflow_id,
            wf_exec_id=exec_id,
            comment_id=comment.id,
            comment="Run this",
            triggered_by_type="service",
        ),
    )
    await session.commit()
    return PickedComment(
        case_id=case.id,
        comment_id=comment.id,
        workflow_id=picked.workflow_id,
        exec_id=exec_id,
    )


async def _add_update_event(session: AsyncSession, role: Role, case: Case) -> CaseEvent:
    event = await CaseEventsService(session=session, role=role).create_event(
        case, UpdatedEvent(field="summary", old="a", new="b")
    )
    await session.commit()
    return event


async def _dispatch_only_row() -> None:
    (row_id,) = await list_outbox_row_ids()
    await CaseTriggerOutboxDispatcher().dispatch_row(row_id)


async def _comment_status(session: AsyncSession, comment_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(CaseComment.workflow_status).where(CaseComment.id == comment_id)
    )


def _tagged_case_create(summary: str = "Tagged case") -> CaseCreate:
    return CaseCreate(
        summary=summary,
        description="Case trigger outbox test",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("bypass_session")
class TestCreateCaseWithTags:
    async def test_case_created_matches_the_tags_given_at_create(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        """A `case_created` trigger filtered on X fires for a case created with X.

        A trigger filtered only on a tag the case wasn't created with does not.
        """
        cases = CasesService(session=session, role=svc_role)
        tag = await cases.tags.create_tag(TagCreate(name="Created with"))
        matching = await _add_trigger(
            session, svc_role, event_types=["case_created"], tag_filters=[tag.ref]
        )
        await _add_trigger(
            session, svc_role, event_types=["case_created"], tag_filters=["other"]
        )

        case = await cases.create_case(_tagged_case_create(), tags=[tag.ref])

        assert [t.ref for t in case.tags] == [tag.ref]
        start = AsyncMock()
        with _dispatch_mocks(start):
            await _dispatch_only_row()
        assert [call.kwargs["wf_id"] for call in start.await_args_list] == [
            WorkflowUUID.new(matching.workflow_id)
        ]

    async def test_tags_commit_with_the_case_created_row(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        """No commit separates the `CASE_CREATED` outbox row from the case's tags.

        The dispatcher sees the row only once it commits, so it can never load
        the case between the case insert and the tag inserts.
        """
        cases = CasesService(session=session, role=svc_role)
        tag = await cases.tags.create_tag(TagCreate(name="Same commit"))
        await _add_trigger(
            session, svc_role, event_types=["case_created"], tag_filters=[tag.ref]
        )
        commits = 0
        commits_at_link: list[int] = []
        real_commit = session.commit
        real_link_tag = cases.tags.link_tag

        async def counting_commit() -> None:
            nonlocal commits
            commits += 1
            await real_commit()

        async def recording_link_tag(case: Case, tag: CaseTag) -> bool:
            commits_at_link.append(commits)
            # The row the dispatcher will pick up is already pending.
            assert len(await _outbox_rows(session, case)) == 1
            return await real_link_tag(case, tag)

        with (
            patch.object(session, "commit", counting_commit),
            patch.object(cases.tags, "link_tag", recording_link_tag),
        ):
            await cases.create_case(_tagged_case_create(), tags=[tag.ref])

        assert commits_at_link == [0]
        assert commits == 1

    async def test_unknown_tag_creates_no_case(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        """An unknown tag fails the whole create, so a retry can't duplicate it."""
        cases = CasesService(session=session, role=svc_role)
        known = await cases.tags.create_tag(TagCreate(name="Known"))
        await _add_trigger(session, svc_role, event_types=["case_created"])
        summary = f"Unknown tag {uuid.uuid4().hex[:8]}"

        with pytest.raises(NoResultFound):
            await cases.create_case(
                _tagged_case_create(summary), tags=[known.ref, "does-not-exist"]
            )

        assert (
            await session.scalar(
                select(func.count()).select_from(Case).where(Case.summary == summary)
            )
            == 0
        )
        assert await list_outbox_row_ids() == []


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
        event = await _add_update_event(session, svc_role, test_case)
        expected_exec_id = case_trigger_exec_id(event.id, trigger.workflow_id)

        start = AsyncMock()
        with _dispatch_mocks(start):
            await _dispatch_only_row()

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
            await _dispatch_only_row()

        assert duplicate.await_args is not None
        assert duplicate.await_args.kwargs["wf_exec_id"] == expected_exec_id
        assert await _outbox_rows(session, test_case) == []

    @pytest.mark.parametrize(
        ("error", "row_kept"),
        [
            # Rows are deleted only after every start for them was accepted.
            (RuntimeError("temporal unavailable"), True),
            # A start that fails on its input every time is dropped, not retried.
            (OverflowError("timeout too large"), False),
        ],
    )
    async def test_a_failing_start_does_not_hold_back_the_other_triggers(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        error: Exception,
        row_kept: bool,
    ) -> None:
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_update_event(session, svc_role, test_case)
        case_id = test_case.id

        start = AsyncMock(side_effect=[error, None])
        with _dispatch_mocks(start):
            if row_kept:
                with pytest.raises(type(error)):
                    await _dispatch_only_row()
            else:
                await _dispatch_only_row()

        assert start.await_count == 2
        assert len(await _outbox_rows(session, case_id)) == int(row_kept)

    async def test_failed_row_sits_out_batches_for_the_retry_delay(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A row that keeps failing cannot fail every batch.

        Even alone in its workspace, where it would take the first turn of
        every batch, a failed row sits out batches, and the wait for rows,
        until the retry delay has passed. The rest of the queue keeps
        dispatching meanwhile.
        """
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_update_event(session, svc_role, test_case)
        await _add_update_event(session, svc_role, test_case)
        case_id = test_case.id
        first, second = await list_outbox_row_ids()

        start = AsyncMock(side_effect=RuntimeError("temporal unavailable"))
        with _dispatch_mocks(start), pytest.raises(RuntimeError):
            await CaseTriggerOutboxDispatcher().dispatch_row(first)
        assert await list_outbox_row_ids() == [second]

        with _dispatch_mocks(start), pytest.raises(RuntimeError):
            await CaseTriggerOutboxDispatcher().dispatch_row(second)
        assert await list_outbox_row_ids() == []
        # The dispatcher waits instead of running empty batches.
        assert not await outbox_has_rows()

        # Both failures are now older than the retry delay, measured from the
        # same database clock the dispatcher reads.
        await session.execute(
            update(CaseTriggerOutbox)
            .where(CaseTriggerOutbox.case_id == case_id)
            .values(
                created_at=func.now() - timedelta(minutes=5),
                updated_at=func.now() - OUTBOX_RETRY_DELAY - timedelta(minutes=1),
            )
        )
        await session.commit()
        assert await outbox_has_rows()
        assert await list_outbox_row_ids() == [first, second]

    async def test_unrecorded_retry_delay_is_reported_apart(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A failed row whose retry delay can't be recorded is ready again at once.

        It raises its own error so the batch can fail and back off instead of
        retrying the row in a tight loop.
        """
        await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_update_event(session, svc_role, test_case)
        (row_id,) = await list_outbox_row_ids()

        start = AsyncMock(side_effect=RuntimeError("temporal unavailable"))
        with (
            _dispatch_mocks(start),
            patch(
                "tracecat.cases.triggers.dispatch._touch_outbox_row",
                new=AsyncMock(side_effect=RuntimeError("pool timeout")),
            ),
            pytest.raises(CaseTriggerRetryNotRecordedError),
        ):
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

    async def test_batches_take_each_workspace_in_turn(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A burst in one workspace cannot fill a batch ahead of other workspaces.

        Each workspace's least recently attempted row comes first, then each
        workspace's second, and so on.
        """
        assert svc_role.organization_id is not None
        quiet = Workspace(
            name="quiet-workspace", organization_id=svc_role.organization_id
        )
        session.add(quiet)
        event = await CaseEventsService(session=session, role=svc_role).create_event(
            test_case, UpdatedEvent(field="summary", old="a", new="b")
        )
        start = datetime.now(UTC) - timedelta(minutes=1)
        # The busy workspace's rows are all older than the quiet one's.
        rows = [
            CaseTriggerOutbox(
                workspace_id=workspace_id,
                case_id=test_case.id,
                event_id=event.id,
                created_at=start + timedelta(seconds=offset),
                updated_at=start + timedelta(seconds=offset),
            )
            for workspace_id, offset in [
                (test_case.workspace_id, 0),
                (test_case.workspace_id, 1),
                (test_case.workspace_id, 2),
                (quiet.id, 3),
                (quiet.id, 4),
            ]
        ]
        session.add_all(rows)
        await session.commit()
        busy_1, busy_2, busy_3, quiet_1, quiet_2 = (row.surrogate_id for row in rows)

        assert await list_outbox_row_ids() == [
            busy_1,
            quiet_1,
            busy_2,
            quiet_2,
            busy_3,
        ]
        assert await list_outbox_row_ids(limit=2) == [busy_1, quiet_1]

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
        await _add_update_event(session, svc_role, test_case)

        start = AsyncMock()
        with _dispatch_mocks(start):
            await _dispatch_only_row()

        assert [call.kwargs["wf_id"] for call in start.await_args_list] == [
            WorkflowUUID.new(matching.workflow_id)
        ]

    @pytest.mark.parametrize(
        ("event_tag_ref", "started"), [("filtered", True), ("unrelated", False)]
    )
    async def test_tag_events_match_the_event_tag(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        event_tag_ref: str,
        started: bool,
    ) -> None:
        """A tag event matches the tag it added, not the case's current tags."""
        await _add_trigger(
            session, svc_role, event_types=["tag_added"], tag_filters=["filtered"]
        )
        await CaseEventsService(session=session, role=svc_role).create_event(
            test_case,
            TagAddedEvent(
                tag_id=uuid.uuid4(), tag_ref=event_tag_ref, tag_name=event_tag_ref
            ),
        )
        await session.commit()

        start = AsyncMock()
        with _dispatch_mocks(start):
            await _dispatch_only_row()

        assert start.await_count == int(started)

    async def test_invalid_trigger_definition_disables_trigger(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        trigger = await _add_trigger(session, svc_role, event_types=["case_updated"])
        await _add_update_event(session, svc_role, test_case)

        start = AsyncMock()
        with _dispatch_mocks(start, get_definition=AsyncMock(return_value=None)):
            await _dispatch_only_row()

        start.assert_not_awaited()
        await session.refresh(trigger)
        assert trigger.status == "offline"
        assert await _outbox_rows(session, test_case) == []


@pytest.mark.anyio
@pytest.mark.usefixtures("bypass_session")
class TestCommentWorkflowDispatch:
    async def test_comment_workflow_uses_preassigned_exec_id(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """The picked workflow starts once with its ID; a same-workflow trigger is skipped."""
        picked = await _add_picked_comment(session, svc_role, test_case)
        await session.execute(
            update(CaseTrigger)
            .where(CaseTrigger.workflow_id == picked.workflow_id)
            .values(status="online")
        )
        await session.commit()

        start = AsyncMock()
        with _dispatch_mocks(start):
            await _dispatch_only_row()

        assert start.await_count == 1
        assert start.await_args is not None
        assert start.await_args.kwargs["wf_exec_id"] == picked.exec_id
        assert start.await_args.kwargs["payload"]["comment"] == "Run this"
        assert await _outbox_rows(session, picked.case_id) == []

    async def test_settled_comment_workflow_is_not_handled_again_on_retry(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """A sibling's retry never re-handles a picked workflow that already started.

        Re-handling it could find its definition gone and mark a running
        workflow failed.
        """
        sibling = await _add_trigger(session, svc_role, event_types=["comment_created"])
        sibling_id = sibling.workflow_id
        picked = await _add_picked_comment(session, svc_role, test_case)

        # The picked workflow starts; the sibling fails and keeps the row.
        (row_id,) = await list_outbox_row_ids()
        first = AsyncMock(side_effect=[None, RuntimeError("temporal unavailable")])
        with _dispatch_mocks(first), pytest.raises(RuntimeError):
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)
        assert [c.kwargs["wf_exec_id"] for c in first.await_args_list] == [
            picked.exec_id,
            case_trigger_exec_id(
                (await _outbox_rows(session, picked.case_id))[0].event_id,
                sibling_id,
            ),
        ]

        # The picked workflow's definition is gone by the retry.
        retry = AsyncMock()
        get_definition = AsyncMock(
            side_effect=lambda wf_id, **_: (
                None if wf_id == WorkflowUUID.new(picked.workflow_id) else _definition()
            )
        )
        with _dispatch_mocks(retry, get_definition=get_definition) as service:
            await CaseTriggerOutboxDispatcher().dispatch_row(row_id)

        service.settle_lost_start.assert_not_awaited()
        assert [c.kwargs["wf_id"] for c in retry.await_args_list] == [
            WorkflowUUID.new(sibling_id)
        ]
        assert await _comment_status(session, picked.comment_id) == (
            CaseCommentWorkflowStatus.RUNNING.value
        )
        assert await _outbox_rows(session, picked.case_id) == []

    async def test_unsettled_comment_workflow_keeps_the_row(
        self, session: AsyncSession, svc_role: Role, test_case: Case
    ) -> None:
        """An overlapping attempt cannot delete a row whose picked workflow is pending."""
        picked = await _add_picked_comment(session, svc_role, test_case)

        # The attempt holding the comment step skipped it here, so only the
        # configured-trigger step runs, as for an overlapping attempt.
        with _dispatch_mocks(AsyncMock()):
            (row_id,) = await list_outbox_row_ids()
            await CaseTriggerOutboxDispatcher()._dispatch_configured_triggers(
                row_id, delete_row=True
            )

        assert len(await _outbox_rows(session, picked.case_id)) == 1

    @pytest.mark.parametrize(
        ("error", "outcome", "status"),
        [
            # A pre-start error on a retry keeps a run an earlier attempt started.
            (OverflowError("timeout too large"), "started", "running"),
            # A rejected duplicate start may be an earlier attempt's tombstone.
            (
                WorkflowAlreadyStartedError("wf_x/exec_y", "DSLWorkflow"),
                "started",
                "running",
            ),
            (
                WorkflowAlreadyStartedError("wf_x/exec_y", "DSLWorkflow"),
                "not_started",
                "failed",
            ),
        ],
    )
    async def test_an_unaccepted_start_is_settled_before_failing_the_comment(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        error: Exception,
        outcome: str,
        status: str,
    ) -> None:
        picked = await _add_picked_comment(session, svc_role, test_case)

        with _dispatch_mocks(AsyncMock(side_effect=error)) as service:
            service.settle_lost_start = AsyncMock(return_value=outcome)
            await _dispatch_only_row()

        service.settle_lost_start.assert_awaited_once_with(picked.exec_id)
        assert await _comment_status(session, picked.comment_id) == status
        assert await _outbox_rows(session, picked.case_id) == []

    @pytest.mark.parametrize(
        ("outcome", "status", "row_kept"),
        [
            ("not_started", CaseCommentWorkflowStatus.FAILED, False),
            # An earlier attempt started the run before the definition went away.
            ("started", CaseCommentWorkflowStatus.RUNNING, False),
            ("unknown", CaseCommentWorkflowStatus.RUNNING, True),
        ],
    )
    async def test_missing_comment_workflow_definition_settles_the_run(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        outcome: str,
        status: CaseCommentWorkflowStatus,
        row_kept: bool,
    ) -> None:
        """A missing definition fails the comment only if its run never started."""
        picked = await _add_picked_comment(session, svc_role, test_case)

        start = AsyncMock()
        with _dispatch_mocks(
            start, get_definition=AsyncMock(return_value=None)
        ) as service:
            service.settle_lost_start = AsyncMock(return_value=outcome)
            if row_kept:
                with pytest.raises(CaseTriggerStartUnknownError):
                    await _dispatch_only_row()
            else:
                await _dispatch_only_row()

        start.assert_not_awaited()
        service.settle_lost_start.assert_awaited_once_with(picked.exec_id)
        assert await _comment_status(session, picked.comment_id) == status.value
        assert len(await _outbox_rows(session, picked.case_id)) == int(row_kept)

    async def test_disabled_case_triggers_write_no_rows(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The kill switch stops picked comment workflows and configured triggers alike."""
        await _add_trigger(
            session, svc_role, event_types=["comment_created", "case_updated"]
        )
        monkeypatch.setattr(
            "tracecat.cases.triggers.outbox.config.TRACECAT__CASE_TRIGGERS_ENABLED",
            False,
        )
        picked = await _add_picked_comment(session, svc_role, test_case)
        await _add_update_event(session, svc_role, test_case)

        assert await _outbox_rows(session, picked.case_id) == []


def test_case_trigger_exec_id_is_stable_per_event_and_workflow() -> None:
    event_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    exec_id = case_trigger_exec_id(event_id, workflow_id)
    assert exec_id == case_trigger_exec_id(event_id, workflow_id)
    assert exec_id != case_trigger_exec_id(event_id, uuid.uuid4())
    assert exec_id != case_trigger_exec_id(uuid.uuid4(), workflow_id)
    assert exec_id.startswith("wf_")
    assert "/exec_" in exec_id


@pytest.mark.anyio
async def test_disabled_case_triggers_leave_queued_rows_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kill switch also stops the dispatcher from draining queued rows."""
    monkeypatch.setattr(
        "tracecat.cases.triggers.dispatcher.config.TRACECAT__CASE_TRIGGERS_ENABLED",
        False,
    )
    monkeypatch.setattr("tracecat.cases.triggers.dispatcher._IDLE_RETURN_SECONDS", 0.0)
    has_rows = AsyncMock(return_value=True)
    list_rows = AsyncMock(return_value=[1])
    monkeypatch.setattr("tracecat.cases.triggers.dispatcher.outbox_has_rows", has_rows)
    monkeypatch.setattr(
        "tracecat.cases.triggers.dispatcher.list_outbox_row_ids", list_rows
    )

    assert await wait_for_case_trigger_outbox() is False
    assert await dispatch_case_trigger_outbox_batch() == 0
    has_rows.assert_not_awaited()
    list_rows.assert_not_awaited()


@pytest.mark.anyio
async def test_batch_dispatches_rows_concurrently_up_to_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At most the configured number of rows run at once, and a failing row
    neither cancels the rows in flight, stops the rest of the batch, nor fails
    the activity into retry backoff that would delay healthy rows.
    """
    monkeypatch.setattr(
        "tracecat.cases.triggers.dispatcher.config.TRACECAT__CASE_TRIGGER_DISPATCH_CONCURRENCY",
        3,
    )
    row_ids = list(range(1, 11))
    monkeypatch.setattr(
        "tracecat.cases.triggers.dispatcher.list_outbox_row_ids",
        AsyncMock(return_value=row_ids),
    )
    in_flight = 0
    peak = 0
    finished: list[int] = []

    async def dispatch_row(_: CaseTriggerOutboxDispatcher, row_id: int) -> None:
        nonlocal in_flight, peak
        if row_id == 2:
            # Fails while rows 1 and 3 are still in flight.
            raise RuntimeError("temporal unavailable")
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.02)
        finally:
            in_flight -= 1
        finished.append(row_id)

    monkeypatch.setattr(CaseTriggerOutboxDispatcher, "dispatch_row", dispatch_row)
    env = ActivityEnvironment()
    progress: list[int] = []

    def record_heartbeat(*details: object) -> None:
        assert isinstance(finished_so_far := details[0], int)
        progress.append(finished_so_far)

    env.on_heartbeat = record_heartbeat

    assert await env.run(dispatch_case_trigger_outbox_batch) == len(row_ids) - 1

    assert peak == 3
    assert sorted(finished) == [row_id for row_id in row_ids if row_id != 2]
    # Every row heartbeats before it starts, carrying the rows finished so far.
    assert len(progress) == len(row_ids)
    assert progress == sorted(progress)
    assert progress[-1] == len(row_ids) - 3


@pytest.mark.anyio
async def test_batch_fails_after_all_rows_when_a_retry_delay_is_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that could not record its retry delay fails the batch into backoff,
    but only after every other row in the batch was dispatched.
    """
    row_ids = [1, 2, 3, 4]
    monkeypatch.setattr(
        "tracecat.cases.triggers.dispatcher.list_outbox_row_ids",
        AsyncMock(return_value=row_ids),
    )
    finished: list[int] = []

    async def dispatch_row(_: CaseTriggerOutboxDispatcher, row_id: int) -> None:
        if row_id == 2:
            raise CaseTriggerRetryNotRecordedError(row_id)
        await asyncio.sleep(0.01)
        finished.append(row_id)

    monkeypatch.setattr(CaseTriggerOutboxDispatcher, "dispatch_row", dispatch_row)

    with pytest.raises(ApplicationError, match="could not record"):
        await ActivityEnvironment().run(dispatch_case_trigger_outbox_batch)
    assert sorted(finished) == [1, 3, 4]


@pytest.mark.anyio
async def test_overlapping_batches_start_each_run_once(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two overlapping activity attempts never handle the same row twice.

    Every row is dispatched in its own connections and locked with
    ``SKIP LOCKED``, so whichever attempt locks a row first handles it and the
    other skips it.
    """
    monkeypatch.setattr(
        "tracecat.cases.triggers.dispatcher.config.TRACECAT__CASE_TRIGGER_DISPATCH_CONCURRENCY",
        4,
    )
    assert svc_role.workspace_id is not None
    workspace_id = svc_role.workspace_id
    # Committed through the dispatcher's own engine, so every concurrent row
    # sees the data on its own connection.
    async with get_async_session_bypass_rls_context_manager() as session:
        workflow = Workflow(
            title=f"Overlap {uuid.uuid4().hex[:8]}",
            description="Test workflow",
            status="offline",
            workspace_id=workspace_id,
        )
        case = Case(
            workspace_id=workspace_id,
            summary="Overlapping dispatch",
            description="Case trigger outbox test",
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
        session.add_all([workflow, case])
        await session.flush()
        session.add(
            CaseTrigger(
                workspace_id=workspace_id,
                workflow_id=workflow.id,
                status="online",
                event_types=["case_updated"],
                tag_filters=[],
            )
        )
        events = [
            CaseEvent(
                workspace_id=workspace_id,
                case_id=case.id,
                type=CaseEventType.CASE_UPDATED,
                data={},
            )
            for _ in range(12)
        ]
        session.add_all(events)
        await session.flush()
        rows = [
            CaseTriggerOutbox(
                workspace_id=workspace_id, case_id=case.id, event_id=event.id
            )
            for event in events
        ]
        session.add_all(rows)
        await session.commit()
        workflow_id, case_id = workflow.id, case.id
        expected = sorted(case_trigger_exec_id(e.id, workflow_id) for e in events)
    # This database is shared, so both attempts take only this test's rows and
    # never dispatch queued work that belongs to anything else.
    monkeypatch.setattr(
        "tracecat.cases.triggers.dispatcher.list_outbox_row_ids",
        AsyncMock(return_value=[row.surrogate_id for row in rows]),
    )

    started: list[str] = []

    async def start(**kwargs: object) -> None:
        started.append(str(kwargs["wf_exec_id"]))
        # Hold the row's lock long enough for the other attempt to reach it.
        await asyncio.sleep(0.02)

    try:
        with _dispatch_mocks(AsyncMock(side_effect=start)):
            await asyncio.gather(
                ActivityEnvironment().run(dispatch_case_trigger_outbox_batch),
                ActivityEnvironment().run(dispatch_case_trigger_outbox_batch),
            )
        assert sorted(started) == expected
        async with get_async_session_bypass_rls_context_manager() as session:
            assert await _outbox_rows(session, case_id) == []
    finally:
        async with get_async_session_bypass_rls_context_manager() as session:
            await session.execute(delete(Case).where(Case.id == case_id))
            await session.execute(
                delete(CaseTrigger).where(CaseTrigger.workflow_id == workflow_id)
            )
            await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await session.commit()


@pytest.mark.anyio
async def test_concurrent_creates_add_new_tags_in_any_order(svc_role: Role) -> None:
    """Two creates adding the same new tags in opposite orders both succeed.

    Each create holds its new tag rows until it commits. Both create them in
    ref order, so neither holds a tag the other is waiting on. The hook lines
    the creates up after their first new tag, which deadlocked when tags were
    created in the order given.
    """
    suffix = uuid.uuid4().hex[:8]
    alpha, beta = f"alpha-{suffix}", f"beta-{suffix}"
    case_ids: list[uuid.UUID] = []
    # Set up the workspace's case fields schema before the concurrent creates.
    async with get_async_session_bypass_rls_context_manager() as session:
        primer = await CasesService(session=session, role=svc_role).create_case(
            _tagged_case_create("Primer")
        )
        case_ids.append(primer.id)

    arrived = [asyncio.Event(), asyncio.Event()]

    async def create(index: int, tags: list[str]) -> list[str]:
        async with get_async_session_bypass_rls_context_manager() as session:
            cases = CasesService(session=session, role=svc_role)
            create_tag = cases.tags._create_tag_if_missing

            async def lined_up_create_tag(name: str) -> CaseTag:
                tag = await create_tag(name)
                if not arrived[index].is_set():
                    arrived[index].set()
                    # The other create may be blocked on this tag, so don't wait forever.
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(arrived[1 - index].wait(), timeout=2)
                return tag

            with patch.object(
                cases.tags, "_create_tag_if_missing", lined_up_create_tag
            ):
                case = await cases.create_case(
                    _tagged_case_create(f"Concurrent {index}"),
                    tags=tags,
                    create_missing_tags=True,
                )
            case_ids.append(case.id)
            return sorted(tag.ref for tag in case.tags)

    try:
        results = await asyncio.gather(
            create(0, [alpha, beta]), create(1, [beta, alpha])
        )
        assert results == [[alpha, beta], [alpha, beta]]
    finally:
        async with get_async_session_bypass_rls_context_manager() as session:
            await session.execute(delete(Case).where(Case.id.in_(case_ids)))
            await session.execute(
                delete(CaseTag).where(
                    CaseTag.workspace_id == svc_role.workspace_id,
                    CaseTag.ref.in_([alpha, beta]),
                )
            )
            await session.commit()


@pytest.mark.anyio
async def test_tagged_create_waits_for_an_import_that_renames_its_tag(
    svc_role: Role,
) -> None:
    """A create and an import that renames its tag, then alters case fields, both finish.

    The create locks its tags before it writes a case fields row, so it waits
    for the import's rename. Locking them only when linking, after the fields
    row, made the import's `ALTER TABLE` and the create wait on each other.
    """
    assert svc_role.workspace_id is not None
    suffix = uuid.uuid4().hex[:8]
    column = f"probe_{suffix}"
    case_ids: list[uuid.UUID] = []
    async with get_async_session_bypass_rls_context_manager() as session:
        cases = CasesService(session=session, role=svc_role)
        tag = await cases.tags.create_tag(TagCreate(name=f"Renamed {suffix}"))
        primer = await cases.create_case(_tagged_case_create("Primer"))
        case_ids.append(primer.id)
        tag_id, schema = tag.id, cases.fields.schema_name

    async def create() -> None:
        async with get_async_session_bypass_rls_context_manager() as session:
            case = await CasesService(session=session, role=svc_role).create_case(
                _tagged_case_create("Tagged during import"), tags=[str(tag_id)]
            )
            case_ids.append(case.id)

    async def lock_waits(session: AsyncSession) -> int:
        result = await session.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        )
        # pg_stat_activity keeps one snapshot per transaction; end it to poll.
        await session.rollback()
        return int(result or 0)

    try:
        async with (
            get_async_session_bypass_rls_context_manager() as importer,
            get_async_session_bypass_rls_context_manager() as observer,
        ):
            await importer.execute(
                update(CaseTag)
                .where(CaseTag.id == tag_id)
                .values(name=f"Renamed again {suffix}")
            )
            creating = asyncio.create_task(create())
            async with asyncio.timeout(10):
                while not await lock_waits(observer):
                    await asyncio.sleep(0.05)
            await importer.execute(
                text(f'ALTER TABLE "{schema}".case_fields ADD COLUMN {column} TEXT')
            )
            await importer.commit()
            await asyncio.wait_for(creating, timeout=10)
    finally:
        async with get_async_session_bypass_rls_context_manager() as session:
            await session.execute(delete(Case).where(Case.id.in_(case_ids)))
            await session.execute(delete(CaseTag).where(CaseTag.id == tag_id))
            await session.execute(
                text(
                    f'ALTER TABLE "{schema}".case_fields DROP COLUMN IF EXISTS {column}'
                )
            )
            await session.commit()


@pytest.mark.anyio
async def test_creates_sharing_a_tag_do_not_wait_on_each_other(svc_role: Role) -> None:
    """Two open creates can both lock the same existing tag.

    The lock is `FOR KEY SHARE`, which doesn't conflict with itself, so a
    popular tag never serializes otherwise independent creates.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        tag = await CaseTagsService(session=session, role=svc_role).create_tag(
            TagCreate(name=f"Shared {uuid.uuid4().hex[:8]}")
        )
        tag_id = tag.id
    try:
        async with (
            get_async_session_bypass_rls_context_manager() as first,
            get_async_session_bypass_rls_context_manager() as second,
        ):
            await CaseTagsService(session=first, role=svc_role).resolve_tags(
                [str(tag_id)]
            )
            await second.execute(text("SET LOCAL lock_timeout = '1s'"))
            await CaseTagsService(session=second, role=svc_role).resolve_tags(
                [str(tag_id)]
            )
    finally:
        async with get_async_session_bypass_rls_context_manager() as session:
            await session.execute(delete(CaseTag).where(CaseTag.id == tag_id))
            await session.commit()


def test_comment_workflow_exec_id_must_belong_to_the_workflow() -> None:
    workflow_id = uuid.uuid4()
    fields = {
        "workflow_id": workflow_id,
        "comment_id": uuid.uuid4(),
        "comment": "Run this",
        "triggered_by_type": "service",
    }
    CaseCommentWorkflowTrigger(
        **fields, wf_exec_id=generate_exec_id(WorkflowUUID.new(workflow_id))
    )
    own = generate_exec_id(WorkflowUUID.new(workflow_id))
    other = generate_exec_id(WorkflowUUID.new(uuid.uuid4()))
    for bad in (other, f"{own}/../../other", f"{own}:x"):
        with pytest.raises(ValueError, match="does not belong"):
            CaseCommentWorkflowTrigger(**fields, wf_exec_id=bad)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("workflow_type", "describe_error", "expected"),
    [
        ("WorkflowStartTombstone", None, "not_started"),
        ("DSLWorkflow", None, "started"),
        (None, RuntimeError("temporal unavailable"), "unknown"),
    ],
)
async def test_settling_an_id_again_recognizes_its_own_tombstone(
    workflow_type: str | None,
    describe_error: Exception | None,
    expected: str,
) -> None:
    """A second settle of the same ID must not mistake the tombstone for a run."""
    wf_exec_id = generate_exec_id(WorkflowUUID.new(uuid.uuid4()))
    client = MagicMock()
    client.start_workflow = AsyncMock(
        side_effect=WorkflowAlreadyStartedError(wf_exec_id, "WorkflowStartTombstone")
    )
    handle = MagicMock()
    handle.describe = AsyncMock(
        side_effect=describe_error,
        return_value=SimpleNamespace(workflow_type=workflow_type),
    )
    client.get_workflow_handle = MagicMock(return_value=handle)
    service = WorkflowExecutionsService(client, role=None)

    assert await service.settle_lost_start(wf_exec_id) == expected
