"""Unit tests for Temporal search attributes.

Objectives
----------
1. Test that search attributes are properly set when dispatching workflows
2. Test that search attributes are properly set for scheduled workflows
3. Test search attribute behavior for different trigger types
4. Test workspace_id search attribute is properly included
"""

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from temporalio.client import Client
from temporalio.common import TypedSearchAttributes

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import Workspace
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers import UserID, WorkflowID
from tracecat.workflow.executions.common import build_query
from tracecat.workflow.executions.correlation import (
    build_agent_session_correlation_id,
    build_case_task_correlation_id,
    build_tracecat_correlation_id,
)
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.schedules.bridge import build_schedule_search_attributes

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def mock_client() -> Mock:
    """Create a mock Temporal client."""
    return Mock(spec=Client)


@pytest.fixture
def mock_role_with_workspace(svc_workspace: Workspace, mock_user_id: UserID) -> Role:
    """Create a test role with workspace ID."""
    return Role(
        type="service",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=mock_user_id,
        service_id="tracecat-service",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
    )


@pytest.fixture
def mock_role_without_workspace() -> Role:
    """Create a test role without workspace ID."""
    return Role(
        type="service",
        workspace_id=None,
        user_id=None,
        service_id="tracecat-service",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
    )


@pytest.fixture
def test_workflow_id() -> WorkflowID:
    """Create a test workflow ID."""
    return WorkflowID(str(uuid.uuid4()))


@pytest.fixture
def mock_dsl() -> DSLInput:
    """Create a minimal DSL for testing."""
    return DSLInput(
        title="Test Workflow",
        description="Test workflow for search attributes",
        entrypoint=DSLEntrypoint(ref="test_action"),
        actions=[
            ActionStatement(
                ref="test_action",
                action="core.transform.reshape",
                args={},
            )
        ],
        triggers=[],
        returns=None,
    )


