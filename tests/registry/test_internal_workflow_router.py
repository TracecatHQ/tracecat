"""Tests for the internal workflow execution router schemas.

These tests verify the request/response schemas for the internal workflow API.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from starlette.status import HTTP_400_BAD_REQUEST

from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.mcp.schemas import JsonPatchOperation
from tracecat.workflow.executions import internal_router
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


class TestEditWorkflowDocument:
    """Tests for the internal edit-document route."""

    @pytest.mark.anyio
    async def test_validate_only_reports_stale_no_op(self, monkeypatch):
        workflow_id = uuid.uuid4()
        workflow = SimpleNamespace(
            id=workflow_id,
            title="Example workflow",
            description="Example description",
            status="offline",
            version=None,
            alias=None,
            entrypoint=None,
            error_handler=None,
            expects={},
            returns=None,
            config={},
            actions=[],
            schedules=[],
            case_trigger=None,
            graph_version=1,
            trigger_position_x=0.0,
            trigger_position_y=0.0,
            viewport_x=0.0,
            viewport_y=0.0,
            viewport_zoom=1.0,
        )

        class _WorkflowService:
            def __init__(self, session: object, role: object) -> None:
                self.session = session
                self.role = role

            async def get_workflow(
                self, wf_id: object, *, for_update: bool = False
            ) -> object:
                assert wf_id == workflow_id
                assert for_update is True
                return workflow

        monkeypatch.setattr(
            internal_router,
            "WorkflowsManagementService",
            _WorkflowService,
        )
        raw_edit_workflow_document = cast(
            Any,
            internal_router.edit_workflow_document,
        ).__wrapped__

        response = await raw_edit_workflow_document(
            role=SimpleNamespace(),
            session=object(),
            workflow_id=workflow_id,
            params=InternalWorkflowEditRequest(
                base_revision="stale-revision",
                patch_ops=[
                    JsonPatchOperation(
                        op="replace",
                        path="/metadata/title",
                        value=workflow.title,
                    )
                ],
                validate_only=True,
            ),
        )
        payload = response.model_dump(mode="json")

        assert payload["valid"] is True
        assert payload["validate_only"] is True
        assert payload["changed_sections"] == []
        assert payload["no_op"] is True
        assert payload["rebased"] is True


class TestBuildImportDataFromDefinitionYaml:
    """Tests for the copilot YAML -> import-data normalization."""

    def test_wraps_bare_definition(self):
        # A bare workflow (no top-level `definition:` key) is enveloped.
        data = _build_import_data_from_definition_yaml(
            definition_yaml=(
                "title: Bare WF\n"
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
        assert data["definition"]["title"] == "Bare WF"

    def test_injects_missing_entrypoint(self):
        # Missing entrypoint is defaulted so DSLInput validation passes.
        data = _build_import_data_from_definition_yaml(
            definition_yaml=(
                "definition:\n"
                "  title: Inferred entrypoint\n"
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

    def test_defaults_missing_description_to_empty_string(self):
        # When neither the YAML nor the caller supplies a description, default
        # it to "" so the required DSLInput.description field is satisfied and
        # the create call doesn't 400 on a missing field.
        yaml_text = (
            "definition:\n"
            "  title: No description\n"
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
            title=None,
            description=None,
        )
        assert data["definition"]["description"] == ""

    def test_does_not_override_existing_description(self):
        # A description already present in the YAML is preserved over the caller's.
        yaml_text = "definition:\n  title: Keep description\n  description: from_yaml\n"
        data = _build_import_data_from_definition_yaml(
            definition_yaml=yaml_text,
            title=None,
            description="from_caller",
        )
        assert data["definition"]["description"] == "from_yaml"

    @pytest.mark.parametrize("depends_on", ["1", "[1]", "[true]"])
    def test_rejects_non_string_depends_on(self, depends_on: str):
        # A scalar or non-string-list depends_on would make auto_generate_layout
        # raise a raw TypeError/AttributeError that escapes the 400-mapping in
        # create_workflow (-> 500). Reject the correctable input here as a 400.
        yaml_text = (
            "definition:\n"
            "  title: T\n"
            "  entrypoint:\n"
            "    ref: start\n"
            "  actions:\n"
            "    - ref: start\n"
            "      action: core.transform.reshape\n"
            f"      depends_on: {depends_on}\n"
            "      args:\n"
            "        value: hi\n"
        )
        with pytest.raises(HTTPException) as exc:
            _build_import_data_from_definition_yaml(
                definition_yaml=yaml_text, title=None, description=None
            )
        assert exc.value.status_code == HTTP_400_BAD_REQUEST

    def test_rejects_too_short_title(self):
        # A 1-2 char title imports via DSLInput but later breaks the edit
        # endpoints (WorkflowEditMetadata requires min_length=3). Reject it now.
        with pytest.raises(HTTPException) as exc:
            _build_import_data_from_definition_yaml(
                definition_yaml="definition:\n  title: T\n",
                title=None,
                description=None,
            )
        assert exc.value.status_code == HTTP_400_BAD_REQUEST

    def test_rejects_oversized_description(self):
        # A >1000 char description imports but breaks the edit endpoints
        # (WorkflowEditMetadata requires max_length=1000). Reject it now.
        with pytest.raises(HTTPException) as exc:
            _build_import_data_from_definition_yaml(
                definition_yaml="definition:\n  title: Valid Title\n",
                title=None,
                description="x" * 1001,
            )
        assert exc.value.status_code == HTTP_400_BAD_REQUEST


class TestRaiseWorkflowEditHttpError:
    """Tests mapping the engine error onto HTTP responses."""

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


class TestInternalRouterCoversSdkPaths:
    """The internal router must expose every path the workflows SDK calls.

    ``TracecatClient`` normalizes the SDK base URL under ``/internal``, so any
    SDK method whose path is missing from the internal router 404s when a
    registry action invokes it. These assertions lock the webhook and
    case-trigger routes (added alongside the chat tools that drive them) into
    the router so the SDK<->route contract can't silently drift.
    """

    def _routes(self) -> set[tuple[str, str]]:
        from tracecat.workflow.executions.internal_router import router

        routes: set[tuple[str, str]] = set()
        for route in router.routes:
            for method in getattr(route, "methods", None) or []:
                routes.add((method, getattr(route, "path", "")))
        return routes

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/internal/workflows/{workflow_id}/webhook"),
            ("PATCH", "/internal/workflows/{workflow_id}/webhook"),
            ("GET", "/internal/workflows/{workflow_id}/case-trigger"),
            ("PATCH", "/internal/workflows/{workflow_id}/case-trigger"),
        ],
    )
    def test_webhook_and_case_trigger_routes_registered(
        self, method: str, path: str
    ) -> None:
        assert (method, path) in self._routes()


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


class TestInternalRouterPublishRoute:
    """The publish route must be registered so the SDK's publish() (which
    resolves under /internal) reaches it instead of 404ing."""

    def test_publish_route_registered(self):
        from tracecat.workflow.executions.internal_router import router

        routes = {
            (method, getattr(route, "path", ""))
            for route in router.routes
            for method in getattr(route, "methods", None) or []
        }
        assert ("POST", "/internal/workflows/{workflow_id}/publish") in routes
