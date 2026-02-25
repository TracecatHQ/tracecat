"""Unit tests for workflow execution workspace scoping."""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.client import Client, WorkflowHandle
from temporalio.common import TypedSearchAttributes

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat.workflow.executions.service import WorkflowExecutionsService

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


@pytest.mark.anyio
class TestWorkflowExecutionWorkspaceFiltering:
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
        assert TemporalSearchAttr.WORKSPACE_ID.value in query
        assert str(mock_role.workspace_id) in query

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
        assert TemporalSearchAttr.WORKSPACE_ID.value in query
        assert str(mock_role.workspace_id) in query
        assert wf_id.short() in query