@pytest.mark.anyio
class TestWorkflowExecutionSearchAttributes:
    """Test search attributes on workflow executions."""

    @pytest.mark.parametrize(
        ("method_name", "temporal_method"),
        [
            ("create_workflow_execution", "execute_workflow"),
            ("create_draft_workflow_execution", "execute_workflow"),
            ("create_workflow_execution_wait_for_start", "start_workflow"),
            ("create_draft_workflow_execution_wait_for_start", "start_workflow"),
        ],
    )
    async def test_all_blocking_execution_entrypoints_include_workspace_search_attr(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
        method_name: str,
        temporal_method: str,
    ) -> None:
        """Every blocking workflow creation entrypoint must set TracecatWorkspaceId."""
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_with_workspace
        )
        mock_client.execute_workflow = AsyncMock(return_value={"status": "completed"})
        mock_client.start_workflow = AsyncMock(return_value=Mock())

        method = getattr(service, method_name)
        with patch.object(service, "_resolve_execution_timeout", return_value=None):
            await method(
                dsl=mock_dsl,
                wf_id=test_workflow_id,
                trigger_type=TriggerType.MANUAL,
            )

        if temporal_method == "execute_workflow":
            await_args = mock_client.execute_workflow.await_args
        else:
            await_args = mock_client.start_workflow.await_args

        assert await_args is not None
        search_attrs = await_args.kwargs["search_attributes"]
        assert isinstance(search_attrs, TypedSearchAttributes)

        ws_key = TemporalSearchAttr.WORKSPACE_ID.key
        workspace_pairs = [
            pair for pair in search_attrs.search_attributes if pair.key == ws_key
        ]
        assert workspace_pairs, "TracecatWorkspaceId search attribute missing"
        assert workspace_pairs[0].value == str(mock_role_with_workspace.workspace_id)

    @pytest.mark.parametrize(
        "method_name",
        [
            "create_workflow_execution_nowait",
            "create_draft_workflow_execution_nowait",
        ],
    )
    async def test_all_nowait_execution_entrypoints_include_workspace_search_attr(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
        method_name: str,
    ) -> None:
        """Every nowait workflow creation entrypoint must set TracecatWorkspaceId."""
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_with_workspace
        )
        mock_client.start_workflow = AsyncMock(return_value=Mock())
        created_tasks: list[asyncio.Task[None]] = []

        def _capture_ensure_future(coro: Any) -> asyncio.Task[None]:
            task = asyncio.create_task(coro)
            created_tasks.append(task)
            return task

        method = getattr(service, method_name)
        with (
            patch.object(service, "_resolve_execution_timeout", return_value=None),
            patch.object(service, "_handle_background_task_exception", Mock()),
            patch(
                "tracecat.workflow.executions.service.asyncio.ensure_future",
                side_effect=_capture_ensure_future,
            ),
        ):
            method(
                dsl=mock_dsl,
                wf_id=test_workflow_id,
                trigger_type=TriggerType.MANUAL,
            )
            await asyncio.gather(*created_tasks)

        await_args = mock_client.start_workflow.await_args
        assert await_args is not None
        search_attrs = await_args.kwargs["search_attributes"]
        assert isinstance(search_attrs, TypedSearchAttributes)

        ws_key = TemporalSearchAttr.WORKSPACE_ID.key
        workspace_pairs = [
            pair for pair in search_attrs.search_attributes if pair.key == ws_key
        ]
        assert workspace_pairs, "TracecatWorkspaceId search attribute missing"
        assert workspace_pairs[0].value == str(mock_role_with_workspace.workspace_id)

    @pytest.mark.parametrize(
        "trigger_type",
        [TriggerType.MANUAL, TriggerType.SCHEDULED, TriggerType.WEBHOOK],
    )
    async def test_trigger_with_user_sets_all_attributes(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
        trigger_type: TriggerType,
    ) -> None:
        """Each trigger type sets trigger_type, user_id, and workspace_id."""
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_with_workspace
        )

        # Mock execute_workflow to capture search attributes
        captured_search_attrs = None

        async def mock_execute_workflow(*args: Any, **kwargs: Any) -> dict[str, str]:
            nonlocal captured_search_attrs
            captured_search_attrs = kwargs.get("search_attributes")
            return {"status": "completed"}

        mock_client.execute_workflow = mock_execute_workflow

        with patch.object(service, "_resolve_execution_timeout", return_value=None):
            await service.create_workflow_execution(
                dsl=mock_dsl,
                wf_id=test_workflow_id,
                trigger_type=trigger_type,
            )

        # Verify search attributes were set
        assert captured_search_attrs is not None
        assert isinstance(captured_search_attrs, TypedSearchAttributes)

        # Extract the search attribute pairs
        pairs = captured_search_attrs.search_attributes
        assert len(pairs) == 4
        values = {
            pair.key.name: pair.value
            for pair in pairs  # pyright: ignore[reportGeneralTypeIssues]
        }
        assert values == {
            TemporalSearchAttr.EXECUTION_TYPE.value: "published",
            TemporalSearchAttr.TRIGGER_TYPE.value: trigger_type.value,
            TemporalSearchAttr.TRIGGERED_BY_USER_ID.value: str(
                mock_role_with_workspace.user_id
            ),
            TemporalSearchAttr.WORKSPACE_ID.value: str(
                mock_role_with_workspace.workspace_id
            ),
        }

    async def test_service_role_without_workspace_omits_workspace_id(
        self,
        mock_client: Mock,
        mock_role_without_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
    ) -> None:
        """Test that service role without workspace ID doesn't set workspace search attribute."""
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_without_workspace
        )

        captured_search_attrs = None

        async def mock_execute_workflow(*args: Any, **kwargs: Any) -> dict[str, str]:
            nonlocal captured_search_attrs
            captured_search_attrs = kwargs.get("search_attributes")
            return {"status": "completed"}

        mock_client.execute_workflow = mock_execute_workflow

        with patch.object(service, "_resolve_execution_timeout", return_value=None):
            await service.create_workflow_execution(
                dsl=mock_dsl,
                wf_id=test_workflow_id,
                trigger_type=TriggerType.MANUAL,
            )

        assert captured_search_attrs is not None
        pairs = captured_search_attrs.search_attributes

        # Should have execution_type and trigger_type (no user_id or workspace_id)
        assert len(pairs) == 2

        # Verify execution type is set
        execution_type_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.EXECUTION_TYPE.value
        )
        assert execution_type_pair.value == "published"

        # Verify trigger type is set
        trigger_type_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        )
        assert trigger_type_pair.value == TriggerType.MANUAL.value


