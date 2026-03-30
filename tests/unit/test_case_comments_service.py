import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES, SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import (
    CaseCommentCreate,
    CaseCommentUpdate,
    CaseCommentWorkflowStatus,
    CaseCreate,
)
from tracecat.cases.service import CaseCommentsService, CasesService
from tracecat.db.models import Case, CaseComment, CaseEvent, Workflow
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatAuthorizationError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import WorkflowUUID

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def stub_case_duration_sync() -> Iterator[None]:
    with patch(
        "tracecat.cases.service.CaseDurationService.sync_case_durations",
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.fixture(autouse=True)
def stub_case_addons_entitlement() -> Iterator[None]:
    with patch.object(
        CaseCommentsService,
        "has_entitlement",
        new=AsyncMock(return_value=True),
    ):
        yield


async def _load_case_events(
    session: AsyncSession, case_id: uuid.UUID
) -> list[CaseEvent]:
    result = await session.execute(
        select(CaseEvent)
        .where(CaseEvent.case_id == case_id)
        .order_by(CaseEvent.created_at, CaseEvent.surrogate_id)
    )
    return list(result.scalars().all())


@pytest.fixture
def audit_event_calls(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    calls: list[SimpleNamespace] = []

    async def mock_create_event(
        self: AuditService,
        *,
        resource_type: str,
        action: str,
        resource_id: uuid.UUID | None = None,
        status: AuditEventStatus = AuditEventStatus.SUCCESS,
        data: dict[str, object] | None = None,
    ) -> None:
        del self
        calls.append(
            SimpleNamespace(
                resource_type=resource_type,
                action=action,
                resource_id=resource_id,
                status=status.value,
                data=data or {},
            )
        )

    monkeypatch.setattr(AuditService, "create_event", mock_create_event)
    return calls


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(session: AsyncSession) -> None:
    """Test that service initialization requires a workspace ID."""
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
        CaseCommentsService(session=session, role=role_without_workspace)


@pytest.fixture
def test_comment_id() -> uuid.UUID:
    """Return a fixed test comment ID for testing."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def test_user_id() -> uuid.UUID:
    """Return a fixed test user ID for testing."""
    return uuid.uuid4()


@pytest.fixture
async def case_comments_service(
    session: AsyncSession, svc_role: Role
) -> CaseCommentsService:
    """Create a case comments service instance for testing."""
    return CaseCommentsService(session=session, role=svc_role)


@pytest.fixture
async def test_case(session: AsyncSession, svc_role: Role) -> Case:
    """Create a test case for use in comments tests."""
    cases_service = CasesService(session=session, role=svc_role)

    case = await cases_service.create_case(
        CaseCreate(
            summary="Test Case for Comments",
            description="This is a test case for comment testing",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )
    return case


@pytest.fixture
async def workflow(session: AsyncSession, svc_role: Role) -> Workflow:
    workflow = Workflow(
        title="Escalate case",
        description="Workflow-backed case comment test",
        status="online",
        alias="escalate_case",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow)
    return workflow


@pytest.fixture
def comment_create_params() -> CaseCommentCreate:
    """Sample comment creation parameters."""
    return CaseCommentCreate(
        content="This is a test comment",
        parent_id=None,
    )


@pytest.mark.anyio
class TestCaseCommentsService:
    async def test_create_and_get_comment(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        """Test creating and retrieving a comment."""
        existing_audit_count = len(audit_event_calls)
        # Create comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        assert created_comment.content == comment_create_params.content
        assert created_comment.parent_id == comment_create_params.parent_id
        assert created_comment.case_id == test_case.id
        assert created_comment.user_id == case_comments_service.role.user_id
        assert created_comment.workspace_id == case_comments_service.workspace_id

        case_events = await _load_case_events(session, test_case.id)
        assert [event.type for event in case_events] == [
            CaseEventType.CASE_CREATED,
            CaseEventType.COMMENT_CREATED,
        ]
        assert case_events[-1].data["comment_id"] == str(created_comment.id)
        assert case_events[-1].data["parent_id"] is None
        assert case_events[-1].data["thread_root_id"] == str(created_comment.id)

        audit_events = audit_event_calls[existing_audit_count:]
        assert [event.status for event in audit_events] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.SUCCESS.value,
        ]
        assert all(event.resource_type == "case_comment" for event in audit_events)
        assert audit_events[-1].data["content"] == comment_create_params.content

        # Retrieve comment
        retrieved_comment = await case_comments_service.get_comment(created_comment.id)
        assert retrieved_comment is not None
        assert retrieved_comment.id == created_comment.id
        assert retrieved_comment.content == comment_create_params.content
        assert retrieved_comment.parent_id == comment_create_params.parent_id
        assert retrieved_comment.user_id == case_comments_service.role.user_id

    async def test_create_workflow_backed_comment(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        workflow: Workflow,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        existing_audit_count = len(audit_event_calls)
        workflow_ref = WorkflowUUID.new(workflow.id)

        with patch(
            "tracecat.cases.service.publish_case_event_payload",
            new=AsyncMock(),
        ):
            created_comment = await case_comments_service.create_comment(
                test_case,
                CaseCommentCreate(
                    content="Run this workflow",
                    workflow_id=workflow_ref,
                ),
            )

        assert created_comment.workflow_id == workflow.id
        assert created_comment.workflow_title == workflow.title
        assert created_comment.workflow_alias == workflow.alias
        assert created_comment.workflow_wf_exec_id is not None
        assert (
            created_comment.workflow_status == CaseCommentWorkflowStatus.RUNNING.value
        )

        serialized = case_comments_service.serialize_comment(created_comment)
        assert serialized.workflow is not None
        assert serialized.workflow.workflow_id == workflow.id
        assert serialized.workflow.title == workflow.title
        assert serialized.workflow.alias == workflow.alias
        assert serialized.workflow.wf_exec_id == created_comment.workflow_wf_exec_id
        assert serialized.workflow.status == CaseCommentWorkflowStatus.RUNNING

        audit_events = audit_event_calls[existing_audit_count:]
        comment_audits = [
            event for event in audit_events if event.resource_type == "case_comment"
        ]
        workflow_audits = [
            event
            for event in audit_events
            if event.resource_type == "workflow_execution"
        ]
        assert [event.status for event in comment_audits[-2:]] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.SUCCESS.value,
        ]
        assert [event.status for event in workflow_audits] == [
            AuditEventStatus.ATTEMPT.value
        ]
        assert workflow_audits[0].data["workflow_id"] == str(workflow.id)
        assert workflow_audits[0].data["comment_id"] == str(created_comment.id)
        assert (
            workflow_audits[0].data["wf_exec_id"] == created_comment.workflow_wf_exec_id
        )

        await session.delete(workflow)
        await session.commit()

        persisted_comment = await case_comments_service.get_comment(created_comment.id)
        assert persisted_comment is not None
        assert persisted_comment.workflow_id == workflow.id

        serialized_after_delete = case_comments_service.serialize_comment(
            persisted_comment
        )
        assert serialized_after_delete.workflow is not None
        assert serialized_after_delete.workflow.workflow_id == workflow.id
        assert serialized_after_delete.workflow.title == workflow.title
        assert serialized_after_delete.workflow.alias == workflow.alias

    async def test_serialize_comment_preserves_workflow_snapshots_without_workflow_id(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
    ) -> None:
        now = datetime.now(UTC)
        comment = CaseComment(
            id=uuid.uuid4(),
            workspace_id=case_comments_service.workspace_id,
            case_id=test_case.id,
            content="Run this workflow",
            parent_id=None,
            user_id=case_comments_service.role.user_id,
            workflow_id=None,
            workflow_title="Escalate case",
            workflow_alias="escalate_case",
            workflow_wf_exec_id="wf_123/exec_456",
            workflow_status=CaseCommentWorkflowStatus.RUNNING.value,
            created_at=now,
            updated_at=now,
        )

        serialized = case_comments_service.serialize_comment(comment)

        assert serialized.workflow is not None
        assert serialized.workflow.workflow_id is None
        assert serialized.workflow.title == "Escalate case"
        assert serialized.workflow.alias == "escalate_case"
        assert serialized.workflow.wf_exec_id == "wf_123/exec_456"
        assert serialized.workflow.status == CaseCommentWorkflowStatus.RUNNING

    async def test_create_workflow_backed_comment_publishes_explicit_trigger(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        workflow: Workflow,
    ) -> None:
        callbacks: list[object] = []

        with (
            patch(
                "tracecat.cases.service.add_after_commit_callback",
                side_effect=lambda _session, _callback: callbacks.append(_callback),
            ),
            patch(
                "tracecat.cases.service.publish_case_event_payload",
                new=AsyncMock(),
            ) as publish_mock,
        ):
            created_comment = await case_comments_service.create_comment(
                test_case,
                CaseCommentCreate(
                    content="Run this workflow",
                    workflow_id=WorkflowUUID.new(workflow.id),
                ),
            )

        assert callbacks == []
        workflow_publish = next(
            kwargs
            for _, kwargs in publish_mock.await_args_list
            if kwargs.get("extra_fields", {}).get("workflow_id") == str(workflow.id)
        )
        assert workflow_publish["event_id"]
        assert workflow_publish["case_id"] == str(test_case.id)
        assert workflow_publish["workspace_id"] == str(test_case.workspace_id)
        assert workflow_publish["extra_fields"]["comment_id"] == str(created_comment.id)
        assert workflow_publish["extra_fields"]["comment"] == "Run this workflow"
        assert workflow_publish["extra_fields"]["parent_id"] is None
        assert workflow_publish["extra_fields"]["wf_exec_id"] == (
            created_comment.workflow_wf_exec_id
        )
        assert workflow_publish["extra_fields"]["text"] == "Run this workflow"

    async def test_create_workflow_backed_reply_publishes_parent_context(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        workflow: Workflow,
    ) -> None:
        parent_comment = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent comment"),
        )

        with patch(
            "tracecat.cases.service.publish_case_event_payload",
            new=AsyncMock(),
        ) as publish_mock:
            created_reply = await case_comments_service.create_comment(
                test_case,
                CaseCommentCreate(
                    content="Reply workflow",
                    parent_id=parent_comment.id,
                    workflow_id=WorkflowUUID.new(workflow.id),
                ),
            )

        workflow_publish = next(
            kwargs
            for _, kwargs in publish_mock.await_args_list
            if kwargs.get("extra_fields", {}).get("workflow_id") == str(workflow.id)
        )
        assert workflow_publish["extra_fields"]["comment_id"] == str(created_reply.id)
        assert workflow_publish["extra_fields"]["parent_id"] == str(parent_comment.id)
        assert workflow_publish["extra_fields"]["comment"] == "Reply workflow"

    async def test_create_workflow_backed_comment_marks_failed_when_publish_fails(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        workflow: Workflow,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        existing_audit_count = len(audit_event_calls)

        with (
            patch(
                "tracecat.cases.service.add_after_commit_callback",
                side_effect=lambda _session, _callback: None,
            ),
            patch(
                "tracecat.cases.service.publish_case_event_payload",
                new=AsyncMock(side_effect=RuntimeError("redis unavailable")),
            ),
        ):
            created_comment = await case_comments_service.create_comment(
                test_case,
                CaseCommentCreate(
                    content="Run this workflow",
                    workflow_id=WorkflowUUID.new(workflow.id),
                ),
            )

        assert created_comment.workflow_status == CaseCommentWorkflowStatus.FAILED.value

        refreshed_comment = await case_comments_service.get_comment(created_comment.id)
        assert refreshed_comment is not None
        assert (
            refreshed_comment.workflow_status == CaseCommentWorkflowStatus.FAILED.value
        )

        workflow_audits = [
            event
            for event in audit_event_calls[existing_audit_count:]
            if event.resource_type == "workflow_execution"
        ]
        assert [event.status for event in workflow_audits] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.FAILURE.value,
        ]

    async def test_create_workflow_backed_comment_requires_case_addons(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        workflow: Workflow,
    ) -> None:
        with patch.object(
            case_comments_service,
            "has_entitlement",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(EntitlementRequired, match="case_addons"):
                await case_comments_service.create_comment(
                    test_case,
                    CaseCommentCreate(
                        content="Run this workflow",
                        workflow_id=WorkflowUUID.new(workflow.id),
                    ),
                )

    async def test_create_workflow_backed_comment_requires_workflow_execute_scope(
        self,
        session: AsyncSession,
        test_case: Case,
        workflow: Workflow,
        svc_role: Role,
    ) -> None:
        role = svc_role.model_copy(update={"scopes": frozenset({"case:update"})})
        service = CaseCommentsService(session=session, role=role)

        with pytest.raises(ScopeDeniedError) as exc_info:
            await service.create_comment(
                test_case,
                CaseCommentCreate(
                    content="Run this workflow",
                    workflow_id=WorkflowUUID.new(workflow.id),
                ),
            )

        assert exc_info.value.missing_scopes == ["workflow:execute"]

    async def test_list_comments(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        """Test listing comments."""
        existing_audit_count = len(audit_event_calls)
        # Create two comments
        comment1 = await case_comments_service.create_comment(
            test_case, comment_create_params
        )

        # Create a second comment with different content
        comment2 = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(
                content="This is another test comment",
                parent_id=None,
            ),
        )

        # Create a reply to the first comment
        comment3 = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(
                content="This is a reply to the first comment",
                parent_id=comment1.id,
            ),
        )

        # List all comments
        comments = await case_comments_service.list_comments(test_case)
        assert len(comments) == 3

        # Check that all our comments are in the list
        comment_ids = {comment.id for comment in comments}
        assert comment1.id in comment_ids
        assert comment2.id in comment_ids
        assert comment3.id in comment_ids

        # Check parent-child relationship
        for comment in comments:
            if comment.id == comment3.id:
                assert comment.parent_id == comment1.id

        case_events = await _load_case_events(session, test_case.id)
        event_counts = Counter(event.type for event in case_events)
        assert event_counts[CaseEventType.COMMENT_CREATED] == 2
        assert event_counts[CaseEventType.COMMENT_REPLY_CREATED] == 1

        audit_events = audit_event_calls[existing_audit_count:]
        audit_counts = Counter(event.status for event in audit_events)
        assert audit_counts[AuditEventStatus.ATTEMPT.value] == 3
        assert audit_counts[AuditEventStatus.SUCCESS.value] == 3
        reply_audit = next(
            event
            for event in audit_events
            if event.data["parent_id"] == str(comment1.id)
        )
        assert reply_audit.data["is_reply"] is True
        assert reply_audit.data["thread_root_id"] == str(comment1.id)

    async def test_list_comment_threads(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Test listing threaded comments."""
        parent = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(
                content="Thread reply",
                parent_id=parent.id,
            ),
        )

        threads = await case_comments_service.list_comment_threads(test_case)

        assert len(threads) == 1
        thread = threads[0]
        assert thread.comment.id == parent.id
        assert thread.reply_count == 1
        assert len(thread.replies) == 1
        assert thread.replies[0].id == reply.id

    async def test_list_comment_threads_groups_orphan_siblings(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
    ) -> None:
        parent_id = uuid.uuid4()
        now = datetime.now(UTC)
        orphan_one = SimpleNamespace(
            id=uuid.uuid4(),
            case_id=test_case.id,
            workspace_id=case_comments_service.workspace_id,
            content="First orphan reply",
            parent_id=parent_id,
            user_id=case_comments_service.role.user_id,
            created_at=now,
            updated_at=now,
            last_edited_at=None,
            deleted_at=None,
        )
        orphan_two = SimpleNamespace(
            id=uuid.uuid4(),
            case_id=test_case.id,
            workspace_id=case_comments_service.workspace_id,
            content="Second orphan reply",
            parent_id=parent_id,
            user_id=case_comments_service.role.user_id,
            created_at=now,
            updated_at=now,
            last_edited_at=None,
            deleted_at=None,
        )

        with patch.object(
            case_comments_service,
            "_list_comment_rows",
            new=AsyncMock(return_value=[(orphan_one, None), (orphan_two, None)]),
        ):
            threads = await case_comments_service.list_comment_threads(test_case)

        assert len(threads) == 1
        assert threads[0].comment.id == orphan_one.id
        assert threads[0].reply_count == 1
        assert [reply.id for reply in threads[0].replies] == [orphan_two.id]

    async def test_list_comment_threads_requires_case_addons(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        parent = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Thread reply", parent_id=parent.id),
        )
        with patch.object(
            case_comments_service,
            "has_entitlement",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(EntitlementRequired, match="case_addons"):
                await case_comments_service.list_comment_threads(test_case)

    async def test_get_comment_thread_requires_case_addons(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        parent = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Thread reply", parent_id=parent.id),
        )
        with patch.object(
            case_comments_service,
            "has_entitlement",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(EntitlementRequired, match="case_addons"):
                await case_comments_service.get_comment_thread(reply.id)

    async def test_create_reply_rejects_cross_case_parent(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        svc_role: Role,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        """Reply creation should reject parents from another case."""
        cases_service = CasesService(session=session, role=svc_role)
        existing_audit_count = len(audit_event_calls)
        first_case = await cases_service.create_case(
            case_params := CaseCreate(
                summary="First case",
                description="Case one",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        second_case = await cases_service.create_case(
            case_params.model_copy(update={"summary": "Second case"})
        )
        parent = await case_comments_service.create_comment(
            first_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )

        with pytest.raises(
            TracecatValidationError, match="Parent comment must belong to the same case"
        ):
            await case_comments_service.create_comment(
                second_case,
                CaseCommentCreate(content="Cross-case reply", parent_id=parent.id),
            )

        audit_events = audit_event_calls[existing_audit_count:]
        create_audits = [event for event in audit_events if event.action == "create"]
        assert [event.status for event in create_audits[-2:]] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.FAILURE.value,
        ]
        assert create_audits[-1].data["parent_id"] == str(parent.id)

    async def test_create_reply_requires_case_addons(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        existing_audit_count = len(audit_event_calls)
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        with patch.object(
            case_comments_service,
            "has_entitlement",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(EntitlementRequired, match="case_addons"):
                await case_comments_service.create_comment(
                    test_case,
                    CaseCommentCreate(content="Reply", parent_id=parent.id),
                )

        audit_events = audit_event_calls[existing_audit_count:]
        create_audits = [event for event in audit_events if event.action == "create"]
        assert [event.status for event in create_audits[-2:]] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.FAILURE.value,
        ]
        assert create_audits[-1].data["parent_id"] == str(parent.id)

    async def test_create_reply_rejects_reply_parent(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
    ) -> None:
        """Reply creation should reject replies to replies."""
        parent = await case_comments_service.create_comment(
            test_case, comment_create_params
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        with pytest.raises(
            TracecatValidationError, match="Replies cannot have replies"
        ):
            await case_comments_service.create_comment(
                test_case,
                CaseCommentCreate(content="Nested reply", parent_id=reply.id),
            )

    async def test_update_comment(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        """Test updating a comment."""
        existing_audit_count = len(audit_event_calls)
        # Create a comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )

        # Update parameters
        update_params = CaseCommentUpdate(content="Updated test comment content")

        # Update comment
        updated_comment = await case_comments_service.update_comment(
            created_comment, update_params
        )
        assert updated_comment.content == update_params.content

        # Verify updates persisted
        retrieved_comment = await case_comments_service.get_comment(created_comment.id)
        assert retrieved_comment is not None
        assert retrieved_comment.content == update_params.content

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_UPDATED
        assert case_events[-1].data["comment_id"] == str(created_comment.id)

        audit_events = audit_event_calls[existing_audit_count:]
        update_audits = [event for event in audit_events if event.action == "update"]
        assert [event.status for event in update_audits] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.SUCCESS.value,
        ]
        assert update_audits[-1].data["content"] == update_params.content

    async def test_update_reply_emits_reply_activity(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        existing_audit_count = len(audit_event_calls)
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        await case_comments_service.update_comment(
            reply,
            CaseCommentUpdate(content="Updated reply"),
        )

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_REPLY_UPDATED

        audit_events = audit_event_calls[existing_audit_count:]
        update_audits = [event for event in audit_events if event.action == "update"]
        assert update_audits[-1].data["parent_id"] == str(parent.id)
        assert update_audits[-1].data["is_reply"] is True

    async def test_update_comment_rejects_workflow_backed_comments(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        workflow: Workflow,
    ) -> None:
        comment = CaseComment(
            id=uuid.uuid4(),
            workspace_id=case_comments_service.workspace_id,
            case_id=test_case.id,
            content="Run this workflow",
            user_id=case_comments_service.role.user_id,
            workflow_id=workflow.id,
            workflow_title=workflow.title,
            workflow_alias=workflow.alias,
            workflow_wf_exec_id="wf_123/exec_456",
            workflow_status=CaseCommentWorkflowStatus.RUNNING.value,
        )
        session.add(comment)
        await session.commit()
        await session.refresh(comment)

        with pytest.raises(
            TracecatValidationError,
            match="Workflow-backed comments cannot be edited",
        ):
            await case_comments_service.update_comment(
                comment,
                CaseCommentUpdate(content="Updated"),
            )

    async def test_update_comment_rejects_reparenting(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
    ) -> None:
        """Updating parent_id should be rejected."""
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        with pytest.raises(
            TracecatValidationError, match="Changing a comment parent is not supported"
        ):
            await case_comments_service.update_comment(
                reply,
                CaseCommentUpdate(parent_id=parent.id),
            )

    async def test_update_comment_authorization(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        test_user_id: uuid.UUID,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        """Test that a user can only update their own comments."""
        existing_audit_count = len(audit_event_calls)
        # Create service with original user
        service1 = CaseCommentsService(session=session, role=svc_role)

        # Create a comment as the first user
        created_comment = await service1.create_comment(
            test_case, comment_create_params
        )

        # Create a different role with a different user ID
        different_role = Role(
            type=svc_role.type,
            user_id=test_user_id,  # Different user ID
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=ADMIN_SCOPES,
        )

        # Create service with different user
        service2 = CaseCommentsService(session=session, role=different_role)

        # Try to update the comment with a different user
        update_params = CaseCommentUpdate(content="Attempted update by different user")

        # Should raise authorization error
        with pytest.raises(TracecatAuthorizationError):
            await service2.update_comment(created_comment, update_params)

        # Verify the comment wasn't updated
        retrieved_comment = await service1.get_comment(created_comment.id)
        assert retrieved_comment is not None
        assert retrieved_comment.content == comment_create_params.content

        audit_events = audit_event_calls[existing_audit_count:]
        failed_update_audits = [
            event for event in audit_events if event.action == "update"
        ]
        assert [event.status for event in failed_update_audits] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.FAILURE.value,
        ]

    async def test_delete_comment(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        """Test deleting a comment."""
        existing_audit_count = len(audit_event_calls)
        # Create a comment
        created_comment = await case_comments_service.create_comment(
            test_case, comment_create_params
        )

        # Delete the comment
        await case_comments_service.delete_comment(created_comment)

        # Verify deletion
        deleted_comment = await case_comments_service.get_comment(created_comment.id)
        assert deleted_comment is None

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_DELETED
        assert case_events[-1].data["delete_mode"] == "hard"

        audit_events = audit_event_calls[existing_audit_count:]
        delete_audits = [event for event in audit_events if event.action == "delete"]
        assert [event.status for event in delete_audits] == [
            AuditEventStatus.ATTEMPT.value,
            AuditEventStatus.SUCCESS.value,
        ]
        assert "content" not in delete_audits[-1].data
        assert delete_audits[-1].data["delete_mode"] == "hard"

    async def test_delete_reply_hard_deletes_leaf(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
    ) -> None:
        """Replies are hard deleted."""
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        reply = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        await case_comments_service.delete_comment(reply)

        deleted_reply = await case_comments_service.get_comment(reply.id)
        assert deleted_reply is None

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_REPLY_DELETED
        assert case_events[-1].data["delete_mode"] == "hard"

    async def test_delete_thread_starter_with_replies_soft_deletes(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        """Top-level comments with replies are soft deleted and rendered as tombstones."""
        existing_audit_count = len(audit_event_calls)
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        await case_comments_service.delete_comment(parent)

        deleted_parent = await case_comments_service.get_comment(parent.id)
        assert deleted_parent is not None
        assert deleted_parent.deleted_at is not None

        threads = await case_comments_service.list_comment_threads(test_case)
        assert len(threads) == 1
        assert threads[0].comment.id == parent.id
        assert threads[0].comment.is_deleted is True
        assert threads[0].comment.content == "Comment deleted"

        case_events = await _load_case_events(session, test_case.id)
        assert case_events[-1].type == CaseEventType.COMMENT_DELETED
        assert case_events[-1].data["delete_mode"] == "soft"

        audit_events = audit_event_calls[existing_audit_count:]
        delete_audits = [event for event in audit_events if event.action == "delete"]
        assert delete_audits[-1].data["delete_mode"] == "soft"
        assert "content" not in delete_audits[-1].data

    async def test_delete_thread_starter_with_replies_is_idempotent(
        self,
        case_comments_service: CaseCommentsService,
        session: AsyncSession,
        test_case: Case,
        audit_event_calls: list[SimpleNamespace],
    ) -> None:
        parent = await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Parent", parent_id=None),
        )
        await case_comments_service.create_comment(
            test_case,
            CaseCommentCreate(content="Reply", parent_id=parent.id),
        )

        await case_comments_service.delete_comment(parent)
        event_count = len(await _load_case_events(session, test_case.id))
        audit_count = len(audit_event_calls)

        deleted_parent = await case_comments_service.get_comment(parent.id)
        assert deleted_parent is not None
        await case_comments_service.delete_comment(deleted_parent)

        assert len(await _load_case_events(session, test_case.id)) == event_count
        assert len(audit_event_calls) == audit_count

    async def test_create_comment_swallows_success_audit_failures(
        self,
        case_comments_service: CaseCommentsService,
        test_case: Case,
    ) -> None:
        async def audit_side_effect(
            *,
            action: str,
            comment_id: uuid.UUID,
            status: AuditEventStatus,
            data: dict[str, object],
        ) -> None:
            del action, comment_id, data
            if status is AuditEventStatus.SUCCESS:
                raise RuntimeError("audit store unavailable")

        with patch.object(
            case_comments_service,
            "_audit_comment_event",
            new=AsyncMock(side_effect=audit_side_effect),
        ):
            created_comment = await case_comments_service.create_comment(
                test_case,
                CaseCommentCreate(content="Comment", parent_id=None),
            )

        assert created_comment.id is not None
        assert await case_comments_service.get_comment(created_comment.id) is not None

    async def test_comment_content_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValidationError, match="Comment content cannot be blank"):
            CaseCommentCreate(content="   ")

        with pytest.raises(ValidationError, match="Comment content cannot be blank"):
            CaseCommentUpdate(content="   ")

    async def test_comment_content_is_stripped(self) -> None:
        assert CaseCommentCreate(content="  hello  ").content == "hello"
        assert CaseCommentUpdate(content="  hello  ").content == "hello"

    async def test_delete_comment_authorization(
        self,
        session: AsyncSession,
        svc_role: Role,
        test_case: Case,
        comment_create_params: CaseCommentCreate,
        test_user_id: uuid.UUID,
    ) -> None:
        """Test that a user can only delete their own comments."""
        # Create service with original user
        service1 = CaseCommentsService(session=session, role=svc_role)

        # Create a comment as the first user
        created_comment = await service1.create_comment(
            test_case, comment_create_params
        )

        # Create a different role with a different user ID
        different_role = Role(
            type=svc_role.type,
            user_id=test_user_id,  # Different user ID
            workspace_id=svc_role.workspace_id,
            organization_id=svc_role.organization_id,
            service_id=svc_role.service_id,
            scopes=ADMIN_SCOPES,
        )

        # Create service with different user
        service2 = CaseCommentsService(session=session, role=different_role)

        # Try to delete the comment with a different user
        # Should raise authorization error
        with pytest.raises(TracecatAuthorizationError):
            await service2.delete_comment(created_comment)

        # Verify the comment wasn't deleted
        retrieved_comment = await service1.get_comment(created_comment.id)
        assert retrieved_comment is not None
