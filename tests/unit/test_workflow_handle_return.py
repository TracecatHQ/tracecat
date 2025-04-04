from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.dsl.common import DSLInput
from tracecat.dsl.models import ExecutionContext
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.store.models import ObjectRef, StoreWorkflowResultActivityInput
from tracecat.ee.store.object_store import ObjectStore
from tracecat.expressions.common import ExprContext


class MockLogger:
    """Mock logger for testing."""

    def debug(self, *args, **kwargs):
        pass

    def trace(self, *args, **kwargs):
        pass


@pytest.mark.anyio
async def test_handle_return_null_returns():
    """Test _handle_return when dsl.returns is None.

    Should return the context with ENV removed.
    """
    # Arrange
    workflow_instance = MagicMock(spec=DSLWorkflow)
    workflow_instance.dsl = MagicMock(spec=DSLInput)
    workflow_instance.dsl.returns = None
    workflow_instance.context = ExecutionContext(
        {
            ExprContext.ACTIONS: {"action1": {"result": "value1"}},
            ExprContext.ENV: {"env1": "value1"},
        }
    )
    # Add mock logger
    workflow_instance.logger = MockLogger()

    expected_result = ExecutionContext(
        {ExprContext.ACTIONS: {"action1": {"result": "value1"}}}
    )

    # Act
    result = await DSLWorkflow._handle_return(workflow_instance)

    # Assert
    assert result == expected_result
    assert ExprContext.ENV not in result


@pytest.mark.anyio
async def test_handle_return_with_object_store():
    """Test _handle_return when TRACECAT__USE_OBJECT_STORE is True.

    Should execute the store_workflow_result_activity.
    """
    # Arrange
    workflow_instance = MagicMock(spec=DSLWorkflow)
    workflow_instance.dsl = MagicMock(spec=DSLInput)
    workflow_instance.dsl.returns = {"test_key": "${{ ACTIONS.action1.result }}"}
    workflow_instance.context = ExecutionContext(
        {ExprContext.ACTIONS: {"action1": {"result": "value1"}}}
    )
    workflow_instance.start_to_close_timeout = 30
    # Add mock logger
    workflow_instance.logger = MockLogger()

    mock_object_ref = ObjectRef(
        key="test-key",
        size=100,
        digest="test-digest",
        metadata={"encoding": "json/plain"},
    )

    # Set up patches
    with (
        patch("tracecat.config.TRACECAT__USE_OBJECT_STORE", True),
        patch(
            "temporalio.workflow.execute_activity", new_callable=AsyncMock
        ) as mock_execute_activity,
    ):
        # Configure mock
        mock_execute_activity.return_value = mock_object_ref

        # Act
        result = await DSLWorkflow._handle_return(workflow_instance)

        # Assert
        assert result == mock_object_ref

        # Verify that execute_activity was called with the correct arguments
        mock_execute_activity.assert_awaited_once()

        # Verify the activity function and arguments
        call_args = mock_execute_activity.await_args
        # First argument should be the activity function
        assert call_args is not None
        assert call_args[0][0] == ObjectStore.store_workflow_result_activity

        # Check input is correct
        activity_input = call_args[1]["arg"]
        assert isinstance(activity_input, StoreWorkflowResultActivityInput)
        assert activity_input.args == workflow_instance.dsl.returns
        assert activity_input.context == workflow_instance.context

        # Check timeouts and retry policy
        assert (
            call_args[1]["start_to_close_timeout"]
            == workflow_instance.start_to_close_timeout
        )
        assert call_args[1]["retry_policy"].maximum_attempts == 1


@pytest.mark.anyio
async def test_handle_return_without_object_store():
    """Test _handle_return when TRACECAT__USE_OBJECT_STORE is False.

    Should directly use eval_templated_object.
    """
    # Arrange
    workflow_instance = MagicMock(spec=DSLWorkflow)
    workflow_instance.dsl = MagicMock(spec=DSLInput)
    workflow_instance.dsl.returns = {"test_key": "${{ ACTIONS.action1.result }}"}
    workflow_instance.context = ExecutionContext(
        {ExprContext.ACTIONS: {"action1": {"result": "value1"}}}
    )
    # Add mock logger
    workflow_instance.logger = MockLogger()

    expected_result = {"test_key": "value1"}

    # Set up patches
    with (
        patch("tracecat.config.TRACECAT__USE_OBJECT_STORE", False),
        patch(
            "tracecat.dsl.workflow.eval_templated_object", return_value=expected_result
        ) as mock_eval,
    ):
        # Act
        result = await DSLWorkflow._handle_return(workflow_instance)

        # Assert
        assert result == expected_result

        # Verify eval_templated_object was called with the right arguments
        mock_eval.assert_called_once_with(
            workflow_instance.dsl.returns, operand=workflow_instance.context
        )


@pytest.mark.anyio
async def test_handle_return_with_object_store_activity_detailed():
    """Test the object store code path with detailed mocking of the activity call.

    This test verifies that the activity is called with the correct parameters and
    ensures that the retry policy is set properly.
    """
    # Arrange
    workflow_instance = MagicMock(spec=DSLWorkflow)
    workflow_instance.dsl = MagicMock(spec=DSLInput)
    workflow_instance.dsl.returns = {"result_key": "${{ ACTIONS.action1.result }}"}
    workflow_instance.context = ExecutionContext(
        {ExprContext.ACTIONS: {"action1": {"result": "activity_value"}}}
    )
    workflow_instance.start_to_close_timeout = 60
    workflow_instance.logger = MockLogger()

    # Create expected ObjectRef to be returned from the activity
    expected_ref = ObjectRef(
        key="blob/test/hash123",
        size=42,
        digest="hash123",
        metadata={"encoding": "json/plain"},
    )

    # Set up patches
    with (
        patch("tracecat.config.TRACECAT__USE_OBJECT_STORE", True),
        patch(
            "temporalio.workflow.execute_activity", new_callable=AsyncMock
        ) as mock_execute_activity,
    ):
        # Configure mock to return the expected reference
        mock_execute_activity.return_value = expected_ref

        # Act
        result = await DSLWorkflow._handle_return(workflow_instance)

        # Assert
        # 1. Verify result matches expected reference
        assert result == expected_ref

        # 2. Verify workflow.execute_activity called with correct args
        mock_execute_activity.assert_awaited_once()

        # 3. Verify activity function and parameters
        call_args = mock_execute_activity.await_args
        assert call_args is not None
        activity_function = call_args[0][0]
        activity_input = call_args[1]["arg"]
        timeout = call_args[1]["start_to_close_timeout"]
        retry_policy = call_args[1]["retry_policy"]

        assert activity_function == ObjectStore.store_workflow_result_activity
        assert isinstance(activity_input, StoreWorkflowResultActivityInput)
        assert activity_input.args == workflow_instance.dsl.returns
        assert activity_input.context == workflow_instance.context
        assert timeout == 60  # Match the workflow_instance.start_to_close_timeout
        assert retry_policy.maximum_attempts == 1  # Using fail_fast policy
