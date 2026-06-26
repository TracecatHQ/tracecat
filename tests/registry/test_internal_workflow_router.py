"""Tests for the internal workflow execution router schemas.

These tests verify the request/response schemas for the internal workflow API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import HTTPException
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_409_CONFLICT

from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.mcp.schemas import JsonPatchOperation
from tracecat.workflow.executions.internal_router import (
    InternalWorkflowCreateRequest,
    InternalWorkflowEditRequest,
    InternalWorkflowExecuteRequest,
    InternalWorkflowExecuteResponse,
    InternalWorkflowStatusResponse,
    _build_import_data_from_definition_yaml,
    _raise_workflow_edit_http_error,
)
from tracecat.workflow.management.draft import WorkflowEditError

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


class TestInternalWorkflowCreateRequest:
    """Tests for the create request, including the optional definition_yaml."""

    def test_create_defaults(self):
        req = InternalWorkflowCreateRequest()
        assert req.title is None
        assert req.description is None
        assert req.definition_yaml is None

    def test_create_with_definition_yaml(self):
        req = InternalWorkflowCreateRequest(definition_yaml="definition:\n  title: T\n")
        assert req.definition_yaml == "definition:\n  title: T\n"

    def test_invalid_dsl_raises_raw_tracecat_error_not_pydantic(self):
        """DSLInput validators raise raw TracecatDSLError, not ValidationError.

        Why the create handler must catch TracecatValidationError (else 500).
        """
        from pydantic import ValidationError as PydanticValidationError

        from tracecat.exceptions import TracecatDSLError
        from tracecat.workflow.management.schemas import ExternalWorkflowDefinition

        import_data = {
            "definition": {
                "title": "T",
                "description": "D",
                "entrypoint": {"ref": None},
                "actions": [
                    {"ref": "dup", "action": "core.transform.reshape", "args": {}},
                    {"ref": "dup", "action": "core.transform.reshape", "args": {}},
                ],
            }
        }
        with pytest.raises(TracecatDSLError) as exc:
            ExternalWorkflowDefinition.model_validate(import_data)
        assert not isinstance(exc.value, PydanticValidationError)


class TestInternalWorkflowEditRequest:
    """Tests for the edit-document request schema."""

    def test_edit_request_defaults_validate_only_false(self):
        req = InternalWorkflowEditRequest(
            base_revision="rev1",
            patch_ops=[JsonPatchOperation(op="add", path="/metadata/title", value="X")],
        )
        assert req.base_revision == "rev1"
        assert req.validate_only is False
        assert req.patch_ops[0].op == "add"


class TestBuildImportDataFromDefinitionYaml:
    """Tests for the copilot YAML -> import-data normalization."""

    def test_wraps_bare_definition(self):
        # A bare workflow (no top-level `definition:` key) is enveloped.
        data = _build_import_data_from_definition_yaml(
            definition_yaml=(
                "title: T\n"
                "entrypoint:\n"
                "  ref: start\n"
                "actions:\n"
                "  - ref: start\n"
                "    action: core.transform.reshape\n"
                "    args:\n"
                "      value: hi\n"
            ),
            title=None,
            description=None,
        )
        assert "definition" in data
        assert data["definition"]["title"] == "T"

    def test_injects_missing_entrypoint(self):
        # Missing entrypoint is defaulted so DSLInput validation passes.
        data = _build_import_data_from_definition_yaml(
            definition_yaml=(
                "definition:\n"
                "  title: T\n"
                "  actions:\n"
                "    - ref: start\n"
                "      action: core.transform.reshape\n"
                "      args:\n"
                "        value: hi\n"
            ),
            title=None,
            description=None,
        )
        assert data["definition"]["entrypoint"] == {"ref": None}

    def test_rejects_invalid_yaml(self):
        with pytest.raises(HTTPException) as exc:
            _build_import_data_from_definition_yaml(
                definition_yaml="::: not yaml :::\n: x",
                title=None,
                description=None,
            )
        assert exc.value.status_code == HTTP_400_BAD_REQUEST

    def test_defaults_title_and_autogenerates_layout(self):
        yaml_text = (
            "definition:\n"
            "  entrypoint:\n"
            "    ref: start\n"
            "  actions:\n"
            "    - ref: start\n"
            "      action: core.transform.reshape\n"
            "      args:\n"
            "        value: hello\n"
        )
        data = _build_import_data_from_definition_yaml(
            definition_yaml=yaml_text,
            title="My Workflow",
            description="desc",
        )
        assert data["definition"]["title"] == "My Workflow"
        assert data["definition"]["description"] == "desc"
        # Layout was auto-generated because none was supplied.
        assert "layout" in data


class TestRaiseWorkflowEditHttpError:
    """Tests mapping the engine error onto HTTP responses."""

    def test_conflict_maps_to_409(self):
        err = WorkflowEditError(
            "Draft revision mismatch", conflict=True, current_revision="rev9"
        )
        with pytest.raises(HTTPException) as exc:
            _raise_workflow_edit_http_error(err)
        assert exc.value.status_code == HTTP_409_CONFLICT
        detail = cast(dict[str, Any], exc.value.detail)
        assert detail["current_revision"] == "rev9"

    def test_validation_error_maps_to_400_with_details(self):
        err = WorkflowEditError(
            "1 validation error(s)",
            code="validation_error",
            details={"type": "validation_error", "errors": []},
        )
        with pytest.raises(HTTPException) as exc:
            _raise_workflow_edit_http_error(err)
        assert exc.value.status_code == HTTP_400_BAD_REQUEST
        assert exc.value.detail == {"type": "validation_error", "errors": []}

    def test_plain_error_maps_to_400_message(self):
        err = WorkflowEditError("Patch path '/x' is not editable via edit_workflow")
        with pytest.raises(HTTPException) as exc:
            _raise_workflow_edit_http_error(err)
        assert exc.value.status_code == HTTP_400_BAD_REQUEST
        assert "not editable" in exc.value.detail


class TestInvalidPatchApplicationRaisesToolError:
    """Bad patch application raises ToolError, not WorkflowEditError.

    Why the route needs a dedicated ``except ToolError`` (else these 500).
    """

    def test_replace_missing_path_raises_tool_error(self):
        from fastmcp.exceptions import ToolError

        from tracecat.mcp.json_patch import apply_json_patch_operations
        from tracecat.mcp.schemas import JsonPatchOperation

        with pytest.raises(ToolError) as exc:
            apply_json_patch_operations(
                document={"metadata": {"title": "T"}},
                patch_ops=[
                    JsonPatchOperation(op="replace", path="/metadata/nope", value=1)
                ],
            )
        assert not isinstance(exc.value, WorkflowEditError)

    def test_array_index_out_of_range_raises_tool_error(self):
        from fastmcp.exceptions import ToolError

        from tracecat.mcp.json_patch import apply_json_patch_operations
        from tracecat.mcp.schemas import JsonPatchOperation

        with pytest.raises(ToolError):
            apply_json_patch_operations(
                document={"items": []},
                patch_ops=[JsonPatchOperation(op="replace", path="/items/5", value=1)],
            )


class TestWorkflowAuthoringContextRequest:
    """Tests for the authoring-context request schema."""

    def test_defaults_to_none(self):
        from tracecat.mcp.schemas import WorkflowAuthoringContextRequest

        req = WorkflowAuthoringContextRequest()
        assert req.action_names is None
        assert req.query is None

    def test_accepts_action_names_and_query(self):
        from tracecat.mcp.schemas import WorkflowAuthoringContextRequest

        req = WorkflowAuthoringContextRequest(
            action_names=["core.http_request", "ai.agent"],
            query="reshape",
        )
        assert req.action_names == ["core.http_request", "ai.agent"]
        assert req.query == "reshape"
