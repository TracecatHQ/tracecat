"""Tests for ExecutorActivities.

These tests cover the Temporal activity that handles action execution.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tests.shared import to_data
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.dsl.types import ActionErrorInfo
from tracecat.exceptions import EntitlementRequired, ExecutionError, LoopExecutionError
from tracecat.executor.activities import ExecutorActivities
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.fixture
def mock_run_action_input() -> RunActionInput:
    """Create a mock RunActionInput for testing."""
    wf_id = WorkflowUUID.new_uuid4()
    action_name = "core.http_request"
    return RunActionInput(
        task=ActionStatement(
            action=action_name,
            args={"url": "https://example.com"},
            ref="test_action",
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={action_name: "tracecat_registry"},
        ),
    )


class TestExecutorActivities:
    """Tests for ExecutorActivities class."""

    def test_cannot_instantiate(self):
        """Test that ExecutorActivities cannot be instantiated."""
        with pytest.raises(RuntimeError, match="should not be instantiated"):
            ExecutorActivities()

    def test_get_activities_returns_list(self):
        """Test that get_activities returns a list of activity functions."""
        activities = ExecutorActivities.get_activities()
        assert isinstance(activities, list)
        assert len(activities) > 0

        # All returned items should have the temporal activity definition
        for activity in activities:
            assert hasattr(activity, "__temporal_activity_definition")

    def test_execute_action_activity_exists(self):
        """Test that execute_action_activity is included in activities."""
        activities = ExecutorActivities.get_activities()
        activity_names = [
            getattr(a, "__temporal_activity_definition").name for a in activities
        ]
        assert "execute_action_activity" in activity_names


class TestExecuteActionActivity:
    """Tests for the execute_action_activity function."""

    @pytest.mark.anyio
    async def test_successful_execution(self, mock_run_action_input, mock_role):
        """Test successful action execution."""
        expected_result = {"status": "success", "data": [1, 2, 3]}

        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
            patch(
                "tracecat.executor.activities.materialize_context",
                new_callable=AsyncMock,
            ) as mock_materialize,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.return_value = expected_result
            # Return the same exec_context to preserve the input
            mock_materialize.return_value = mock_run_action_input.exec_context

            result = await ExecutorActivities.execute_action_activity(
                mock_run_action_input, mock_role
            )

            # Result is wrapped in StoredObject (InlineObject for small payloads)
            assert await to_data(result) == expected_result
            mock_dispatch.assert_called_once_with(
                backend=mock_backend.return_value, input=mock_run_action_input
            )

    @pytest.mark.anyio
    async def test_execution_error_raises_application_error(
        self, mock_run_action_input, mock_role
    ):
        """Test that ExecutionError is converted to ApplicationError."""
        error_info = ExecutorActionErrorInfo(
            type="ValueError",
            message="Invalid argument",
            action_name="test_action",
            filename="<test>",
            function="test_function",
        )
        exec_error = ExecutionError(info=error_info)

        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.side_effect = exec_error

            with pytest.raises(ApplicationError) as exc_info:
                await ExecutorActivities.execute_action_activity(
                    mock_run_action_input, mock_role
                )

            app_error = exc_info.value
            assert app_error.type == "ExecutionError"
            # Check that the error info is in the details
            assert len(app_error.details) > 0

    @pytest.mark.anyio
    async def test_loop_execution_error_raises_application_error(
        self, mock_run_action_input, mock_role
    ):
        """Test that LoopExecutionError is converted to ApplicationError."""
        # Create mock loop errors
        error_info = ExecutorActionErrorInfo(
            type="ValueError",
            message="Loop iteration failed",
            action_name="test_action",
            filename="<test>",
            function="test_function",
            loop_iteration=0,
        )
        loop_errors = [ExecutionError(info=error_info)]
        loop_error = LoopExecutionError(loop_errors)

        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.side_effect = loop_error

            with pytest.raises(ApplicationError) as exc_info:
                await ExecutorActivities.execute_action_activity(
                    mock_run_action_input, mock_role
                )

            app_error = exc_info.value
            assert app_error.type == "LoopExecutionError"

    @pytest.mark.anyio
    async def test_unexpected_error_is_non_retryable(
        self, mock_run_action_input, mock_role
    ):
        """Test that unexpected errors are marked as non-retryable."""
        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.side_effect = RuntimeError("Unexpected crash")

            with pytest.raises(ApplicationError) as exc_info:
                await ExecutorActivities.execute_action_activity(
                    mock_run_action_input, mock_role
                )

            app_error = exc_info.value
            assert app_error.type == "RuntimeError"
            assert app_error.non_retryable is True

    @pytest.mark.anyio
    async def test_entitlement_error_raises_non_retryable_application_error(
        self, mock_run_action_input, mock_role
    ):
        """Test that EntitlementRequired is converted to non-retryable ApplicationError."""
        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.side_effect = EntitlementRequired("custom_registry")

            with pytest.raises(ApplicationError) as exc_info:
                await ExecutorActivities.execute_action_activity(
                    mock_run_action_input, mock_role
                )

            app_error = exc_info.value
            assert app_error.type == "EntitlementRequired"
            assert app_error.non_retryable is True
            assert len(app_error.details) > 0
            detail = app_error.details[0]
            assert isinstance(detail, ActionErrorInfo)
            assert "custom_registry" in detail.message

    @pytest.mark.anyio
    async def test_application_error_passthrough(
        self, mock_run_action_input, mock_role
    ):
        """Test that ApplicationError is passed through with proper wrapping."""
        original_error = ApplicationError("Original error", type="CustomError")

        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.side_effect = original_error

            with pytest.raises(ApplicationError) as exc_info:
                await ExecutorActivities.execute_action_activity(
                    mock_run_action_input, mock_role
                )

            app_error = exc_info.value
            assert app_error.type == "CustomError"

    @pytest.mark.anyio
    async def test_context_variables_are_set(self, mock_run_action_input, mock_role):
        """Test that context variables (ctx_run, ctx_role) are set."""
        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
            patch("tracecat.executor.activities.ctx_run") as mock_ctx_run,
            patch("tracecat.executor.activities.ctx_role") as mock_ctx_role,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.return_value = {"result": "ok"}

            await ExecutorActivities.execute_action_activity(
                mock_run_action_input, mock_role
            )

            mock_ctx_run.set.assert_called_once_with(mock_run_action_input.run_context)
            mock_ctx_role.set.assert_called_once_with(mock_role)

    @pytest.mark.anyio
    async def test_error_info_includes_stream_id(
        self, mock_run_action_input, mock_role
    ):
        """Test that error info includes stream_id from input."""
        # Add stream_id to input
        mock_run_action_input.stream_id = "test-stream-123"

        error_info = ExecutorActionErrorInfo(
            type="ValueError",
            message="Test error",
            action_name="test_action",
            filename="<test>",
            function="test_function",
        )
        exec_error = ExecutionError(info=error_info)

        with (
            patch("tracecat.executor.activities.activity") as mock_activity,
            patch("tracecat.executor.activities.get_executor_backend") as mock_backend,
            patch(
                "tracecat.executor.activities.dispatch_action",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            mock_activity.info.return_value = MagicMock(attempt=1)
            mock_backend.return_value = MagicMock()
            mock_dispatch.side_effect = exec_error

            with pytest.raises(ApplicationError) as exc_info:
                await ExecutorActivities.execute_action_activity(
                    mock_run_action_input, mock_role
                )

            # The ActionErrorInfo in details should have the stream_id
            app_error = exc_info.value
            assert len(app_error.details) > 0
            action_error_info = app_error.details[0]
            assert isinstance(action_error_info, ActionErrorInfo)
            assert action_error_info.stream_id == "test-stream-123"