@pytest.mark.anyio
class TestScheduleSearchAttributes:
    """Test search attributes on scheduled workflows."""

    async def test_build_schedule_search_attributes_with_workspace(
        self, mock_role_with_workspace: Role
    ) -> None:
        """Test building schedule search attributes with workspace ID."""
        search_attrs = build_schedule_search_attributes(mock_role_with_workspace)

        assert isinstance(search_attrs, TypedSearchAttributes)
        pairs = search_attrs.search_attributes

        # Should have trigger_type, execution_type, and workspace_id
        assert len(pairs) == 3

        # Verify trigger type is SCHEDULED
        trigger_pair = next(
            p for p in pairs if p.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        )
        assert trigger_pair.value == TriggerType.SCHEDULED.value

        # Verify execution type is PUBLISHED
        execution_pair = next(
            p for p in pairs if p.key.name == TemporalSearchAttr.EXECUTION_TYPE.value
        )
        assert execution_pair.value == ExecutionType.PUBLISHED.value

        # Verify workspace ID
        workspace_pair = next(
            p for p in pairs if p.key.name == TemporalSearchAttr.WORKSPACE_ID.value
        )
        assert workspace_pair.value == str(mock_role_with_workspace.workspace_id)

    async def test_build_schedule_search_attributes_without_workspace(
        self, mock_role_without_workspace: Role
    ) -> None:
        """Test building schedule search attributes without workspace ID."""
        search_attrs = build_schedule_search_attributes(mock_role_without_workspace)

        assert isinstance(search_attrs, TypedSearchAttributes)
        pairs = search_attrs.search_attributes

        # Should only have trigger_type and execution_type (no workspace_id)
        assert len(pairs) == 2

        # Verify trigger and execution types are set
        trigger_pair = next(
            p for p in pairs if p.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        )
        assert trigger_pair.value == TriggerType.SCHEDULED.value
        execution_pair = next(
            p for p in pairs if p.key.name == TemporalSearchAttr.EXECUTION_TYPE.value
        )
        assert execution_pair.value == ExecutionType.PUBLISHED.value


@pytest.mark.anyio
class TestSearchAttributeQueries:
    """Test that search attributes can be properly queried."""

    async def test_query_by_workspace_id_in_build_query(
        self, mock_role_with_workspace: Role, test_workflow_id: WorkflowID
    ) -> None:
        """Test that build_query constructs queries with search attributes."""
        assert mock_role_with_workspace.workspace_id is not None
        query = build_query(
            workspace_id=mock_role_with_workspace.workspace_id,
            workflow_id=test_workflow_id,
            trigger_types={TriggerType.MANUAL},
            triggered_by_user_id=mock_role_with_workspace.user_id,
        )

        # Verify query includes workspace, trigger type, and user ID
        assert TemporalSearchAttr.WORKSPACE_ID.value in query
        assert str(mock_role_with_workspace.workspace_id) in query
        assert TemporalSearchAttr.TRIGGER_TYPE.value in query
        assert TemporalSearchAttr.TRIGGERED_BY_USER_ID.value in query
        assert str(mock_role_with_workspace.user_id) in query

    async def test_query_with_multiple_trigger_types(
        self, test_workflow_id: WorkflowID
    ) -> None:
        """Test building queries with multiple trigger types."""
        query = build_query(
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            workflow_id=test_workflow_id,
            trigger_types={TriggerType.SCHEDULED, TriggerType.WEBHOOK},
        )

        # Verify query includes both trigger types
        assert TemporalSearchAttr.TRIGGER_TYPE.value in query
        assert TriggerType.SCHEDULED.value in query
        assert TriggerType.WEBHOOK.value in query

    async def test_query_with_workflow_id_only(
        self, test_workflow_id: WorkflowID
    ) -> None:
        """Test building queries with just workflow ID."""
        query = build_query(
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            workflow_id=test_workflow_id,
        )

        # Verify query includes workflow ID
        assert test_workflow_id.short() in query


