"""Tests for the Workflows SDK client.

These tests use mocking and don't require database or other infrastructure.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tracecat_registry.sdk.workflows import (
    DEFAULT_MAX_WAIT_TIME,
    DEFAULT_POLL_INTERVAL,
    TERMINAL_STATUSES,
    WorkflowsClient,
)


@pytest.fixture
def mock_tracecat_client() -> MagicMock:
    """Create a mock TracecatClient."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    return client


@pytest.fixture
def workflows_client(mock_tracecat_client: MagicMock) -> WorkflowsClient:
    """Create a WorkflowsClient with mocked HTTP client."""
    return WorkflowsClient(mock_tracecat_client)


class TestWorkflowsClientExecute:
    """Tests for WorkflowsClient.execute()."""

    @pytest.mark.anyio
    async def test_execute_detach_with_alias(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test execute with detach strategy using alias."""
        mock_tracecat_client.post.return_value = {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "message": "Workflow execution started",
        }

        result = await workflows_client.execute(
            workflow_alias="my-workflow",
            trigger_inputs={"key": "value"},
            wait_strategy="detach",
        )

        assert result == {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "STARTED",
        }
        mock_tracecat_client.post.assert_called_once_with(
            "/workflows/executions",
            json={
                "workflow_alias": "my-workflow",
                "trigger_inputs": {"key": "value"},
            },
        )

    @pytest.mark.anyio
    async def test_execute_detach_with_id(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test execute with detach strategy using workflow ID."""
        mock_tracecat_client.post.return_value = {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "message": "Workflow execution started",
        }

        result = await workflows_client.execute(
            workflow_id="wf-00000000000000000000000000000123",
            wait_strategy="detach",
        )

        assert result["workflow_id"] == "wf-00000000000000000000000000000123"
        assert result["status"] == "STARTED"

    @pytest.mark.anyio
    async def test_execute_requires_id_or_alias(
        self, workflows_client: WorkflowsClient
    ):
        """Test that execute requires either workflow_id or workflow_alias."""
        with pytest.raises(ValueError, match="Either workflow_id or workflow_alias"):
            await workflows_client.execute(wait_strategy="detach")

    @pytest.mark.anyio
    async def test_execute_wait_success(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test execute with wait strategy - successful completion."""
        mock_tracecat_client.post.return_value = {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "message": "Workflow execution started",
        }
        mock_tracecat_client.get.return_value = {
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "COMPLETED",
            "result": {"output": "success"},
        }

        result = await workflows_client.execute(
            workflow_alias="my-workflow",
            wait_strategy="wait",
        )

        assert result == {"output": "success"}

    @pytest.mark.anyio
    async def test_execute_wait_raises_on_failure(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test execute with wait strategy raises on workflow failure."""
        mock_tracecat_client.post.return_value = {
            "workflow_id": "wf-00000000000000000000000000000123",
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "message": "Workflow execution started",
        }
        mock_tracecat_client.get.return_value = {
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "FAILED",
        }

        # Catch by base Exception class to avoid class identity issues with module reloads
        # in pytest-xdist parallel execution, then verify the exception type by name
        with pytest.raises(Exception) as exc_info:
            await workflows_client.execute(
                workflow_alias="my-workflow",
                wait_strategy="wait",
            )

        # Verify it's the right exception type and has expected attributes
        # Use Any to allow attribute access without importing the class (avoids class identity issues)
        exc: Any = exc_info.value
        assert type(exc).__name__ == "WorkflowExecutionError"
        assert exc.status == "FAILED"
        assert (
            exc.workflow_execution_id == "wf-00000000000000000000000000000123/exec-456"
        )


class TestWorkflowsClientGetStatus:
    """Tests for WorkflowsClient.get_status()."""

    @pytest.mark.anyio
    async def test_get_status(
        self, workflows_client: WorkflowsClient, mock_tracecat_client: MagicMock
    ):
        """Test get_status returns execution status."""
        mock_tracecat_client.get.return_value = {
            "workflow_execution_id": "wf-00000000000000000000000000000123/exec-456",
            "status": "RUNNING",
            "start_time": "2024-01-01T00:00:00Z",
            "close_time": None,
            "result": None,
        }

        result = await workflows_client.get_status(
            "wf-00000000000000000000000000000123/exec-456"
        )

        assert result["status"] == "RUNNING"
        mock_tracecat_client.get.assert_called_once_with(
            "/workflows/executions/wf-00000000000000000000000000000123/exec-456"
        )


class TestTerminalStatuses:
    """Tests for terminal status handling."""

    def test_terminal_statuses_are_frozen(self):
        """Test that terminal statuses cannot be modified."""
        assert isinstance(TERMINAL_STATUSES, frozenset)

    def test_expected_terminal_statuses(self):
        """Test that expected statuses are terminal."""
        expected = {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
        assert TERMINAL_STATUSES == expected


class TestDefaultConstants:
    """Tests for default configuration constants."""

    def test_default_poll_interval(self):
        """Test default poll interval is reasonable."""
        assert DEFAULT_POLL_INTERVAL == 2.0

    def test_default_max_wait_time(self):
        """Test default max wait time is 5 minutes."""
        assert DEFAULT_MAX_WAIT_TIME == 300.0
