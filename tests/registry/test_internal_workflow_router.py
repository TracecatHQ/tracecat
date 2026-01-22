"""Tests for the internal workflow execution router schemas.

These tests verify the request/response schemas for the internal workflow API.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.executions.internal_router import (
    InternalWorkflowExecuteRequest,
    InternalWorkflowExecuteResponse,
    InternalWorkflowStatusResponse,
)

# Mark all tests to use the db fixture (required by conftest autouse)
pytestmark = pytest.mark.usefixtures("db")


class TestInternalWorkflowExecuteRequest:
    """Tests for request schema validation."""

    def test_request_with_workflow_id(self):
        """Test request with workflow ID."""
        wf_id = WorkflowUUID.new("wf-00000000000000000000000000000123")
        req = InternalWorkflowExecuteRequest(
            workflow_id=wf_id,
            trigger_inputs={"key": "value"},
        )
        assert req.workflow_id == wf_id
        assert req.trigger_inputs == {"key": "value"}

    def test_request_with_workflow_alias(self):
        """Test request with workflow alias."""
        req = InternalWorkflowExecuteRequest(
            workflow_alias="my-workflow",
        )
        assert req.workflow_alias == "my-workflow"

    def test_request_defaults(self):
        """Test request default values."""
        req = InternalWorkflowExecuteRequest()
        assert req.workflow_id is None
        assert req.workflow_alias is None
        assert req.trigger_inputs is None
        assert req.parent_workflow_execution_id is None

    def test_request_with_all_fields(self):
        """Test request with all fields populated."""
        wf_id = WorkflowUUID.new("wf-00000000000000000000000000000123")
        req = InternalWorkflowExecuteRequest(
            workflow_id=wf_id,
            workflow_alias="my-workflow",
            trigger_inputs={"key": "value"},
            parent_workflow_execution_id="wf-00000000000000000000000000000456/exec-789",
        )
        assert req.workflow_id == wf_id
        assert req.workflow_alias == "my-workflow"
        assert req.trigger_inputs == {"key": "value"}
        assert (
            req.parent_workflow_execution_id
            == "wf-00000000000000000000000000000456/exec-789"
        )


class TestInternalWorkflowExecuteResponse:
    """Tests for response schema."""

    def test_response_creation(self):
        """Test response creation."""
        wf_id = WorkflowUUID.new("wf-00000000000000000000000000000123")
        resp = InternalWorkflowExecuteResponse(
            workflow_id=wf_id,
            workflow_execution_id="wf-00000000000000000000000000000123/exec-456",
            message="Workflow execution started",
        )
        assert resp.workflow_id == wf_id
        assert (
            resp.workflow_execution_id == "wf-00000000000000000000000000000123/exec-456"
        )
        assert resp.message == "Workflow execution started"

    def test_response_serialization(self):
        """Test response can be serialized to dict."""
        wf_id = WorkflowUUID.new("wf-00000000000000000000000000000123")
        resp = InternalWorkflowExecuteResponse(
            workflow_id=wf_id,
            workflow_execution_id="wf-00000000000000000000000000000123/exec-456",
            message="Workflow execution started",
        )
        data = resp.model_dump()
        # model_dump() keeps WorkflowUUID as object (serialization only for JSON mode)
        assert data["workflow_id"] == wf_id
        assert (
            data["workflow_execution_id"]
            == "wf-00000000000000000000000000000123/exec-456"
        )
        assert data["message"] == "Workflow execution started"


class TestInternalWorkflowStatusResponse:
    """Tests for status response schema."""

    def test_status_response_running(self):
        """Test status response for running workflow."""
        resp = InternalWorkflowStatusResponse(
            workflow_execution_id="wf-00000000000000000000000000000123/exec-456",
            status="RUNNING",
            start_time=datetime.now(UTC),
        )
        assert resp.status == "RUNNING"
        assert resp.close_time is None
        assert resp.result is None

    def test_status_response_completed(self):
        """Test status response for completed workflow."""
        start = datetime.now(UTC)
        close = datetime.now(UTC)
        resp = InternalWorkflowStatusResponse(
            workflow_execution_id="wf-00000000000000000000000000000123/exec-456",
            status="COMPLETED",
            start_time=start,
            close_time=close,
            result={"output": "success"},
        )
        assert resp.status == "COMPLETED"
        assert resp.result == {"output": "success"}
        assert resp.start_time == start
        assert resp.close_time == close

    def test_status_response_failed(self):
        """Test status response for failed workflow."""
        resp = InternalWorkflowStatusResponse(
            workflow_execution_id="wf-00000000000000000000000000000123/exec-456",
            status="FAILED",
            start_time=datetime.now(UTC),
            close_time=datetime.now(UTC),
        )
        assert resp.status == "FAILED"
        assert resp.result is None

    def test_status_response_all_statuses(self):
        """Test that all expected statuses are valid."""
        statuses = [
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "CANCELED",
            "TERMINATED",
            "TIMED_OUT",
            "CONTINUED_AS_NEW",
            "UNKNOWN",
        ]
        for status in statuses:
            resp = InternalWorkflowStatusResponse(
                workflow_execution_id="wf-00000000000000000000000000000123/exec-456",
                status=status,
            )
            assert resp.status == status

    def test_status_response_serialization(self):
        """Test status response can be serialized to dict."""
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        resp = InternalWorkflowStatusResponse(
            workflow_execution_id="wf-00000000000000000000000000000123/exec-456",
            status="COMPLETED",
            start_time=start,
            result={"output": "success"},
        )
        data = resp.model_dump()
        assert (
            data["workflow_execution_id"]
            == "wf-00000000000000000000000000000123/exec-456"
        )
        assert data["status"] == "COMPLETED"
        assert data["result"] == {"output": "success"}
