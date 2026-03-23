"""Unit tests for workflow execution workspace scoping."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowExecutionStatus,
    WorkflowHandle,
)
from temporalio.common import TypedSearchAttributes

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import CursorPaginationParams
from tracecat.workflow.executions.common import build_query
from tracecat.workflow.executions.enums import ExecutionType, TemporalSearchAttr
from tracecat.workflow.executions.schemas import (
    WorkflowExecutionRelationFilter,
    WorkflowExecutionStatusFilterMode,
    WorkflowExecutionStatusLiteral,
    WorkflowRunReadMinimal,
)
from tracecat.workflow.executions.service import (
    WorkflowExecutionNotFoundError,
    WorkflowExecutionsService,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def mock_client() -> Mock:
    return Mock(spec=Client)


@pytest.fixture
def mock_role(svc_workspace) -> Role:
    return Role(
        type="service",
        workspace_id=svc_workspace.id,
        user_id=None,
        service_id="tracecat-service",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
    )


@pytest.fixture
def mock_role_without_workspace() -> Role:
    return Role(
        type="service",
        workspace_id=None,
        user_id=None,
        service_id="tracecat-service",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
    )


@pytest.mark.anyio
class TestWorkflowExecutionWorkspaceFiltering:
    async def test_workflow_run_read_minimal_from_dataclass_keeps_status_typed(
        self,
    ) -> None:
        execution = cast(
            WorkflowExecution,
            SimpleNamespace(
                id="wf_4itKqkgCZrLhgYiq5L211X/exec_6XG2qg6b9qBD1RJu7KPsJr",
                run_id="run-1",
                start_time=datetime.now(UTC),
                execution_time=None,
                close_time=None,
                status=WorkflowExecutionStatus.COMPLETED,
                workflow_type="DSLWorkflow",
                task_queue="tracecat-task-queue",
                history_length=42,
                parent_id=None,
                typed_search_attributes=TypedSearchAttributes(search_attributes=[]),
            ),
        )

        run = WorkflowRunReadMinimal.from_dataclass(
            execution,
            workflow_id="wf_4itKqkgCZrLhgYiq5L211X",
            workflow_title="Demo workflow",
        )

        assert run.status == WorkflowExecutionStatus.COMPLETED

    async def test_get_execution_returns_none_for_workspace_mismatch(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        execution = Mock()
        execution.id = "wf_abc/exec_abc"
        execution.typed_search_attributes = TypedSearchAttributes(
            search_attributes=[
                TemporalSearchAttr.WORKSPACE_ID.create_pair(str(uuid.uuid4()))
            ]
        )
        handle = Mock(spec=WorkflowHandle)
        handle.describe = AsyncMock(return_value=execution)
        mock_client.get_workflow_handle_for = Mock(return_value=handle)

        result = await service.get_execution("wf_abc/exec_abc")

        assert result is None

    async def test_get_execution_returns_execution_for_workspace_match(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        execution = Mock()
        execution.id = "wf_abc/exec_abc"
        execution.typed_search_attributes = TypedSearchAttributes(
            search_attributes=[
                TemporalSearchAttr.WORKSPACE_ID.create_pair(str(mock_role.workspace_id))
            ]
        )
        handle = Mock(spec=WorkflowHandle)
        handle.describe = AsyncMock(return_value=execution)
        mock_client.get_workflow_handle_for = Mock(return_value=handle)

        result = await service.get_execution("wf_abc/exec_abc")

        assert result is execution

    async def test_list_executions_includes_workspace_filter(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        with patch.object(
            service, "query_executions", AsyncMock(return_value=[])
        ) as mock_q:
            await service.list_executions()

        mock_q.assert_awaited_once()
        await_args = mock_q.await_args
        assert await_args is not None
        query = await_args.kwargs["query"]
        workspace_clause = (
            f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{mock_role.workspace_id}'"
        )
        assert workspace_clause in query

    async def test_query_executions_enforces_workspace_scope(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)

        class EmptyAsyncIterator:
            def __aiter__(self) -> "EmptyAsyncIterator":
                return self

            async def __anext__(self) -> WorkflowExecution:
                raise StopAsyncIteration

        mock_client.list_workflows = Mock(return_value=EmptyAsyncIterator())

        await service.query_executions(query="ExecutionStatus = 'Running'")

        called_query = mock_client.list_workflows.call_args.kwargs["query"]
        workspace_clause = (
            f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{mock_role.workspace_id}'"
        )
        assert workspace_clause in called_query
        assert f"(ExecutionStatus = 'Running') AND {workspace_clause}" == called_query

    async def test_query_executions_requires_workspace_id(
        self,
        mock_client: Mock,
        mock_role_without_workspace: Role,
    ) -> None:
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_without_workspace
        )
        mock_client.list_workflows = Mock()

        with pytest.raises(
            ValueError, match="Workspace ID is required to query workflow executions"
        ):
            await service.query_executions(query="ExecutionStatus = 'Running'")

        mock_client.list_workflows.assert_not_called()

    async def test_list_executions_by_workflow_id_includes_workspace_filter(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        wf_id = WorkflowUUID.new("wf_4itKqkgCZrLhgYiq5L211X")
        with patch.object(
            service, "query_executions", AsyncMock(return_value=[])
        ) as mock_q:
            await service.list_executions_by_workflow_id(wf_id)

        mock_q.assert_awaited_once()
        await_args = mock_q.await_args
        assert await_args is not None
        query = await_args.kwargs["query"]
        workspace_clause = (
            f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{mock_role.workspace_id}'"
        )
        assert workspace_clause in query
        assert wf_id.short() in query

    async def test_require_execution_raises_for_missing_execution(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        with patch.object(service, "get_execution", AsyncMock(return_value=None)):
            with pytest.raises(WorkflowExecutionNotFoundError):
                await service.require_execution("wf_abc/exec_abc")

    async def test_cancel_workflow_execution_requires_execution_visibility(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        handle = Mock(spec=WorkflowHandle)
        handle.cancel = AsyncMock()

        with (
            patch.object(
                service, "require_execution", AsyncMock(return_value=Mock())
            ) as mock_require_execution,
            patch.object(service, "handle", Mock(return_value=handle)) as mock_handle,
        ):
            await service.cancel_workflow_execution("wf_abc/exec_abc")

        mock_require_execution.assert_awaited_once_with("wf_abc/exec_abc")
        mock_handle.assert_called_once_with("wf_abc/exec_abc")
        handle.cancel.assert_awaited_once()

    async def test_cancel_workflow_execution_raises_when_not_visible(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        with (
            patch.object(
                service,
                "require_execution",
                AsyncMock(
                    side_effect=WorkflowExecutionNotFoundError(
                        "Workflow execution not found: wf_abc/exec_abc"
                    )
                ),
            ) as mock_require_execution,
            patch.object(service, "handle", Mock()) as mock_handle,
        ):
            with pytest.raises(WorkflowExecutionNotFoundError):
                await service.cancel_workflow_execution("wf_abc/exec_abc")

        mock_require_execution.assert_awaited_once_with("wf_abc/exec_abc")
        mock_handle.assert_not_called()

    async def test_terminate_workflow_execution_requires_execution_visibility(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        handle = Mock(spec=WorkflowHandle)
        handle.terminate = AsyncMock()

        with (
            patch.object(
                service, "require_execution", AsyncMock(return_value=Mock())
            ) as mock_require_execution,
            patch.object(service, "handle", Mock(return_value=handle)) as mock_handle,
        ):
            await service.terminate_workflow_execution(
                "wf_abc/exec_abc", reason="Testing terminate"
            )

        mock_require_execution.assert_awaited_once_with("wf_abc/exec_abc")
        mock_handle.assert_called_once_with("wf_abc/exec_abc")
        handle.terminate.assert_awaited_once_with(reason="Testing terminate")

    async def test_terminate_workflow_execution_raises_when_not_visible(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        with (
            patch.object(
                service,
                "require_execution",
                AsyncMock(
                    side_effect=WorkflowExecutionNotFoundError(
                        "Workflow execution not found: wf_abc/exec_abc"
                    )
                ),
            ) as mock_require_execution,
            patch.object(service, "handle", Mock()) as mock_handle,
        ):
            with pytest.raises(WorkflowExecutionNotFoundError):
                await service.terminate_workflow_execution("wf_abc/exec_abc")

        mock_require_execution.assert_awaited_once_with("wf_abc/exec_abc")
        mock_handle.assert_not_called()

    async def test_list_executions_paginated_rejects_mismatched_cursor(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        bad_cursor = service._encode_query_cursor(b"token", "wrong-fingerprint")
        with pytest.raises(ValueError, match="Cursor no longer matches"):
            await service.list_executions_paginated(
                pagination=CursorPaginationParams(limit=10, cursor=bad_cursor),
                relation=WorkflowExecutionRelationFilter.ALL,
            )

    async def test_list_executions_paginated_filters_root_and_child(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)
        root_exec = SimpleNamespace(parent_id=None)
        child_exec = SimpleNamespace(parent_id="wf_parent/exec_parent")

        class Iterator:
            def __init__(self) -> None:
                self.current_page = [root_exec, child_exec]
                self.next_page_token = None

            async def fetch_next_page(self, *, page_size: int | None = None) -> None:
                return None

        workspace_clause = (
            f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{mock_role.workspace_id}'"
        )

        mock_client.list_workflows = Mock(return_value=Iterator())

        root_page = await service.list_executions_paginated(
            pagination=CursorPaginationParams(limit=10),
            relation=WorkflowExecutionRelationFilter.ROOT,
        )
        assert root_page.items == [root_exec]
        root_query = mock_client.list_workflows.call_args.kwargs["query"]
        assert workspace_clause in root_query

        mock_client.list_workflows = Mock(return_value=Iterator())
        child_page = await service.list_executions_paginated(
            pagination=CursorPaginationParams(limit=10),
            relation=WorkflowExecutionRelationFilter.CHILD,
        )
        assert child_page.items == [child_exec]
        child_query = mock_client.list_workflows.call_args.kwargs["query"]
        assert workspace_clause in child_query

    async def test_list_executions_paginated_applies_status_filter(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)

        class Iterator:
            def __init__(self) -> None:
                self.current_page = []
                self.next_page_token = None

            async def fetch_next_page(self, *, page_size: int | None = None) -> None:
                return None

        workspace_clause = (
            f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{mock_role.workspace_id}'"
        )

        mock_client.list_workflows = Mock(return_value=Iterator())
        statuses: set[WorkflowExecutionStatusLiteral] = {"RUNNING", "FAILED"}
        await service.list_executions_paginated(
            pagination=CursorPaginationParams(limit=10),
            statuses=statuses,
            status_mode=WorkflowExecutionStatusFilterMode.INCLUDE,
        )
        include_query = mock_client.list_workflows.call_args.kwargs["query"]
        assert "ExecutionStatus = 'Failed'" in include_query
        assert "ExecutionStatus = 'Running'" in include_query
        assert workspace_clause in include_query

        mock_client.list_workflows = Mock(return_value=Iterator())
        await service.list_executions_paginated(
            pagination=CursorPaginationParams(limit=10),
            statuses=statuses,
            status_mode=WorkflowExecutionStatusFilterMode.EXCLUDE,
        )
        exclude_query = mock_client.list_workflows.call_args.kwargs["query"]
        assert "ExecutionStatus != 'Failed'" in exclude_query
        assert "ExecutionStatus != 'Running'" in exclude_query
        assert workspace_clause in exclude_query

    async def test_list_executions_paginated_excludes_agent_workflow_type(
        self,
        mock_client: Mock,
        mock_role: Role,
    ) -> None:
        service = WorkflowExecutionsService(client=mock_client, role=mock_role)

        class Iterator:
            def __init__(self) -> None:
                self.current_page = []
                self.next_page_token = None

            async def fetch_next_page(self, *, page_size: int | None = None) -> None:
                return None

        mock_client.list_workflows = Mock(return_value=Iterator())

        await service.list_executions_paginated(
            pagination=CursorPaginationParams(limit=10),
            execution_types={ExecutionType.PUBLISHED},
            exclude_workflow_types={"DurableAgentWorkflow"},
        )

        query = mock_client.list_workflows.call_args.kwargs["query"]
        assert "WorkflowType != 'DurableAgentWorkflow'" in query
        assert (
            f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{mock_role.workspace_id}'"
            in query
        )
        assert f"{TemporalSearchAttr.EXECUTION_TYPE.value} = 'published'" in query
        assert f"{TemporalSearchAttr.EXECUTION_TYPE.value} IS NULL" in query

    async def test_list_executions_paginated_requires_workspace_id(
        self,
        mock_client: Mock,
        mock_role_without_workspace: Role,
    ) -> None:
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_without_workspace
        )
        mock_client.list_workflows = Mock()

        with pytest.raises(
            ValueError, match="Workspace ID is required to query workflow executions"
        ):
            await service.list_executions_paginated(
                pagination=CursorPaginationParams(limit=10)
            )

        mock_client.list_workflows.assert_not_called()

    async def test_build_query_workspace_only(self) -> None:
        workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        query = build_query(workspace_id=workspace_id)
        workspace_clause = f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{workspace_id}'"
        assert query == workspace_clause

    async def test_build_query_workspace_clause_joined_with_and(self) -> None:
        workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        user_id = uuid.UUID("00000000-0000-0000-0000-000000000123")
        query = build_query(
            workspace_id=workspace_id,
            triggered_by_user_id=user_id,
        )
        workspace_clause = f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{workspace_id}'"
        assert query.startswith(workspace_clause)
        assert f"{workspace_clause} AND " in query

    async def test_build_query_supports_workflow_type_exclusion(self) -> None:
        workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        query = build_query(
            workspace_id=workspace_id,
            exclude_workflow_types={"DurableAgentWorkflow"},
        )
        workspace_clause = f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{workspace_id}'"
        assert workspace_clause in query
        assert "WorkflowType != 'DurableAgentWorkflow'" in query

    async def test_build_query_supports_time_duration_user_and_execution_type(
        self,
    ) -> None:
        workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        user_id = uuid.UUID("00000000-0000-0000-0000-000000000123")
        query = build_query(
            workspace_id=workspace_id,
            triggered_by_user_id=user_id,
            execution_types={ExecutionType.PUBLISHED},
            exclude_workflow_types={"DurableAgentWorkflow"},
            start_time_from=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            start_time_to=datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
            close_time_from=datetime(2026, 2, 1, 0, 0, tzinfo=UTC),
            close_time_to=datetime(2026, 2, 28, 23, 59, tzinfo=UTC),
            duration_gte_seconds=60,
            duration_lte_seconds=3600,
        )

        workspace_clause = f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{workspace_id}'"
        assert query.startswith(workspace_clause)
        assert f"{workspace_clause} AND " in query
        assert f"TracecatTriggeredByUserId = '{str(user_id)}'" in query
        assert "TracecatExecutionType = 'published'" in query
        assert "TracecatExecutionType IS NULL" in query
        assert "WorkflowType != 'DurableAgentWorkflow'" in query
        assert "StartTime >=" in query
        assert "StartTime <=" in query
        assert "CloseTime >=" in query
        assert "CloseTime <=" in query
        assert "ExecutionDuration >= '60s'" in query
        assert "ExecutionDuration <= '3600s'" in query