@pytest.mark.anyio
class TestSearchAttributeEnumMethods:
    async def test_tracecat_correlation_id_helpers(self) -> None:
        session_id = uuid.UUID("00000000-0000-0000-0000-000000000123")

        assert (
            build_tracecat_correlation_id("agent-session", session_id)
            == "agent-session:00000000-0000-0000-0000-000000000123"
        )
        assert (
            build_agent_session_correlation_id(session_id)
            == "agent-session:00000000-0000-0000-0000-000000000123"
        )


@pytest.mark.anyio
class TestCaseTaskCorrelation:
    """Runs started from a case task carry a queryable correlation ID."""

    async def test_case_task_correlation_id(self) -> None:
        task_id = uuid.UUID("00000000-0000-0000-0000-000000000456")
        assert (
            build_case_task_correlation_id(task_id)
            == "case-task:00000000-0000-0000-0000-000000000456"
        )

    async def test_wait_for_start_sets_correlation_search_attr(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
    ) -> None:
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_with_workspace
        )
        mock_client.start_workflow = AsyncMock(return_value=Mock())
        correlation_id = build_case_task_correlation_id(uuid.uuid4())

        with patch.object(service, "_resolve_execution_timeout", return_value=None):
            await service.create_workflow_execution_wait_for_start(
                dsl=mock_dsl,
                wf_id=test_workflow_id,
                correlation_id=correlation_id,
            )

        await_args = mock_client.start_workflow.await_args
        assert await_args is not None
        search_attrs = await_args.kwargs["search_attributes"]
        pairs = [
            pair
            for pair in search_attrs.search_attributes
            if pair.key == TemporalSearchAttr.CORRELATION_ID.key
        ]
        assert [pair.value for pair in pairs] == [correlation_id]

    async def test_no_correlation_search_attr_by_default(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
    ) -> None:
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_with_workspace
        )
        mock_client.start_workflow = AsyncMock(return_value=Mock())

        with patch.object(service, "_resolve_execution_timeout", return_value=None):
            await service.create_workflow_execution_wait_for_start(
                dsl=mock_dsl, wf_id=test_workflow_id
            )

        await_args = mock_client.start_workflow.await_args
        assert await_args is not None
        search_attrs = await_args.kwargs["search_attributes"]
        assert all(
            pair.key != TemporalSearchAttr.CORRELATION_ID.key
            for pair in search_attrs.search_attributes
        )

    async def test_build_query_filters_by_correlation_id(
        self, mock_role_with_workspace: Role
    ) -> None:
        assert mock_role_with_workspace.workspace_id is not None
        correlation_id = build_case_task_correlation_id(uuid.uuid4())
        query = build_query(
            workspace_id=mock_role_with_workspace.workspace_id,
            correlation_id=correlation_id,
        )
        assert f"TracecatCorrelationId = '{correlation_id}'" in query
        assert str(mock_role_with_workspace.workspace_id) in query

    async def test_build_query_rejects_quoted_correlation_id(
        self, mock_role_with_workspace: Role
    ) -> None:
        assert mock_role_with_workspace.workspace_id is not None
        with pytest.raises(ValueError, match="Invalid correlation ID"):
            build_query(
                workspace_id=mock_role_with_workspace.workspace_id,
                correlation_id="case-task:x' OR '1'='1",
            )
