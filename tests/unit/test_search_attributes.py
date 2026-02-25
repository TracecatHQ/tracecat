"""Unit tests for Temporal search attributes.

Objectives
----------
1. Test that search attributes are properly set when dispatching workflows
2. Test that search attributes are properly set for scheduled workflows
3. Test search attribute behavior for different trigger types
4. Test workspace_id search attribute is properly included
"""

import uuid
from typing import Any
from unittest.mock import Mock, patch

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
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType
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

    async def test_manual_trigger_with_user_sets_all_attributes(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
    ) -> None:
        """Test that manual trigger with user sets trigger_type, user_id, and workspace_id."""
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
                trigger_type=TriggerType.MANUAL,
            )

        # Verify search attributes were set
        assert captured_search_attrs is not None
        assert isinstance(captured_search_attrs, TypedSearchAttributes)

        # Extract the search attribute pairs
        pairs = captured_search_attrs.search_attributes
        assert len(pairs) == 4

        # Verify execution type
        execution_type_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.EXECUTION_TYPE.value
        )
        assert execution_type_pair.value == "published"

        # Verify trigger type
        trigger_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        )
        assert trigger_pair.value == TriggerType.MANUAL.value

        # Verify user ID
        user_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.TRIGGERED_BY_USER_ID.value
        )
        assert user_pair.value == str(mock_role_with_workspace.user_id)

        # Verify workspace ID
        workspace_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.WORKSPACE_ID.value
        )
        assert workspace_pair.value == str(mock_role_with_workspace.workspace_id)

    async def test_scheduled_trigger_sets_trigger_type_and_workspace(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
    ) -> None:
        """Test that scheduled trigger sets trigger_type and workspace_id."""
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
                trigger_type=TriggerType.SCHEDULED,
            )

        # Verify search attributes were set
        assert captured_search_attrs is not None
        pairs = captured_search_attrs.search_attributes

        # Should have execution_type, trigger_type, user_id, and workspace_id
        assert len(pairs) == 4

        # Verify trigger type is SCHEDULED
        trigger_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        )
        assert trigger_pair.value == TriggerType.SCHEDULED.value

        # Verify workspace ID is set correctly
        workspace_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.WORKSPACE_ID.value
        )
        assert workspace_pair.value == str(mock_role_with_workspace.workspace_id)

    async def test_webhook_trigger_sets_correct_attributes(
        self,
        mock_client: Mock,
        mock_role_with_workspace: Role,
        mock_dsl: DSLInput,
        test_workflow_id: WorkflowID,
    ) -> None:
        """Test that webhook trigger sets correct search attributes."""
        service = WorkflowExecutionsService(
            client=mock_client, role=mock_role_with_workspace
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
                trigger_type=TriggerType.WEBHOOK,
            )

        assert captured_search_attrs is not None
        pairs = captured_search_attrs.search_attributes

        # Verify trigger type is WEBHOOK
        trigger_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        )
        assert trigger_pair.value == TriggerType.WEBHOOK.value

        # Verify workspace ID is set correctly
        workspace_pair = next(
            p
            for p in pairs  # pyright: ignore[reportGeneralTypeIssues]
            if p.key.name == TemporalSearchAttr.WORKSPACE_ID.value
        )
        assert workspace_pair.value == str(mock_role_with_workspace.workspace_id)

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

        # Should have trigger_type and workspace_id
        assert len(pairs) == 2

        # Verify trigger type is SCHEDULED
        trigger_pair = next(
            p for p in pairs if p.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        )
        assert trigger_pair.value == TriggerType.SCHEDULED.value

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

        # Should only have trigger_type (no workspace_id)
        assert len(pairs) == 1

        # Verify only trigger type is set
        assert pairs[0].key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        assert pairs[0].value == TriggerType.SCHEDULED.value


@pytest.mark.anyio
class TestSearchAttributeQueries:
    """Test that search attributes can be properly queried."""

    async def test_query_by_workspace_id_in_build_query(
        self, mock_role_with_workspace: Role, test_workflow_id: WorkflowID
    ) -> None:
        """Test that build_query constructs queries with search attributes."""
        query = build_query(
            workflow_id=test_workflow_id,
            trigger_types={TriggerType.MANUAL},
            triggered_by_user_id=mock_role_with_workspace.user_id,
            workspace_id=str(mock_role_with_workspace.workspace_id),
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
        query = build_query(workflow_id=test_workflow_id)

        # Verify query includes workflow ID
        assert test_workflow_id.short() in query


@pytest.mark.anyio
class TestSearchAttributeEnumMethods:
    """Test the helper methods on search attribute enums."""

    async def test_trigger_type_to_search_attr_pair(self) -> None:
        """Test TriggerType.to_temporal_search_attr_pair() method."""
        # Test MANUAL trigger
        manual_pair = TriggerType.MANUAL.to_temporal_search_attr_pair()
        assert manual_pair.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        assert manual_pair.value == TriggerType.MANUAL.value

        # Test SCHEDULED trigger
        scheduled_pair = TriggerType.SCHEDULED.to_temporal_search_attr_pair()
        assert scheduled_pair.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        assert scheduled_pair.value == TriggerType.SCHEDULED.value

        # Test WEBHOOK trigger
        webhook_pair = TriggerType.WEBHOOK.to_temporal_search_attr_pair()
        assert webhook_pair.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        assert webhook_pair.value == TriggerType.WEBHOOK.value

    async def test_temporal_search_attr_create_pair(self) -> None:
        """Test TemporalSearchAttr.create_pair() method."""
        # Test TRIGGER_TYPE
        trigger_pair = TemporalSearchAttr.TRIGGER_TYPE.create_pair("manual")
        assert trigger_pair.key.name == TemporalSearchAttr.TRIGGER_TYPE.value
        assert trigger_pair.value == "manual"

        # Test WORKSPACE_ID
        workspace_pair = TemporalSearchAttr.WORKSPACE_ID.create_pair("ws-123")
        assert workspace_pair.key.name == TemporalSearchAttr.WORKSPACE_ID.value
        assert workspace_pair.value == "ws-123"

        # Test TRIGGERED_BY_USER_ID
        user_pair = TemporalSearchAttr.TRIGGERED_BY_USER_ID.create_pair("user-456")
        assert user_pair.key.name == TemporalSearchAttr.TRIGGERED_BY_USER_ID.value
        assert user_pair.value == "user-456"

    async def test_temporal_search_attr_key_property(self) -> None:
        """Test that TemporalSearchAttr.key property returns SearchAttributeKey."""
        from temporalio.common import SearchAttributeKey

        # Test that key property returns correct type
        trigger_key = TemporalSearchAttr.TRIGGER_TYPE.key
        assert isinstance(trigger_key, SearchAttributeKey)
        assert trigger_key.name == TemporalSearchAttr.TRIGGER_TYPE.value

        workspace_key = TemporalSearchAttr.WORKSPACE_ID.key
        assert isinstance(workspace_key, SearchAttributeKey)
        assert workspace_key.name == TemporalSearchAttr.WORKSPACE_ID.value

        user_key = TemporalSearchAttr.TRIGGERED_BY_USER_ID.key
        assert isinstance(user_key, SearchAttributeKey)
        assert user_key.name == TemporalSearchAttr.TRIGGERED_BY_USER_ID.value
