from __future__ import annotations

import asyncio
import base64
import json
import sys
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import CallToolRequestParams
from tracecat_registry import RegistrySecret

import tracecat.mcp.auth as mcp_auth
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.preset.schemas import AgentPresetRead
from tracecat.agent.skill.schemas import SkillRead, SkillUploadFile
from tracecat.agent.stream.events import StreamDelta, StreamEnd
from tracecat.exceptions import BuiltinRegistryHasNoSelectionError
from tracecat.expressions.common import ExprType
from tracecat.integrations.enums import MCPAuthType, OAuthGrantType
from tracecat.tables.service import TablesService
from tracecat.validation.schemas import (
    ValidationDetail,
    ValidationResult,
    ValidationResultType,
)

_original_create_mcp_auth = mcp_auth.create_mcp_auth
try:
    mcp_auth.create_mcp_auth = lambda: None
    from tracecat.mcp import server as mcp_server  # noqa: E402
finally:
    mcp_auth.create_mcp_auth = _original_create_mcp_auth


def _tool(fn: Any) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Cast a FastMCP-decorated tool back to a callable for test invocation.

    ``@mcp.tool()`` is typed as returning ``FunctionTool`` (not callable),
    but at runtime it returns the original async function.
    """
    return cast(Callable[..., Coroutine[Any, Any, Any]], fn)


def _payload(result: Any) -> Any:
    """Normalize tool output into plain Python data for assertions."""
    if isinstance(result, str):
        return json.loads(result)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, list):
        return [_payload(item) for item in result]
    if isinstance(result, dict):
        return {key: _payload(value) for key, value in result.items()}
    return result


class _AsyncContext:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _build_preset_read(preset: Any) -> AgentPresetRead:
    data = dict(preset) if isinstance(preset, dict) else dict(vars(preset))
    data.setdefault("skills", [])
    return AgentPresetRead.model_validate(data)


class _PresetReadBuilder:
    async def build_preset_read(self, preset: Any) -> AgentPresetRead:
        return _build_preset_read(preset)


class _FakeRedis:
    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        _ = ex
        self.storage[key] = value

    async def get(self, key: str) -> bytes | None:
        return self.storage.get(key)


def _fake_ctx(
    *,
    session_id: str = "test-session",
    transport: str = "streamable-http",
) -> Any:
    return SimpleNamespace(session_id=session_id, transport=transport)


def _workflow_stub(**overrides: Any) -> SimpleNamespace:
    data: dict[str, Any] = {
        "id": uuid.uuid4(),
        "title": "Example workflow",
        "description": "Example description",
        "status": "offline",
        "version": None,
        "alias": None,
        "entrypoint": None,
        "error_handler": None,
        "expects": {},
        "returns": None,
        "config": {},
        "actions": [],
        "schedules": [],
        "case_trigger": None,
        "trigger_position_x": 0.0,
        "trigger_position_y": 0.0,
        "viewport_x": 0.0,
        "viewport_y": 0.0,
        "viewport_zoom": 1.0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _schedule_stub(**overrides: Any) -> SimpleNamespace:
    data: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "inputs": {},
        "cron": "0 * * * *",
        "every": None,
        "offset": None,
        "start_at": None,
        "end_at": None,
        "timeout": 0.0,
        "status": "offline",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _action_stub(**overrides: Any) -> SimpleNamespace:
    data: dict[str, Any] = {
        "id": uuid.uuid4(),
        "ref": "step_a",
        "type": "core.noop",
        "title": "Step A",
        "description": "",
        "status": "offline",
        "inputs": "{}",
        "control_flow": {},
        "is_interactive": False,
        "interaction": None,
        "upstream_edges": [],
        "position_x": 0.0,
        "position_y": 0.0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.anyio
async def test_resolve_workspace_role_rejects_invalid_workspace_id():
    with pytest.raises(ToolError, match="Invalid workspace ID"):
        await mcp_server._resolve_workspace_role("not-a-uuid")


@pytest.mark.anyio
async def test_resolve_workspace_role_surfaces_auth_errors(monkeypatch):
    async def _resolve_role_for_request(_workspace_id):
        raise ValueError("Workspace access denied")

    monkeypatch.setattr(
        mcp_server,
        "resolve_role_for_request",
        _resolve_role_for_request,
    )

    with pytest.raises(ToolError, match="Workspace access denied"):
        await mcp_server._resolve_workspace_role(str(uuid.uuid4()))


@pytest.mark.anyio
async def test_validate_workflow_returns_expression_details(monkeypatch):
    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return SimpleNamespace()

        async def build_dsl_from_workflow(self, _workflow):
            return SimpleNamespace()

    async def _validate_dsl(*_args, **_kwargs):
        return {
            ValidationResult.new(
                type=ValidationResultType.EXPRESSION,
                status="error",
                msg="Found 1 expression errors",
                ref="step_a",
                expression_type=ExprType.GENERIC,
                detail=[
                    ValidationDetail(
                        type="action.input",
                        msg="bad expr",
                        loc=("step_a", "inputs", "field"),
                    )
                ],
            )
        }

    def _with_session(*_args, **_kwargs):
        return _AsyncContext(_WorkflowService())

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.management.management.WorkflowsManagementService.with_session",
        _with_session,
    )
    monkeypatch.setattr(mcp_server, "validate_dsl", _validate_dsl)

    result = await _tool(mcp_server.validate_workflow)(
        workspace_id=str(uuid.uuid4()), workflow_id=str(uuid.uuid4())
    )
    payload = _payload(result)
    assert payload["valid"] is False
    assert payload["errors"][0]["type"] == "expression"
    assert payload["errors"][0]["details"][0]["type"] == "action.input"
    assert payload["errors"][0]["details"][0]["msg"] == "bad expr"
    assert payload["errors"][0]["details"][0]["loc"] == ["step_a", "inputs", "field"]


@pytest.mark.anyio
async def test_validate_template_action_requires_artifact_id():
    with pytest.raises(TypeError, match="artifact_id"):
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(uuid.uuid4()),
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
async def test_prepare_template_file_upload_stores_artifact(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()

    async def _resolve(_workspace_id):
        return workspace_id, role

    upload_args: dict[str, Any] = {}

    async def _upload_url(
        *,
        key: str,
        bucket: str,
        expiry: int | None = None,
        content_type: str | None = None,
    ):
        upload_args.update(
            {
                "key": key,
                "bucket": bucket,
                "expiry": expiry,
                "content_type": content_type,
            }
        )
        return f"https://example.test/upload/{key}"

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    monkeypatch.setattr(mcp_server.blob, "generate_presigned_upload_url", _upload_url)

    payload = _payload(
        await _tool(mcp_server.prepare_template_file_upload)(
            workspace_id=str(workspace_id),
            relative_path="templates/example.yaml",
            ctx=_fake_ctx(session_id="template-session"),
        )
    )
    stored = await mcp_server._load_template_file_artifact(payload["artifact_id"])
    assert stored is not None
    assert stored.relative_path == "templates/example.yaml"
    assert stored.session_id == "template-session"
    assert stored.client_id == "client-a"
    assert (
        upload_args["expiry"]
        == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
    )


@pytest.mark.anyio
async def test_validate_template_action_remote_uses_artifact(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    artifact = mcp_server.TemplateFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="template-session",
        relative_path="templates/example.yaml",
        blob_key="template-key",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    async def _download_file(_key: str, _bucket: str) -> bytes:
        return b"definition:\n  action: tools.test.run\n"

    async def _validate_text(*, role: Any, template_text: str, check_db: bool):
        _ = role, check_db
        assert "tools.test.run" in template_text
        return json.dumps(
            {"valid": True, "action_name": "tools.test.run", "errors": []}
        )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    monkeypatch.setattr(
        mcp_server.blob,
        "file_exists",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(mcp_server.blob, "download_file", _download_file)
    monkeypatch.setattr(mcp_server, "_validate_template_action_text", _validate_text)
    await mcp_server._store_template_file_artifact(artifact)

    payload = _payload(
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(workspace_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="template-session"),
        )
    )
    assert payload["valid"] is True
    stored = await mcp_server._load_template_file_artifact(str(artifact.artifact_id))
    assert stored is not None
    assert stored.used is True


@pytest.mark.anyio
async def test_validate_template_action_rejects_stdio_transport(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(
        ToolError, match="only supported for remote streamable-http MCP clients"
    ):
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(uuid.uuid4()),
            artifact_id=str(uuid.uuid4()),
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_validate_template_action_remote_rejects_expired_artifact(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    artifact = mcp_server.TemplateFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="template-session",
        relative_path="templates/example.yaml",
        blob_key="template-key",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    await mcp_server._store_template_file_artifact(artifact)

    with pytest.raises(ToolError, match="has expired"):
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(workspace_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="template-session"),
        )


@pytest.mark.anyio
async def test_validate_template_action_remote_rejects_client_mismatch(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    artifact = mcp_server.TemplateFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="template-session",
        relative_path="templates/example.yaml",
        blob_key="template-key",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-b")
    await mcp_server._store_template_file_artifact(artifact)

    with pytest.raises(ToolError, match="not valid for this MCP client"):
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(workspace_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="template-session"),
        )


def test_auto_generate_layout_handles_cycles():
    actions = [
        {"ref": "start", "depends_on": []},
        {"ref": "middle", "depends_on": ["start", "end"]},
        {"ref": "end", "depends_on": ["middle"]},
    ]

    layout = mcp_server._auto_generate_layout(actions)
    refs = {item["ref"] for item in layout["actions"]}

    assert refs == {"start", "middle", "end"}
    assert len(layout["actions"]) == 3


def test_extract_layout_positions_full():
    layout_data = {
        "trigger": {"x": 10, "y": 20},
        "viewport": {"x": 30, "y": 40, "zoom": 1.5},
        "actions": [
            {"ref": "step1", "x": 100, "y": 200},
            {"ref": "step2", "x": 300, "y": 400},
        ],
    }
    trigger, viewport, actions = mcp_server._extract_layout_positions(layout_data)
    assert trigger == (10, 20)
    assert viewport == (30, 40, 1.5)
    assert actions == {"step1": (100, 200), "step2": (300, 400)}


def test_extract_layout_positions_none():
    trigger, viewport, actions = mcp_server._extract_layout_positions(None)
    assert trigger is None
    assert viewport is None
    assert actions is None


def test_extract_layout_positions_partial():
    layout_data = {
        "trigger": {"x": 5},
        "actions": [{"ref": "a", "y": 99}],
    }
    trigger, viewport, actions = mcp_server._extract_layout_positions(layout_data)
    assert trigger == (5, 0.0)
    assert viewport is None
    assert actions == {"a": (0.0, 99)}


def test_extract_layout_positions_nested_position_shape():
    layout_data = {
        "trigger": {"position": {"x": 10, "y": 20}},
        "actions": [{"ref": "a", "position": {"x": 30, "y": 40}}],
    }
    trigger, viewport, actions = mcp_server._extract_layout_positions(layout_data)
    assert trigger == (10, 20)
    assert viewport is None
    assert actions == {"a": (30, 40)}


def test_auto_generate_layout_round_trips_through_extract():
    """Auto-generated layout can be extracted into position tuples."""
    actions = [
        {"ref": "step1", "depends_on": []},
        {"ref": "step2", "depends_on": ["step1"]},
    ]
    layout_data = mcp_server._auto_generate_layout(actions)
    trigger, viewport, action_positions = mcp_server._extract_layout_positions(
        layout_data
    )
    assert trigger == (0, 0)
    assert viewport is None
    assert action_positions is not None
    assert "step1" in action_positions
    assert "step2" in action_positions
    # step1 at depth 0 → y=300, step2 at depth 1 → y=600
    assert action_positions["step1"][1] == 300
    assert action_positions["step2"][1] == 600


@pytest.mark.anyio
async def test_update_workflow_metadata_only_omits_unset_fields(monkeypatch):
    """A metadata-only update must not overwrite title/status with NULL."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    wf_id = uuid.uuid4()

    # Track which attributes are set on the workflow object
    setattr_calls: dict[str, object] = {}

    class FakeWorkflow:
        id = wf_id
        title = "Original title"
        description = "Original desc"
        status = "online"
        version = None
        alias = None
        entrypoint = None
        actions = [SimpleNamespace(ref="step_a", position_x=0.0, position_y=0.0)]

        def __setattr__(self, name, value):
            setattr_calls[name] = value
            object.__setattr__(self, name, value)

    fake_workflow = FakeWorkflow()

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = _FakeSession()

        async def get_workflow(self, _wf_id):
            return fake_workflow

    class _FakeSession:
        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def refresh(self, obj, attrs=None):
            pass

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    result = await _tool(mcp_server.update_workflow)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(wf_id),
    )
    payload = _payload(result)
    assert payload["message"] == f"Workflow {wf_id} updated successfully"
    assert payload["mode"] == "metadata"

    # The metadata fields must NOT have been set via setattr
    assert "title" not in setattr_calls
    assert "description" not in setattr_calls
    assert "status" not in setattr_calls


@pytest.mark.anyio
async def test_update_workflow_definition_yaml_uses_shared_yaml_update(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    workflow = SimpleNamespace(id=workflow_id)
    captured: dict[str, Any] = {}

    class _FakeSession:
        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def refresh(self, obj, attrs=None):
            pass

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = _FakeSession()
            self.for_update_calls: list[bool] = []

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            self.for_update_calls.append(for_update)
            return workflow

    workflow_service = _WorkflowService()

    async def _apply_yaml_update(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(workflow_service),
    )
    monkeypatch.setattr(
        mcp_server,
        "_parse_workflow_yaml_payload",
        lambda definition_yaml: {"definition_yaml": definition_yaml},
    )
    monkeypatch.setattr(mcp_server, "_apply_workflow_yaml_update", _apply_yaml_update)

    payload = _payload(
        await _tool(mcp_server.update_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            definition_yaml="definition:\n  title: Example\n",
            update_mode="replace",
        )
    )
    assert payload["mode"] == "replace"
    assert captured["workflow_id"] == mcp_server.WorkflowUUID.new(workflow_id)
    assert captured["definition_yaml"] == "definition:\n  title: Example\n"
    assert captured["yaml_payload"] == {
        "definition_yaml": "definition:\n  title: Example\n"
    }
    assert captured["update_mode"] == "replace"


@pytest.mark.anyio
async def test_get_workflow_returns_metadata_only(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    payload = _payload(
        await _tool(mcp_server.get_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
        )
    )
    assert payload["id"] == str(workflow_id)
    assert payload["draft_revision"]
    assert payload["draft_document"]["metadata"]["title"] == "Example workflow"
    assert "definition_yaml" not in payload


def test_build_workflow_edit_document_normalizes_null_schedule_timeout() -> None:
    workflow = _workflow_stub(schedules=[_schedule_stub(timeout=None)])

    document = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow)
    )

    assert document.schedules is not None
    assert len(document.schedules) == 1
    assert document.schedules[0].timeout == 0


def test_build_workflow_edit_document_sorts_schedules_by_canonical_content() -> None:
    schedule_a = _schedule_stub(
        id=uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        cron="30 * * * *",
        status="offline",
        timeout=30.0,
    )
    schedule_b = _schedule_stub(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        cron="0 * * * *",
        status="online",
        timeout=0.0,
    )
    workflow_one = _workflow_stub(schedules=[schedule_a, schedule_b])
    workflow_two = _workflow_stub(
        schedules=[
            _schedule_stub(
                id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
                cron="30 * * * *",
                status="offline",
                timeout=30.0,
            ),
            _schedule_stub(
                id=uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffe"),
                cron="0 * * * *",
                status="online",
                timeout=0.0,
            ),
        ]
    )

    document_one = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow_one)
    )
    document_two = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow_two)
    )

    assert [schedule.cron for schedule in document_one.schedules] == [
        "0 * * * *",
        "30 * * * *",
    ]
    assert [
        schedule.model_dump(mode="json") for schedule in document_one.schedules
    ] == [schedule.model_dump(mode="json") for schedule in document_two.schedules]


@pytest.mark.anyio
async def test_replace_workflow_definition_from_dsl_uses_existing_workflow() -> None:
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(
        id=workflow_id,
        title="Original workflow",
        description="Original description",
        entrypoint="start",
        expects={},
        config={},
        returns=None,
        actions=[],
    )
    updated_payload = mcp_server._workflow_edit_document_payload(
        mcp_server._build_workflow_edit_document(
            cast(mcp_server._WorkflowEditDocumentSource, workflow)
        )
    )
    updated_payload["metadata"]["title"] = "Updated workflow"
    updated_payload["definition"]["entrypoint"]["ref"] = "trigger"
    updated_payload["definition"]["actions"] = [
        {
            "ref": "step_a",
            "action": "core.noop",
            "args": {},
            "depends_on": [],
            "description": "",
        }
    ]
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)
    dsl = mcp_server._workflow_edit_document_to_dsl(updated_document)

    captured: dict[str, Any] = {}

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def execute(self, stmt: Any) -> None:
            _ = stmt

        async def flush(self) -> None:
            return None

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

    async def _create_actions_from_dsl(
        dsl_arg: Any,
        wf_id: Any,
        action_positions: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        captured["dsl"] = dsl_arg
        captured["workflow_id"] = wf_id
        captured["action_positions"] = action_positions

    service = SimpleNamespace(
        session=_FakeSession(),
        workspace_id=uuid.uuid4(),
        create_actions_from_dsl=_create_actions_from_dsl,
    )
    await mcp_server._replace_workflow_definition_from_dsl(
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        dsl=dsl,
        action_positions={"step_a": (10.0, 20.0)},
    )

    assert workflow.title == "Updated workflow"
    assert workflow.entrypoint == "trigger"
    assert captured["workflow_id"] == workflow_id
    assert captured["action_positions"] == {"step_a": (10.0, 20.0)}


@pytest.mark.anyio
async def test_edit_workflow_updates_metadata(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

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
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        viewport_x=0.0,
        viewport_y=0.0,
        viewport_zoom=1.0,
    )

    class _FakeSession:
        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def refresh(self, obj, attrs=None):
            pass

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = _FakeSession()
            self.for_update_calls: list[bool] = []

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            self.for_update_calls.append(for_update)
            return workflow

    workflow_service = _WorkflowService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(workflow_service),
    )

    base_revision = mcp_server._compute_workflow_edit_revision(
        mcp_server._build_workflow_edit_document(
            cast(mcp_server._WorkflowEditDocumentSource, workflow)
        )
    )
    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {"op": "replace", "path": "/metadata/title", "value": "Updated flow"}
            ],
        )
    )

    assert payload["message"] == f"Workflow {workflow_id} updated successfully"
    assert payload["draft_revision"]
    assert workflow.title == "Updated flow"
    assert workflow_service.for_update_calls == [True]


@pytest.mark.anyio
async def test_edit_workflow_refreshes_related_state_before_response_revision(
    monkeypatch,
):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    class _FakeSession:
        def __init__(self) -> None:
            self.refresh_calls: list[list[str] | None] = []

        def add(self, obj):
            _ = obj

        async def commit(self):
            return None

        async def refresh(self, obj, attrs=None):
            _ = obj
            self.refresh_calls.append(list(attrs) if attrs is not None else None)

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = _FakeSession()

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            _ = for_update
            return workflow

    workflow_service = _WorkflowService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(workflow_service),
    )

    base_revision = mcp_server._compute_workflow_edit_revision(
        mcp_server._build_workflow_edit_document(
            cast(mcp_server._WorkflowEditDocumentSource, workflow)
        )
    )
    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/metadata/title",
                    "value": "Updated flow",
                }
            ],
        )
    )

    assert payload["message"] == f"Workflow {workflow_id} updated successfully"
    assert [
        "actions",
        "schedules",
        "case_trigger",
    ] in workflow_service.session.refresh_calls


@pytest.mark.anyio
async def test_edit_workflow_validate_only_does_not_persist(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

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
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        viewport_x=0.0,
        viewport_y=0.0,
        viewport_zoom=1.0,
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            _ = for_update
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    base_revision = mcp_server._compute_workflow_edit_revision(
        mcp_server._build_workflow_edit_document(
            cast(mcp_server._WorkflowEditDocumentSource, workflow)
        )
    )
    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/metadata/title",
                    "value": "Validated flow",
                }
            ],
            validate_only=True,
        )
    )

    assert payload["valid"] is True
    assert payload["validate_only"] is True
    assert workflow.title == "Example workflow"


@pytest.mark.anyio
async def test_edit_workflow_updates_metadata_with_disconnected_layout_actions(
    monkeypatch,
):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    trigger_id = f"trigger-{workflow_id}"
    connected_action = _action_stub(
        ref="step_a",
        upstream_edges=[{"source_id": trigger_id, "source_type": "trigger"}],
        position_x=10.0,
        position_y=20.0,
    )
    disconnected_action = _action_stub(
        ref="step_orphan",
        position_x=30.0,
        position_y=40.0,
    )
    workflow = _workflow_stub(
        id=workflow_id,
        actions=[disconnected_action, connected_action],
    )

    class _FakeSession:
        def add(self, obj):
            _ = obj

        async def commit(self):
            return None

        async def refresh(self, obj, attrs=None):
            _ = obj, attrs

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = _FakeSession()

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            _ = for_update
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    base_revision = mcp_server._compute_workflow_edit_revision(
        mcp_server._build_workflow_edit_document(
            cast(mcp_server._WorkflowEditDocumentSource, workflow)
        )
    )
    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/metadata/title",
                    "value": "Updated disconnected flow",
                }
            ],
        )
    )

    assert payload["message"] == f"Workflow {workflow_id} updated successfully"
    assert workflow.title == "Updated disconnected flow"


@pytest.mark.anyio
async def test_edit_workflow_validate_only_canonicalizes_draft_revision(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    trigger_id = f"trigger-{workflow_id}"
    action_a = _action_stub(
        ref="step_a",
        upstream_edges=[{"source_id": trigger_id, "source_type": "trigger"}],
        position_x=10.0,
        position_y=20.0,
    )
    action_b = _action_stub(
        ref="step_b",
        upstream_edges=[
            {
                "source_id": str(action_a.id),
                "source_type": "udf",
                "source_handle": "success",
            }
        ],
        position_x=30.0,
        position_y=40.0,
    )
    workflow = _workflow_stub(
        id=workflow_id,
        actions=[action_b, action_a],
        schedules=[
            _schedule_stub(cron="30 * * * *", timeout=30.0),
            _schedule_stub(cron="0 * * * *", timeout=0.0),
        ],
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            _ = for_update
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    draft_document = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow)
    )
    base_revision = mcp_server._compute_workflow_edit_revision(draft_document)
    reversed_definition_actions = list(
        reversed(draft_document.definition.model_dump(mode="json")["actions"])
    )
    reversed_layout_actions = list(
        reversed(draft_document.layout.model_dump(mode="json")["actions"])
    )
    reversed_schedules = list(
        reversed(
            [schedule.model_dump(mode="json") for schedule in draft_document.schedules]
        )
    )

    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/definition/actions",
                    "value": reversed_definition_actions,
                },
                {
                    "op": "replace",
                    "path": "/layout/actions",
                    "value": reversed_layout_actions,
                },
                {
                    "op": "replace",
                    "path": "/schedules",
                    "value": reversed_schedules,
                },
            ],
            validate_only=True,
        )
    )

    assert payload["valid"] is True
    assert payload["validate_only"] is True
    assert payload["draft_revision"] == base_revision


@pytest.mark.anyio
async def test_edit_workflow_rejects_stale_revision(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

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
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        viewport_x=0.0,
        viewport_y=0.0,
        viewport_zoom=1.0,
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            _ = for_update
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    with pytest.raises(ToolError) as exc_info:
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision="stale-revision",
            patch_ops=[
                {"op": "replace", "path": "/metadata/title", "value": "Updated flow"}
            ],
        )

    payload = cast(dict[str, Any], exc_info.value.args[0])
    assert payload["status"] == "conflict"
    assert payload["current_revision"]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/version", 2),
        ("/definition/config/scheduler", "static"),
        ("/definition/actions/0/id", "00000000-0000-0000-0000-000000000000"),
    ],
)
def test_parse_workflow_edit_request_rejects_forbidden_paths(path, value):
    with pytest.raises(ToolError, match="not editable via edit_workflow"):
        mcp_server._parse_workflow_edit_request(
            base_revision="revision",
            patch_ops=[{"op": "add", "path": path, "value": value}],
            validate_only=False,
        )


@pytest.mark.anyio
async def test_edit_workflow_rejects_unknown_nested_fields(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

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
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        viewport_x=0.0,
        viewport_y=0.0,
        viewport_zoom=1.0,
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            _ = for_update
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    base_revision = mcp_server._compute_workflow_edit_revision(
        mcp_server._build_workflow_edit_document(
            cast(mcp_server._WorkflowEditDocumentSource, workflow)
        )
    )

    with pytest.raises(ToolError, match="Extra inputs are not permitted"):
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[{"op": "add", "path": "/definition/config/foo", "value": "bar"}],
            validate_only=True,
        )


@pytest.mark.anyio
async def test_persist_workflow_edit_document_applies_metadata_with_definition_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow_stub(status="offline", alias=None)
    original_document = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = mcp_server._workflow_edit_document_payload(original_document)
    updated_payload["metadata"]["status"] = "online"
    updated_payload["metadata"]["alias"] = "new-alias"
    updated_payload["definition"]["actions"] = [
        {
            "ref": "step_a",
            "action": "core.noop",
            "args": {},
            "depends_on": [],
            "description": "",
        }
    ]
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def execute(self, stmt: Any) -> None:
            _ = stmt

        async def flush(self) -> None:
            return None

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    captured: dict[str, Any] = {}

    async def _replace_definition_from_dsl(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        mcp_server,
        "_replace_workflow_definition_from_dsl",
        _replace_definition_from_dsl,
    )

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await mcp_server._persist_workflow_edit_document(
        role=SimpleNamespace(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert captured["workflow"] is workflow
    assert workflow.status == "online"
    assert workflow.alias == "new-alias"


@pytest.mark.anyio
async def test_persist_workflow_edit_document_preserves_offline_schedule_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow_stub()
    original_document = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = mcp_server._workflow_edit_document_payload(original_document)
    updated_payload["schedules"] = [
        {
            "cron": "0 * * * *",
            "status": "offline",
            "inputs": {},
            "timeout": 0,
        }
    ]
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    offline_schedule_id = uuid.uuid4()
    updated_schedules: list[tuple[uuid.UUID, Any]] = []

    async def _replace_schedules(**kwargs: Any) -> list[uuid.UUID]:
        _ = kwargs
        return [offline_schedule_id]

    class _FakeWorkflowSchedulesService:
        def __init__(self, session: Any, role: Any) -> None:
            _ = session, role

        async def update_schedule(self, schedule_id: uuid.UUID, params: Any) -> None:
            updated_schedules.append((schedule_id, params))

    monkeypatch.setattr(mcp_server, "_replace_workflow_schedules", _replace_schedules)
    monkeypatch.setattr(
        mcp_server,
        "WorkflowSchedulesService",
        _FakeWorkflowSchedulesService,
    )

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await mcp_server._persist_workflow_edit_document(
        role=SimpleNamespace(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert len(updated_schedules) == 1
    assert updated_schedules[0][0] == offline_schedule_id
    assert updated_schedules[0][1].status == "offline"


@pytest.mark.anyio
async def test_persist_workflow_edit_document_resets_removed_layout_fields() -> None:
    action = SimpleNamespace(
        id=uuid.uuid4(),
        ref="step_a",
        type="core.noop",
        title="Step A",
        description="",
        status="offline",
        inputs="{}",
        control_flow={},
        is_interactive=False,
        interaction=None,
        upstream_edges=[],
        position_x=40.0,
        position_y=50.0,
    )
    workflow = _workflow_stub(
        trigger_position_x=10.0,
        trigger_position_y=20.0,
        viewport_x=30.0,
        viewport_y=40.0,
        viewport_zoom=1.5,
        actions=[action],
    )
    original_document = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = mcp_server._workflow_edit_document_payload(original_document)
    del updated_payload["layout"]["trigger"]["x"]
    del updated_payload["layout"]["viewport"]["x"]
    del updated_payload["layout"]["actions"][0]["x"]
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await mcp_server._persist_workflow_edit_document(
        role=SimpleNamespace(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert workflow.trigger_position_x == 0.0
    assert workflow.trigger_position_y == 20.0
    assert workflow.viewport_x == 0.0
    assert workflow.viewport_y == 40.0
    assert workflow.viewport_zoom == 1.5
    assert action.position_x == 0.0
    assert action.position_y == 50.0


@pytest.mark.anyio
async def test_persist_workflow_edit_document_resets_removed_layout_actions() -> None:
    action_a = SimpleNamespace(
        id=uuid.uuid4(),
        ref="step_a",
        type="core.noop",
        title="Step A",
        description="",
        status="offline",
        inputs="{}",
        control_flow={},
        is_interactive=False,
        interaction=None,
        upstream_edges=[],
        position_x=40.0,
        position_y=50.0,
    )
    action_b = SimpleNamespace(
        id=uuid.uuid4(),
        ref="step_b",
        type="core.noop",
        title="Step B",
        description="",
        status="offline",
        inputs="{}",
        control_flow={},
        is_interactive=False,
        interaction=None,
        upstream_edges=[],
        position_x=60.0,
        position_y=70.0,
    )
    workflow = _workflow_stub(actions=[action_a, action_b])
    original_document = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = mcp_server._workflow_edit_document_payload(original_document)
    updated_payload["layout"]["actions"] = [updated_payload["layout"]["actions"][1]]
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await mcp_server._persist_workflow_edit_document(
        role=SimpleNamespace(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert action_a.position_x == 0.0
    assert action_a.position_y == 0.0
    assert action_b.position_x == 60.0
    assert action_b.position_y == 70.0


@pytest.mark.anyio
async def test_persist_workflow_edit_document_resets_removed_layout_object() -> None:
    action = SimpleNamespace(
        id=uuid.uuid4(),
        ref="step_a",
        type="core.noop",
        title="Step A",
        description="",
        status="offline",
        inputs="{}",
        control_flow={},
        is_interactive=False,
        interaction=None,
        upstream_edges=[],
        position_x=40.0,
        position_y=50.0,
    )
    workflow = _workflow_stub(
        trigger_position_x=10.0,
        trigger_position_y=20.0,
        viewport_x=30.0,
        viewport_y=40.0,
        viewport_zoom=1.5,
        actions=[action],
    )
    original_document = mcp_server._build_workflow_edit_document(
        cast(mcp_server._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = mcp_server._workflow_edit_document_payload(original_document)
    del updated_payload["layout"]
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await mcp_server._persist_workflow_edit_document(
        role=SimpleNamespace(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert workflow.trigger_position_x == 0.0
    assert workflow.trigger_position_y == 0.0
    assert workflow.viewport_x == 0.0
    assert workflow.viewport_y == 0.0
    assert workflow.viewport_zoom == 1.0
    assert action.position_x == 0.0
    assert action.position_y == 0.0


@pytest.mark.anyio
async def test_get_workflow_returns_inline_definition_when_requested(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(
        mcp_server,
        "_get_workflow_folder_path",
        lambda **_kwargs: asyncio.sleep(0, result=None),
    )
    monkeypatch.setattr(
        mcp_server,
        "_build_workflow_yaml_envelope",
        lambda **_kwargs: asyncio.sleep(
            0, result={"definition": {"title": "Inline workflow"}}
        ),
    )

    payload = _payload(
        await _tool(mcp_server.get_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            include_definition_yaml=True,
        )
    )
    assert payload["definition_transport"] == "inline"
    assert payload["definition_size_bytes"] <= payload["inline_limit_bytes"]
    assert "definition_yaml" in payload
    assert "Inline workflow" in payload["definition_yaml"]


@pytest.mark.anyio
async def test_get_workflow_returns_staged_metadata_when_inline_is_too_large(
    monkeypatch,
):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(
        mcp_server,
        "_get_workflow_folder_path",
        lambda **_kwargs: asyncio.sleep(0, result="/detections/high/"),
    )
    monkeypatch.setattr(
        mcp_server,
        "_build_workflow_yaml_envelope",
        lambda **_kwargs: asyncio.sleep(
            0, result={"definition": {"description": "x" * 200_000}}
        ),
    )

    payload = _payload(
        await _tool(mcp_server.get_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            include_definition_yaml=True,
        )
    )
    assert payload["definition_transport"] == "staged_required"
    assert payload["definition_size_bytes"] > payload["inline_limit_bytes"]
    assert "definition_yaml" not in payload
    assert payload["suggested_relative_path"].endswith(".yaml")
    assert payload["suggested_relative_path"].startswith("detections/high/")


@pytest.mark.anyio
async def test_create_workflow_definition_yaml_uses_import_helpers(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    captured: dict[str, Any] = {}

    async def _create_from_import_data(*, role, import_data, use_workflow_id=False):
        captured["import_data"] = import_data
        captured["use_workflow_id"] = use_workflow_id
        return SimpleNamespace(
            id=uuid.uuid4(),
            title="Imported workflow",
            description="Imported description",
            status="offline",
        )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "_build_import_data_from_workflow_yaml",
        lambda **kwargs: {"definition_yaml": kwargs["definition_yaml"]},
    )
    monkeypatch.setattr(
        mcp_server,
        "_create_workflow_from_import_data",
        _create_from_import_data,
    )

    payload = _payload(
        await _tool(mcp_server.create_workflow)(
            workspace_id=str(uuid.uuid4()),
            title="Example",
            description="Desc",
            definition_yaml="definition:\n  title: Example\n",
        )
    )
    assert payload["title"] == "Imported workflow"
    assert captured["import_data"] == {
        "definition_yaml": "definition:\n  title: Example\n"
    }


@pytest.mark.anyio
async def test_create_workflow_rejects_oversized_inline_definition_yaml(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(ToolError, match="prepare_workflow_file_upload"):
        await _tool(mcp_server.create_workflow)(
            workspace_id=str(uuid.uuid4()),
            title="Example",
            definition_yaml="x" * (mcp_server._inline_workflow_yaml_max_bytes() + 1),
        )


@pytest.mark.anyio
async def test_create_workflow_surfaces_builtin_registry_sync_pending(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    class _WorkflowService:
        async def create_workflow_from_external_definition(self, *_args, **_kwargs):
            raise BuiltinRegistryHasNoSelectionError(
                "Builtin registry sync is still in progress. Please retry shortly.",
                detail={"origin": "tracecat_registry"},
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    with pytest.raises(ToolError, match="retry shortly"):
        await _tool(mcp_server.create_workflow)(
            workspace_id=str(uuid.uuid4()),
            title="Example",
            definition_yaml="definition:\n  title: Example\n",
        )


@pytest.mark.anyio
async def test_update_workflow_rejects_oversized_inline_definition_yaml(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(ToolError, match="prepare_workflow_file_upload"):
        await _tool(mcp_server.update_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
            definition_yaml="x" * (mcp_server._inline_workflow_yaml_max_bytes() + 1),
        )


@pytest.mark.anyio
async def test_publish_workflow_builtin_registry_not_ready_returns_validation_failure(
    monkeypatch,
):
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace()
    workflow = SimpleNamespace(id=workflow_id, alias=None, registry_lock=None)
    dsl = SimpleNamespace(actions=[SimpleNamespace(action="core.transform.reshape")])

    class _WorkflowService:
        def __init__(self):
            self.session = object()

        async def get_workflow(self, wf_id):
            assert wf_id == mcp_server.WorkflowUUID.new(workflow_id)
            return workflow

        async def build_dsl_from_workflow(self, workflow_obj):
            assert workflow_obj is workflow
            return dsl

    class _LockService:
        def __init__(self, *_args):
            pass

        async def resolve_lock_with_bindings(self, _action_names):
            raise BuiltinRegistryHasNoSelectionError(
                "Builtin registry sync is still in progress. Please retry shortly.",
                detail={"origin": "tracecat_registry"},
            )

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(
        mcp_server, "validate_dsl", lambda **_kwargs: asyncio.sleep(0, result=[])
    )
    monkeypatch.setattr(mcp_server, "RegistryLockService", _LockService)

    result = await _tool(mcp_server.publish_workflow)(
        workspace_id=str(workspace_id),
        workflow_id=str(workflow_id),
    )

    payload = _payload(result)
    assert payload["workflow_id"] == str(workflow_id)
    assert payload["status"] == "failure"
    assert payload["message"] == "1 validation error(s)"
    error = payload["errors"][0]
    assert error["type"] == "dsl"
    assert "retry shortly" in error["message"]
    assert error["details"][0]["type"] == "registry.builtin_sync_pending"


@pytest.mark.anyio
async def test_get_workflow_file_rejects_stdio_transport(monkeypatch):
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=uuid.uuid4())
    workflow = SimpleNamespace(
        id=workflow_id,
        title="Collision workflow",
        description=None,
        status="offline",
        version=None,
        alias=None,
        entrypoint=None,
        folder_id=None,
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        viewport_x=0.0,
        viewport_y=0.0,
        viewport_zoom=1.0,
        actions=[],
        schedules=[],
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

        async def build_dsl_from_workflow(self, _workflow):
            return SimpleNamespace(model_dump=lambda **_kwargs: {"title": "Collision"})

    class _CaseTriggerService:
        def __init__(self, _session, *, role):
            self.role = role

        async def get_case_trigger(self, _workflow_id):
            raise mcp_server.TracecatNotFoundError("not found")

    monkeypatch.setattr(
        mcp_server,
        "_resolve_workspace_role",
        lambda _workspace_id: asyncio.sleep(0, result=(workspace_id, role)),
    )
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(mcp_server, "CaseTriggersService", _CaseTriggerService)

    with pytest.raises(
        ToolError, match="only supported for remote streamable-http MCP clients"
    ):
        await _tool(mcp_server.get_workflow_file)(
            workspace_id=str(workspace_id),
            workflow_id=str(workflow_id),
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_get_workflow_file_remote_returns_download_metadata(monkeypatch):
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=uuid.uuid4())
    uploaded: dict[str, Any] = {}
    workflow = SimpleNamespace(
        id=workflow_id,
        title="Remote workflow",
        description=None,
        status="offline",
        version=None,
        alias=None,
        entrypoint=None,
        folder_id=None,
        trigger_position_x=0.0,
        trigger_position_y=0.0,
        viewport_x=0.0,
        viewport_y=0.0,
        viewport_zoom=1.0,
        actions=[],
        schedules=[],
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

        async def build_dsl_from_workflow(self, _workflow):
            return SimpleNamespace(model_dump=lambda **_kwargs: {"title": "Remote"})

    class _CaseTriggerService:
        def __init__(self, _session, *, role):
            self.role = role

        async def get_case_trigger(self, _workflow_id):
            raise mcp_server.TracecatNotFoundError("not found")

    async def _upload_file(
        content: bytes, key: str, bucket: str, content_type: str | None = None
    ):
        uploaded["content"] = content
        uploaded["key"] = key
        uploaded["bucket"] = bucket
        uploaded["content_type"] = content_type

    async def _download_url(
        *,
        key: str,
        bucket: str,
        expiry: int | None = None,
        override_content_type: str | None = None,
    ):
        uploaded["download_args"] = {
            "key": key,
            "bucket": bucket,
            "expiry": expiry,
            "override_content_type": override_content_type,
        }
        return "https://example.test/download"

    monkeypatch.setattr(
        mcp_server,
        "_resolve_workspace_role",
        lambda _workspace_id: asyncio.sleep(0, result=(workspace_id, role)),
    )
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(mcp_server, "CaseTriggersService", _CaseTriggerService)
    monkeypatch.setattr(mcp_server.blob, "upload_file", _upload_file)
    monkeypatch.setattr(
        mcp_server.blob,
        "generate_presigned_download_url",
        _download_url,
    )

    result = await _tool(mcp_server.get_workflow_file)(
        workspace_id=str(workspace_id),
        workflow_id=str(workflow_id),
        ctx=_fake_ctx(session_id="remote-session"),
    )

    payload = _payload(result)
    assert payload["download_url"] == "https://example.test/download"
    assert payload["transport"] == "streamable-http"
    assert "definition_yaml" not in payload
    assert uploaded["content_type"] == "application/yaml"
    assert (
        uploaded["download_args"]["expiry"]
        == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
    )
    assert uploaded["key"].startswith(
        f"{workspace_id}/mcp/workflow-files/remote-session/"
    )


@pytest.mark.anyio
async def test_get_workflow_file_draft_false_uses_published_definition(
    monkeypatch,
):
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=uuid.uuid4())
    uploaded: dict[str, Any] = {}
    workflow = SimpleNamespace(
        id=workflow_id,
        title="Published workflow",
        description="Example description",
        status="offline",
        version=None,
        alias=None,
        entrypoint=None,
        folder_id=None,
        trigger_position_x=1.0,
        trigger_position_y=2.0,
        viewport_x=3.0,
        viewport_y=4.0,
        viewport_zoom=0.5,
        actions=[],
        schedules=[],
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

        async def build_dsl_from_workflow(self, _workflow):
            raise AssertionError("draft export should not build DSL from workflow")

    class _DefinitionService:
        def __init__(self, _session, *, role):
            self.role = role

        async def get_definition_by_workflow_id(self, _workflow_id, *, version=None):
            _ = version
            return SimpleNamespace(
                version=3,
                content={
                    "title": "Published definition",
                    "description": "Published description",
                    "entrypoint": {"ref": "step_a"},
                    "actions": [
                        {
                            "ref": "step_a",
                            "action": "core.workflow.execute",
                            "args": {},
                        }
                    ],
                },
            )

    class _CaseTriggerService:
        def __init__(self, _session, *, role):
            self.role = role

        async def get_case_trigger(self, _workflow_id):
            raise mcp_server.TracecatNotFoundError("not found")

    async def _upload_file(
        content: bytes, key: str, bucket: str, content_type: str | None = None
    ):
        uploaded["content"] = content
        uploaded["key"] = key
        uploaded["bucket"] = bucket
        uploaded["content_type"] = content_type

    async def _download_url(
        *,
        key: str,
        bucket: str,
        expiry: int | None = None,
        override_content_type: str | None = None,
    ):
        uploaded["download_args"] = {
            "key": key,
            "bucket": bucket,
            "expiry": expiry,
            "override_content_type": override_content_type,
        }
        return "https://example.test/published.yaml"

    monkeypatch.setattr(
        mcp_server,
        "_resolve_workspace_role",
        lambda _workspace_id: asyncio.sleep(0, result=(workspace_id, role)),
    )
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(mcp_server, "WorkflowDefinitionsService", _DefinitionService)
    monkeypatch.setattr(mcp_server, "CaseTriggersService", _CaseTriggerService)
    monkeypatch.setattr(mcp_server.blob, "upload_file", _upload_file)
    monkeypatch.setattr(
        mcp_server.blob,
        "generate_presigned_download_url",
        _download_url,
    )

    result = await _tool(mcp_server.get_workflow_file)(
        workspace_id=str(workspace_id),
        workflow_id=str(workflow_id),
        draft=False,
        ctx=_fake_ctx(session_id="published-session"),
    )

    payload = _payload(result)
    exported = yaml.safe_load(uploaded["content"].decode("utf-8"))
    assert payload["draft"] is False
    assert payload["download_url"] == "https://example.test/published.yaml"
    assert (
        uploaded["download_args"]["expiry"]
        == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
    )
    assert exported["version"] == 3
    assert exported["definition"]["title"] == "Published definition"


@pytest.mark.anyio
async def test_prepare_workflow_file_upload_stores_artifact_metadata(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()

    async def _resolve(_workspace_id):
        return workspace_id, role

    upload_args: dict[str, Any] = {}

    async def _upload_url(
        *,
        key: str,
        bucket: str,
        expiry: int | None = None,
        content_type: str | None = None,
    ):
        upload_args.update(
            {
                "key": key,
                "bucket": bucket,
                "expiry": expiry,
                "content_type": content_type,
            }
        )
        return f"https://example.test/upload/{key}"

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    monkeypatch.setattr(mcp_server.blob, "generate_presigned_upload_url", _upload_url)

    result = await _tool(mcp_server.prepare_workflow_file_upload)(
        workspace_id=str(workspace_id),
        relative_path="detections/high/workflow.yaml",
        operation="update",
        workflow_id=str(uuid.uuid4()),
        ctx=_fake_ctx(session_id="session-a"),
    )

    payload = _payload(result)
    assert payload["relative_path"] == "detections/high/workflow.yaml"
    assert payload["folder_path"] == "/detections/high/"
    stored = await mcp_server._load_workflow_file_artifact(payload["artifact_id"])
    assert stored is not None
    assert stored.client_id == "client-a"
    assert stored.session_id == "session-a"
    assert stored.workspace_id == workspace_id
    assert (
        upload_args["expiry"]
        == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
    )


@pytest.mark.anyio
async def test_create_workflow_from_uploaded_file_rejects_expired_artifact(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    artifact = mcp_server.WorkflowFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="session-a",
        operation=mcp_server.WorkflowFileOperation.CREATE,
        relative_path="workflow.yaml",
        folder_path=None,
        blob_key="blob-key",
        expires_at=datetime.now(UTC) - timedelta(seconds=10),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    await mcp_server._store_workflow_file_artifact(artifact)

    with pytest.raises(ToolError, match="has expired"):
        await _tool(mcp_server.create_workflow_from_uploaded_file)(
            workspace_id=str(workspace_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="session-a"),
        )


@pytest.mark.anyio
async def test_create_workflow_from_uploaded_file_rejects_stdio_transport(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(
        ToolError, match="only supported for remote streamable-http MCP clients"
    ):
        await _tool(mcp_server.create_workflow_from_uploaded_file)(
            workspace_id=str(uuid.uuid4()),
            artifact_id=str(uuid.uuid4()),
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_create_workflow_from_uploaded_file_rejects_client_mismatch(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    artifact = mcp_server.WorkflowFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="session-a",
        operation=mcp_server.WorkflowFileOperation.CREATE,
        relative_path="workflow.yaml",
        folder_path=None,
        blob_key="blob-key",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-b")
    await mcp_server._store_workflow_file_artifact(artifact)

    with pytest.raises(ToolError, match="not valid for this MCP client"):
        await _tool(mcp_server.create_workflow_from_uploaded_file)(
            workspace_id=str(workspace_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="session-a"),
        )


@pytest.mark.anyio
async def test_create_workflow_from_uploaded_file_imports_and_assigns_folder(
    monkeypatch,
):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    assigned: dict[str, Any] = {}
    created_workflow = SimpleNamespace(
        id=uuid.uuid4(),
        title="Created workflow",
        description="Created description",
        status="offline",
    )
    artifact = mcp_server.WorkflowFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="session-a",
        operation=mcp_server.WorkflowFileOperation.CREATE,
        relative_path="detections/workflow.yaml",
        folder_path="/detections/",
        blob_key="blob-key",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    async def _download_file(_key: str, _bucket: str) -> bytes:
        return (
            b"definition:\n"
            b"  title: Uploaded workflow\n"
            b"  description: Uploaded description\n"
            b"  entrypoint:\n"
            b"    ref: manual\n"
            b"  actions:\n"
            b"    - ref: step_a\n"
            b"      action: core.transform.reshape\n"
            b"      args: {}\n"
        )

    async def _create_from_import(
        *, role: Any, import_data: dict[str, Any], use_workflow_id: bool = False
    ):
        assigned["import_data"] = import_data
        assigned["use_workflow_id"] = use_workflow_id
        return created_workflow

    async def _assign_folder(
        *, role: Any, session: Any, workflow_id: Any, folder_path: str | None
    ):
        assigned["workflow_id"] = workflow_id
        assigned["folder_path"] = folder_path

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    monkeypatch.setattr(
        mcp_server.blob,
        "file_exists",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(mcp_server.blob, "download_file", _download_file)
    monkeypatch.setattr(
        mcp_server, "_create_workflow_from_import_data", _create_from_import
    )
    monkeypatch.setattr(mcp_server, "_assign_workflow_to_folder", _assign_folder)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(SimpleNamespace(session=object())),
    )
    await mcp_server._store_workflow_file_artifact(artifact)

    result = await _tool(mcp_server.create_workflow_from_uploaded_file)(
        workspace_id=str(workspace_id),
        artifact_id=str(artifact.artifact_id),
        ctx=_fake_ctx(session_id="session-a"),
    )

    payload = _payload(result)
    assert payload["id"] == str(created_workflow.id)
    assert assigned["folder_path"] == "/detections/"
    stored = await mcp_server._load_workflow_file_artifact(str(artifact.artifact_id))
    assert stored is not None
    assert stored.used is True
    assert stored.sha256 is not None


@pytest.mark.anyio
async def test_update_workflow_from_uploaded_file_updates_and_rejects_replay(
    monkeypatch,
):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    captured: dict[str, Any] = {}
    workflow = SimpleNamespace(id=workflow_id)
    artifact = mcp_server.WorkflowFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="session-a",
        operation=mcp_server.WorkflowFileOperation.UPDATE,
        relative_path="detections/critical/workflow.yaml",
        folder_path="/detections/critical/",
        blob_key="blob-key",
        workflow_id=workflow_id,
        update_mode="replace",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, wf_id):
            assert wf_id == mcp_server.WorkflowUUID.new(workflow_id)
            return workflow

    async def _download_file(_key: str, _bucket: str) -> bytes:
        return (
            b"definition:\n"
            b"  title: Uploaded workflow\n"
            b"  description: Uploaded description\n"
            b"  entrypoint:\n"
            b"    ref: manual\n"
            b"  actions:\n"
            b"    - ref: step_a\n"
            b"      action: core.transform.reshape\n"
            b"      args: {}\n"
        )

    async def _apply_update(**kwargs):
        captured["update"] = kwargs

    async def _assign_folder(
        *, role: Any, session: Any, workflow_id: Any, folder_path: str | None
    ):
        captured["folder_path"] = folder_path
        captured["workflow_id"] = workflow_id

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    monkeypatch.setattr(
        mcp_server.blob,
        "file_exists",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(mcp_server.blob, "download_file", _download_file)
    monkeypatch.setattr(mcp_server, "_apply_workflow_yaml_update", _apply_update)
    monkeypatch.setattr(mcp_server, "_assign_workflow_to_folder", _assign_folder)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    await mcp_server._store_workflow_file_artifact(artifact)

    result = await _tool(mcp_server.update_workflow_from_uploaded_file)(
        workspace_id=str(workspace_id),
        workflow_id=str(workflow_id),
        artifact_id=str(artifact.artifact_id),
        ctx=_fake_ctx(session_id="session-a"),
    )

    payload = _payload(result)
    assert payload["mode"] == "replace"
    assert captured["folder_path"] == "/detections/critical/"
    assert captured["workflow_id"] == mcp_server.WorkflowUUID.new(workflow_id)
    assert captured["update"]["update_mode"] == "replace"

    with pytest.raises(ToolError, match="already been consumed"):
        await _tool(mcp_server.update_workflow_from_uploaded_file)(
            workspace_id=str(workspace_id),
            workflow_id=str(workflow_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="session-a"),
        )


@pytest.mark.anyio
async def test_update_workflow_from_uploaded_file_rejects_stdio_transport(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(
        ToolError, match="only supported for remote streamable-http MCP clients"
    ):
        await _tool(mcp_server.update_workflow_from_uploaded_file)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
            artifact_id=str(uuid.uuid4()),
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_update_workflow_from_uploaded_file_rejects_update_mode_mismatch(
    monkeypatch,
):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    artifact = mcp_server.WorkflowFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        client_id="client-a",
        session_id="session-a",
        operation=mcp_server.WorkflowFileOperation.UPDATE,
        relative_path="workflow.yaml",
        folder_path=None,
        blob_key="blob-key",
        workflow_id=workflow_id,
        update_mode="replace",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    await mcp_server._store_workflow_file_artifact(artifact)

    with pytest.raises(
        ToolError, match="update_mode does not match the prepared upload artifact"
    ):
        await _tool(mcp_server.update_workflow_from_uploaded_file)(
            workspace_id=str(workspace_id),
            workflow_id=str(workflow_id),
            artifact_id=str(artifact.artifact_id),
            update_mode="patch",
            ctx=_fake_ctx(session_id="session-a"),
        )


@pytest.mark.anyio
async def test_update_workflow_from_uploaded_file_rejects_cross_workspace_target(
    monkeypatch,
):
    workspace_id = uuid.uuid4()
    other_workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()
    artifact = mcp_server.WorkflowFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=other_workspace_id,
        client_id="client-a",
        session_id="session-a",
        operation=mcp_server.WorkflowFileOperation.UPDATE,
        relative_path="workflow.yaml",
        folder_path=None,
        blob_key="blob-key",
        workflow_id=workflow_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_get_workflow_artifact_redis", lambda: fake_redis)
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    await mcp_server._store_workflow_file_artifact(artifact)

    with pytest.raises(ToolError, match="not valid for this workspace"):
        await _tool(mcp_server.update_workflow_from_uploaded_file)(
            workspace_id=str(workspace_id),
            workflow_id=str(workflow_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="session-a"),
        )


def test_evaluate_configuration_reports_missing_workspace_secret_keys():
    requirements = [
        {
            "name": "slack",
            "required_keys": ["SLACK_BOT_TOKEN"],
            "optional": False,
        }
    ]
    workspace_inventory = {"slack": set()}
    configured, missing = mcp_server._evaluate_configuration(
        requirements,
        workspace_inventory,
    )

    assert configured is False
    assert missing == ["missing key: slack.SLACK_BOT_TOKEN"]


@pytest.mark.anyio
async def test_create_table_parses_columns_json(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    created = {}

    async def _create_table(params):
        created["params"] = params
        return SimpleNamespace(id=uuid.uuid4(), name=params.name)

    table_service = SimpleNamespace(create_table=_create_table)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.tables.service.TablesService.with_session",
        lambda role: _AsyncContext(table_service),
    )

    result = await _tool(mcp_server.create_table)(
        workspace_id=str(uuid.uuid4()),
        name="ioc_table",
        columns_json='[{"name":"ioc","type":"TEXT","nullable":false}]',
    )
    payload = _payload(result)
    assert payload["name"] == "ioc_table"
    assert created["params"].name == "ioc_table"
    assert created["params"].columns[0].name == "ioc"


@pytest.mark.anyio
async def test_get_action_context_includes_configuration(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    indexed_action = SimpleNamespace(
        manifest=SimpleNamespace(),
        index_entry=SimpleNamespace(options=None),
    )
    registry_service = SimpleNamespace(
        aggregate_secrets_from_manifest=lambda _manifest, _action_name: [
            RegistrySecret(name="slack", keys=["SLACK_BOT_TOKEN"])
        ],
    )

    async def _get_indexed(_action_name):
        return indexed_action

    registry_service.get_action_from_index = _get_indexed

    async def _create_tool(_action_name, _indexed):
        return SimpleNamespace(
            description="Post to Slack",
            parameters_json_schema={
                "type": "object",
                "properties": {"channel": {"type": "string"}},
                "required": ["channel"],
            },
        )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    async def _secret_inventory(_role):
        return {"slack": {"SLACK_BOT_TOKEN"}}

    monkeypatch.setattr(
        mcp_server,
        "_load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(registry_service),
    )
    monkeypatch.setattr(mcp_server, "create_tool_from_registry", _create_tool)

    result = await _tool(mcp_server.get_action_context)(
        workspace_id=str(uuid.uuid4()),
        action_name="tools.slack.post_message",
    )
    payload = _payload(result)
    assert payload["action_name"] == "tools.slack.post_message"
    assert payload["configured"] is True
    assert payload["missing_requirements"] == []
    assert payload["required_secrets"][0]["name"] == "slack"


@pytest.mark.anyio
async def test_list_secrets_metadata_returns_keys_not_values(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workspace_secret = SimpleNamespace(
        id=uuid.uuid4(),
        name="slack",
        type="custom",
        description=None,
        environment="default",
        tags={"team": "soc"},
        encrypted_keys=b"encrypted",
    )

    async def _list_secrets():
        return [workspace_secret]

    secret_service = SimpleNamespace(
        list_secrets=_list_secrets,
        decrypt_keys=lambda _encrypted: [
            SimpleNamespace(key="API_KEY", value="super-secret-value")
        ],
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.secrets.service.SecretsService.with_session",
        lambda role: _AsyncContext(secret_service),
    )

    result = await _tool(mcp_server.list_secrets_metadata)(
        workspace_id=str(uuid.uuid4()),
    )
    payload = _payload(result)
    assert payload["items"][0]["keys"] == ["API_KEY"]
    assert payload["has_more"] is False


# ---------------------------------------------------------------------------
# Webhook tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_webhook(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    webhook_id = uuid.uuid4()
    fake_webhook = SimpleNamespace(
        id=webhook_id,
        secret="whsec_test",
        status="online",
        entrypoint_ref=None,
        allowlisted_cidrs=[],
        filters={},
        methods=["POST"],
        workflow_id=uuid.uuid4(),
        url="https://example.com/webhook",
        api_key=None,
    )

    async def _get_webhook(session, workspace_id, workflow_id):
        return fake_webhook

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.webhooks.service.get_webhook",
        _get_webhook,
    )

    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(SimpleNamespace()),
    )

    result = await _tool(mcp_server.get_webhook)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
    )
    payload = _payload(result)
    assert payload["secret"] == "whsec_test"
    assert payload["status"] == "online"


@pytest.mark.anyio
async def test_get_webhook_not_found(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    async def _get_webhook(session, workspace_id, workflow_id):
        return None

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.webhooks.service.get_webhook",
        _get_webhook,
    )
    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(SimpleNamespace()),
    )

    with pytest.raises(ToolError, match="Webhook not found"):
        await _tool(mcp_server.get_webhook)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
        )


@pytest.mark.anyio
async def test_update_webhook(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    fake_webhook = SimpleNamespace(
        id=uuid.uuid4(),
        secret="whsec_test",
        status="offline",
        methods=["POST"],
        entrypoint_ref=None,
        allowlisted_cidrs=[],
        filters={},
        workflow_id=uuid.uuid4(),
        url="https://example.com/webhook",
        api_key=None,
    )

    async def _get_webhook(session, workspace_id, workflow_id):
        return fake_webhook

    class FakeSession:
        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def refresh(self, _obj):
            pass

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.webhooks.service.get_webhook",
        _get_webhook,
    )
    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(FakeSession()),
    )

    result = await _tool(mcp_server.update_webhook)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        status="online",
    )
    payload = _payload(result)
    assert "updated successfully" in payload["message"]
    assert fake_webhook.status == "online"


@pytest.mark.anyio
async def test_update_webhook_omits_unset_fields(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    setattr_calls: dict[str, object] = {}

    class FakeWebhook(SimpleNamespace):
        def __setattr__(self, name, value):
            setattr_calls[name] = value
            super().__setattr__(name, value)

    fake_webhook = FakeWebhook(
        id=uuid.uuid4(),
        secret="whsec_test",
        status="offline",
        methods=["POST"],
        entrypoint_ref=None,
        allowlisted_cidrs=[],
        filters={},
        workflow_id=uuid.uuid4(),
        url="https://example.com/webhook",
        api_key=None,
    )
    setattr_calls.clear()

    async def _get_webhook(session, workspace_id, workflow_id):
        return fake_webhook

    class FakeSession:
        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def refresh(self, _obj):
            pass

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.webhooks.service.get_webhook",
        _get_webhook,
    )
    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(FakeSession()),
    )

    result = await _tool(mcp_server.update_webhook)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
    )
    payload = _payload(result)
    assert "updated successfully" in payload["message"]
    assert fake_webhook.status == "offline"
    assert "status" not in setattr_calls
    assert "methods" not in setattr_calls
    assert "allowlisted_cidrs" not in setattr_calls


# ---------------------------------------------------------------------------
# Case trigger tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_case_trigger(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    ct_id = uuid.uuid4()
    fake_ct = SimpleNamespace(
        id=ct_id,
        workflow_id=uuid.uuid4(),
        status="online",
        event_types=["case_created"],
        tag_filters=["malware"],
    )

    async def _get_case_trigger(_workflow_id):
        return fake_ct

    ct_service = SimpleNamespace(get_case_trigger=_get_case_trigger)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.case_triggers.service.CaseTriggersService.with_session",
        lambda role: _AsyncContext(ct_service),
    )

    result = await _tool(mcp_server.get_case_trigger)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
    )
    payload = _payload(result)
    assert payload["status"] == "online"
    assert payload["event_types"] == ["case_created"]
    assert payload["tag_filters"] == ["malware"]


@pytest.mark.anyio
async def test_get_case_trigger_not_found(monkeypatch):
    from tracecat.exceptions import TracecatNotFoundError

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _get_case_trigger(_workflow_id):
        raise TracecatNotFoundError("Case trigger not found")

    ct_service = SimpleNamespace(get_case_trigger=_get_case_trigger)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.case_triggers.service.CaseTriggersService.with_session",
        lambda role: _AsyncContext(ct_service),
    )

    with pytest.raises(ToolError, match="not found"):
        await _tool(mcp_server.get_case_trigger)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
        )


@pytest.mark.anyio
async def test_update_case_trigger(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    trigger_id = uuid.uuid4()

    captured_update: dict[str, Any] = {}

    async def _update_case_trigger(_workflow_id, _params, create_missing_tags=False):
        captured_update["workflow_id"] = _workflow_id
        captured_update["status"] = _params.status
        captured_update["event_types"] = _params.event_types
        captured_update["tag_filters"] = _params.tag_filters
        captured_update["create_missing_tags"] = create_missing_tags
        return SimpleNamespace(
            id=trigger_id,
            workflow_id=workflow_id,
            status=_params.status or "offline",
            event_types=_params.event_types or [],
            tag_filters=_params.tag_filters or [],
        )

    ct_service = SimpleNamespace(update_case_trigger=_update_case_trigger)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.case_triggers.service.CaseTriggersService.with_session",
        lambda role: _AsyncContext(ct_service),
    )

    result = await _tool(mcp_server.update_case_trigger)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(workflow_id),
        status="online",
        event_types='["case_created", "case_updated"]',
    )
    payload = _payload(result)
    assert "updated successfully" in payload["message"]
    assert captured_update["workflow_id"] == workflow_id
    assert captured_update["status"] == "online"
    assert captured_update["event_types"] == ["case_created", "case_updated"]
    assert captured_update["tag_filters"] is None
    assert captured_update["create_missing_tags"] is True


# ---------------------------------------------------------------------------
# Workflow tag tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_workflow_tags(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    tags = [
        SimpleNamespace(
            id=uuid.uuid4(),
            name="Critical",
            ref="critical",
            color="#ff0000",
        )
    ]

    async def _list_tags():
        return tags

    tag_service = SimpleNamespace(list_tags=_list_tags)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "TagsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(tag_service)),
    )

    result = await _tool(mcp_server.list_workflow_tags)(workspace_id=str(uuid.uuid4()))
    payload = _payload(result)
    assert payload["items"] == [
        {
            "id": str(tags[0].id),
            "name": "Critical",
            "ref": "critical",
            "color": "#ff0000",
        }
    ]


@pytest.mark.anyio
async def test_update_workflow_tag(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    tag = SimpleNamespace(
        id=uuid.uuid4(),
        name="Old name",
        ref="old-name",
        color="#111111",
    )
    captured: dict[str, Any] = {}

    async def _get_tag_by_ref_or_id(tag_id):
        captured["tag_id"] = tag_id
        return tag

    async def _update_tag(_tag, params):
        captured["name"] = params.name
        captured["color"] = params.color
        return SimpleNamespace(
            id=tag.id,
            name=params.name or tag.name,
            ref="new-name",
            color=params.color or tag.color,
        )

    tag_service = SimpleNamespace(
        get_tag_by_ref_or_id=_get_tag_by_ref_or_id,
        update_tag=_update_tag,
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "TagsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(tag_service)),
    )

    result = await _tool(mcp_server.update_workflow_tag)(
        workspace_id=str(uuid.uuid4()),
        tag_id="old-name",
        name="New name",
        color="#222222",
    )
    payload = _payload(result)
    assert captured == {
        "tag_id": "old-name",
        "name": "New name",
        "color": "#222222",
    }
    assert payload["ref"] == "new-name"
    assert payload["color"] == "#222222"


@pytest.mark.anyio
async def test_add_workflow_tag(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    workflow_id = uuid.uuid4()
    tag_id = uuid.uuid4()
    captured: dict[str, Any] = {}

    async def _add_workflow_tag(wf_id, parsed_tag_id):
        captured["workflow_id"] = wf_id
        captured["tag_id"] = parsed_tag_id

    tag_service = SimpleNamespace(add_workflow_tag=_add_workflow_tag)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "WorkflowTagsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(tag_service)),
    )

    result = await _tool(mcp_server.add_workflow_tag)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(workflow_id),
        tag_id=str(tag_id),
    )
    payload = _payload(result)
    assert captured == {"workflow_id": workflow_id, "tag_id": tag_id}
    assert "added to workflow" in payload["message"]


# ---------------------------------------------------------------------------
# Case tag tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_case_tags(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    tags = [
        SimpleNamespace(
            id=uuid.uuid4(),
            name="Malware",
            ref="malware",
            color="#ff8800",
        )
    ]

    async def _list_workspace_tags():
        return tags

    tag_service = SimpleNamespace(list_workspace_tags=_list_workspace_tags)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTagsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(tag_service)),
    )

    result = await _tool(mcp_server.list_case_tags)(workspace_id=str(uuid.uuid4()))
    payload = _payload(result)
    assert payload["items"][0]["ref"] == "malware"
    assert payload["items"][0]["color"] == "#ff8800"


@pytest.mark.anyio
async def test_update_case_tag(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    tag = SimpleNamespace(
        id=uuid.uuid4(),
        name="Malware",
        ref="malware",
        color="#ff8800",
    )
    captured: dict[str, Any] = {}

    async def _get_tag_by_ref_or_id(tag_id):
        captured["tag_id"] = tag_id
        return tag

    async def _update_tag(_tag, params):
        captured["name"] = params.name
        captured["color"] = params.color
        return SimpleNamespace(
            id=tag.id,
            name=params.name or tag.name,
            ref="incident-response",
            color=params.color or tag.color,
        )

    tag_service = SimpleNamespace(
        get_tag_by_ref_or_id=_get_tag_by_ref_or_id,
        update_tag=_update_tag,
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTagsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(tag_service)),
    )

    result = await _tool(mcp_server.update_case_tag)(
        workspace_id=str(uuid.uuid4()),
        tag_id="malware",
        name="Incident response",
        color="#123456",
    )
    payload = _payload(result)
    assert captured["tag_id"] == "malware"
    assert payload["ref"] == "incident-response"
    assert payload["color"] == "#123456"


@pytest.mark.anyio
async def test_add_case_tag(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    tag = SimpleNamespace(
        id=uuid.uuid4(),
        name="Escalated",
        ref="escalated",
        color="#00aa00",
    )
    captured: dict[str, Any] = {}

    async def _add_case_tag(parsed_case_id, tag_identifier):
        captured["case_id"] = parsed_case_id
        captured["tag_identifier"] = tag_identifier
        return tag

    tag_service = SimpleNamespace(add_case_tag=_add_case_tag)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTagsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(tag_service)),
    )

    result = await _tool(mcp_server.add_case_tag)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        tag_identifier="Escalated",
    )
    payload = _payload(result)
    assert captured == {"case_id": case_id, "tag_identifier": "Escalated"}
    assert payload["ref"] == "escalated"


@pytest.mark.anyio
async def test_remove_case_tag(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    captured: dict[str, Any] = {}

    async def _remove_case_tag(parsed_case_id, tag_identifier):
        captured["case_id"] = parsed_case_id
        captured["tag_identifier"] = tag_identifier

    tag_service = SimpleNamespace(remove_case_tag=_remove_case_tag)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTagsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(tag_service)),
    )

    result = await _tool(mcp_server.remove_case_tag)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        tag_identifier="escalated",
    )
    payload = _payload(result)
    assert captured == {"case_id": case_id, "tag_identifier": "escalated"}
    assert "removed from case" in payload["message"]


# ---------------------------------------------------------------------------
# Case field tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_case_fields(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    columns = [
        {
            "name": "case_id",
            "type": "UUID",
            "nullable": False,
            "default": None,
            "comment": "Case UUID",
        },
        {
            "name": "status_reason",
            "type": "TEXT",
            "nullable": True,
            "default": None,
            "comment": "Reason for the current status",
        },
        {
            "name": "severity_band",
            "type": "TEXT",
            "nullable": True,
            "default": None,
            "comment": "Severity band",
        },
    ]
    field_schema = {
        "status_reason": {"type": "TEXT", "kind": "LONG_TEXT"},
        "severity_band": {"type": "SELECT", "options": ["low", "high"]},
    }

    async def _list_fields():
        return columns

    async def _get_field_schema():
        return field_schema

    field_service = SimpleNamespace(
        list_fields=_list_fields,
        get_field_schema=_get_field_schema,
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseFieldsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(field_service)),
    )

    result = await _tool(mcp_server.list_case_fields)(workspace_id=str(uuid.uuid4()))
    payload = _payload(result)
    assert payload["items"][0]["id"] == "case_id"
    assert payload["items"][0]["type"] == "UUID"
    assert payload["items"][0]["reserved"] is True
    assert payload["items"][1]["id"] == "status_reason"
    assert payload["items"][1]["type"] == "TEXT"
    assert payload["items"][1]["kind"] == "LONG_TEXT"
    assert payload["items"][2]["type"] == "SELECT"
    assert payload["items"][2]["options"] == ["low", "high"]


@pytest.mark.anyio
async def test_create_case_field_parses_type_and_options(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    captured: dict[str, Any] = {}

    async def _create_field(params):
        captured["name"] = params.name
        captured["type"] = params.type
        captured["options"] = params.options

    field_service = SimpleNamespace(create_field=_create_field)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseFieldsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(field_service)),
    )

    result = await _tool(mcp_server.create_case_field)(
        workspace_id=str(uuid.uuid4()),
        name="severity_band",
        type="SELECT",
        options='["low","medium","high"]',
    )
    payload = _payload(result)
    assert captured["name"] == "severity_band"
    assert str(captured["type"]) == "SELECT"
    assert captured["options"] == ["low", "medium", "high"]
    assert "created successfully" in payload["message"]


@pytest.mark.anyio
async def test_create_case_field_parses_kind(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    captured: dict[str, Any] = {}

    async def _create_field(params):
        captured["name"] = params.name
        captured["type"] = params.type
        captured["kind"] = params.kind

    field_service = SimpleNamespace(create_field=_create_field)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseFieldsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(field_service)),
    )

    result = await _tool(mcp_server.create_case_field)(
        workspace_id=str(uuid.uuid4()),
        name="details",
        type="TEXT",
        kind="LONG_TEXT",
    )
    payload = _payload(result)
    assert captured["name"] == "details"
    assert str(captured["type"]) == "TEXT"
    assert captured["kind"].value == "LONG_TEXT"
    assert "created successfully" in payload["message"]


@pytest.mark.anyio
async def test_create_case_field_rejects_invalid_kind_pair(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    field_service = SimpleNamespace(create_field=lambda _params: None)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseFieldsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(field_service)),
    )

    with pytest.raises(ToolError, match="Case field kind LONG_TEXT requires type TEXT"):
        await _tool(mcp_server.create_case_field)(
            workspace_id=str(uuid.uuid4()),
            name="bad_details",
            type="INTEGER",
            kind="LONG_TEXT",
        )


@pytest.mark.anyio
async def test_update_case_field_parses_type_and_options(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    captured: dict[str, Any] = {}

    async def _update_field(field_id, params):
        captured["field_id"] = field_id
        captured["name"] = params.name
        captured["type"] = params.type
        captured["options"] = params.options

    field_service = SimpleNamespace(update_field=_update_field)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseFieldsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(field_service)),
    )

    result = await _tool(mcp_server.update_case_field)(
        workspace_id=str(uuid.uuid4()),
        field_id="severity_band",
        name="priority_band",
        type="MULTI_SELECT",
        options='["p1","p2"]',
    )
    payload = _payload(result)
    assert captured["field_id"] == "severity_band"
    assert captured["name"] == "priority_band"
    assert str(captured["type"]) == "MULTI_SELECT"
    assert captured["options"] == ["p1", "p2"]
    assert "updated successfully" in payload["message"]


@pytest.mark.anyio
async def test_delete_case_field(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    captured: dict[str, Any] = {}

    async def _delete_field(field_id):
        captured["field_id"] = field_id

    field_service = SimpleNamespace(delete_field=_delete_field)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseFieldsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(field_service)),
    )

    result = await _tool(mcp_server.delete_case_field)(
        workspace_id=str(uuid.uuid4()),
        field_id="severity_band",
    )
    payload = _payload(result)
    assert captured["field_id"] == "severity_band"
    assert "deleted successfully" in payload["message"]


# ---------------------------------------------------------------------------
# Case CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_cases(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    now = datetime.now(UTC)

    from tracecat.pagination import CursorPaginatedResponse

    list_result = CursorPaginatedResponse(
        items=[
            {
                "id": str(case_id),
                "short_id": "CASE-0001",
                "created_at": str(now),
                "updated_at": str(now),
                "summary": "Suspicious login",
                "status": "new",
                "priority": "high",
                "severity": "medium",
                "assignee": None,
                "tags": [],
                "dropdown_values": [],
                "num_tasks_completed": 0,
                "num_tasks_total": 2,
            }
        ],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
    )

    async def _list_cases(**kwargs):
        return list_result

    cases_service = SimpleNamespace(list_cases=_list_cases)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.list_cases)(workspace_id=str(uuid.uuid4()))
    payload = _payload(result)
    assert len(payload["items"]) == 1
    assert payload["items"][0]["summary"] == "Suspicious login"
    assert payload["has_more"] is False


@pytest.mark.anyio
async def test_search_cases(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    now = datetime.now(UTC)

    from tracecat.pagination import CursorPaginatedResponse

    search_result = CursorPaginatedResponse(
        items=[
            {
                "id": str(case_id),
                "short_id": "CASE-0042",
                "created_at": str(now),
                "updated_at": str(now),
                "summary": "Phishing alert",
                "status": "in_progress",
                "priority": "critical",
                "severity": "high",
                "assignee": None,
                "tags": [],
                "dropdown_values": [],
                "num_tasks_completed": 1,
                "num_tasks_total": 3,
            }
        ],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
    )
    captured: dict[str, Any] = {}

    async def _search_cases(params, **kwargs):
        captured.update(kwargs)
        return search_result

    cases_service = SimpleNamespace(search_cases=_search_cases)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.search_cases)(
        workspace_id=str(uuid.uuid4()),
        status="new,in_progress",
        priority="critical",
        search_term="phishing",
    )
    payload = _payload(result)
    assert len(payload["items"]) == 1
    assert payload["items"][0]["summary"] == "Phishing alert"
    assert captured["search_term"] == "phishing"
    # Verify enum parsing for comma-separated values
    assert len(captured["status"]) == 2
    assert len(captured["priority"]) == 1


@pytest.mark.anyio
async def test_get_case(monkeypatch):
    ws_id = uuid.uuid4()

    async def _resolve(_workspace_id):
        return ws_id, SimpleNamespace()

    case_id = uuid.uuid4()
    now = datetime.now(UTC)
    case = SimpleNamespace(
        id=case_id,
        short_id="CASE-0001",
        created_at=now,
        updated_at=now,
        summary="Suspicious login",
        status=SimpleNamespace(value="new"),
        priority=SimpleNamespace(value="high"),
        severity=SimpleNamespace(value="medium"),
        description="Detailed description",
        assignee=None,
        payload={"key": "value"},
        tags=[],
    )

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _get_fields(c):
        return {"my_field": "hello"}

    async def _list_fields():
        return [
            {
                "name": "my_field",
                "type": "TEXT",
                "nullable": True,
                "default": None,
                "comment": None,
            }
        ]

    async def _get_field_schema():
        return {"my_field": {"type": "TEXT"}}

    fields_svc = SimpleNamespace(
        get_fields=_get_fields,
        list_fields=_list_fields,
        get_field_schema=_get_field_schema,
    )

    async def _has_entitlement(_ent):
        return False

    cases_service = SimpleNamespace(
        get_case=_get_case,
        fields=fields_svc,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseDropdownValuesService",
        lambda session, role: SimpleNamespace(has_entitlement=_has_entitlement),
    )

    result = await _tool(mcp_server.get_case)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
    )
    payload = _payload(result)
    assert payload["id"] == str(case_id)
    assert payload["summary"] == "Suspicious login"
    assert payload["description"] == "Detailed description"
    assert payload["payload"] == {"key": "value"}
    assert len(payload["fields"]) == 1
    assert payload["fields"][0]["id"] == "my_field"
    assert payload["fields"][0]["value"] == "hello"


@pytest.mark.anyio
async def test_get_case_not_found(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _get_case(parsed_id, **kwargs):
        return None

    cases_service = SimpleNamespace(get_case=_get_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    with pytest.raises(ToolError, match="not found"):
        await _tool(mcp_server.get_case)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(uuid.uuid4()),
        )


@pytest.mark.anyio
async def test_create_case(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    captured: dict[str, Any] = {}

    async def _create_case(params):
        captured["summary"] = params.summary
        captured["status"] = params.status
        captured["priority"] = params.priority
        captured["severity"] = params.severity
        captured["description"] = params.description
        captured["fields"] = params.fields
        return SimpleNamespace(id=case_id, short_id="CASE-0005")

    cases_service = SimpleNamespace(create_case=_create_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.create_case)(
        workspace_id=str(uuid.uuid4()),
        summary="New incident",
        description="Something happened",
        status="new",
        priority="high",
        severity="medium",
        fields='{"my_field": "val"}',
    )
    payload = _payload(result)
    assert payload["id"] == str(case_id)
    assert payload["short_id"] == "CASE-0005"
    assert "created successfully" in payload["message"]
    assert captured["summary"] == "New incident"
    assert captured["fields"] == {"my_field": "val"}


@pytest.mark.anyio
async def test_create_case_with_tags(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    added_tags: list[tuple[str, bool]] = []

    async def _create_case(params):
        return SimpleNamespace(id=case_id, short_id="CASE-0006")

    async def _add_case_tag(cid, tag, *, create_if_missing=False):
        added_tags.append((tag, create_if_missing))

    tags_svc = SimpleNamespace(add_case_tag=_add_case_tag)
    cases_service = SimpleNamespace(create_case=_create_case, tags=tags_svc)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.create_case)(
        workspace_id=str(uuid.uuid4()),
        summary="Tagged incident",
        description="With tags",
        status="new",
        priority="high",
        severity="medium",
        tags="malware,phishing",
        create_missing_tags=True,
    )
    payload = _payload(result)
    assert payload["id"] == str(case_id)
    assert "created successfully" in payload["message"]
    assert len(added_tags) == 2
    assert added_tags[0] == ("malware", True)
    assert added_tags[1] == ("phishing", True)


@pytest.mark.anyio
async def test_update_case(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)
    captured: dict[str, Any] = {}

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _update_case(c, params):
        captured["summary"] = params.summary
        captured["status"] = params.status
        return c

    cases_service = SimpleNamespace(get_case=_get_case, update_case=_update_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.update_case)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        summary="Updated summary",
        status="in_progress",
    )
    payload = _payload(result)
    assert "updated successfully" in payload["message"]
    assert captured["summary"] == "Updated summary"
    assert str(captured["status"]) == "in_progress"


@pytest.mark.anyio
async def test_update_case_with_tags(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)
    removed_tags: list[str] = []
    added_tags: list[tuple[str, bool]] = []

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _update_case(c, params):
        return c

    async def _list_tags_for_case(cid):
        return [SimpleNamespace(ref="old-tag")]

    async def _remove_case_tag(cid, ref):
        removed_tags.append(ref)

    async def _add_case_tag(cid, tag, *, create_if_missing=False):
        added_tags.append((tag, create_if_missing))

    tags_svc = SimpleNamespace(
        list_tags_for_case=_list_tags_for_case,
        remove_case_tag=_remove_case_tag,
        add_case_tag=_add_case_tag,
    )
    cases_service = SimpleNamespace(
        get_case=_get_case, update_case=_update_case, tags=tags_svc
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.update_case)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        tags="new-tag-1,new-tag-2",
        create_missing_tags=True,
    )
    payload = _payload(result)
    assert "updated successfully" in payload["message"]
    assert removed_tags == ["old-tag"]
    assert len(added_tags) == 2
    assert added_tags[0] == ("new-tag-1", True)
    assert added_tags[1] == ("new-tag-2", True)


@pytest.mark.anyio
async def test_update_case_not_found(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _get_case(parsed_id, **kwargs):
        return None

    cases_service = SimpleNamespace(get_case=_get_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    with pytest.raises(ToolError, match="not found"):
        await _tool(mcp_server.update_case)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(uuid.uuid4()),
            summary="Won't work",
        )


@pytest.mark.anyio
async def test_delete_case(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)
    deleted = []

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _delete_case(c):
        deleted.append(c.id)

    cases_service = SimpleNamespace(get_case=_get_case, delete_case=_delete_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.delete_case)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
    )
    payload = _payload(result)
    assert "deleted successfully" in payload["message"]
    assert deleted == [case_id]


@pytest.mark.anyio
async def test_delete_case_not_found(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _get_case(parsed_id, **kwargs):
        return None

    cases_service = SimpleNamespace(get_case=_get_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    with pytest.raises(ToolError, match="not found"):
        await _tool(mcp_server.delete_case)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# Case comments tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_case_comments(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    comment_id = uuid.uuid4()
    now = datetime.now(UTC)
    case = SimpleNamespace(id=case_id)

    from tracecat.cases.schemas import CaseCommentRead

    comments = [
        CaseCommentRead(
            id=comment_id,
            created_at=now,
            updated_at=now,
            content="This looks suspicious",
            parent_id=None,
            workflow=None,
            user=None,
            last_edited_at=None,
            deleted_at=None,
            is_deleted=False,
        )
    ]

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _list_comments(c):
        return comments

    cases_service = SimpleNamespace(
        get_case=_get_case,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseCommentsService",
        lambda session, role: SimpleNamespace(list_comments=_list_comments),
    )

    result = await _tool(mcp_server.list_case_comments)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
    )
    payload = _payload(result)
    assert len(payload) == 1
    assert payload[0]["content"] == "This looks suspicious"
    assert payload[0]["id"] == str(comment_id)


@pytest.mark.anyio
async def test_list_case_comment_threads(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    comment_id = uuid.uuid4()
    reply_id = uuid.uuid4()
    now = datetime.now(UTC)
    case = SimpleNamespace(id=case_id)

    from tracecat.cases.schemas import CaseCommentRead, CaseCommentThreadRead

    root_comment = CaseCommentRead(
        id=comment_id,
        created_at=now,
        updated_at=now,
        content="Root comment",
        parent_id=None,
        workflow=None,
        user=None,
    )
    reply = CaseCommentRead(
        id=reply_id,
        created_at=now,
        updated_at=now,
        content="Reply",
        parent_id=comment_id,
        workflow=None,
        user=None,
    )
    threads = [
        CaseCommentThreadRead(
            comment=root_comment,
            replies=[reply],
            reply_count=1,
            last_activity_at=now,
        )
    ]

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _list_comment_threads(c):
        return threads

    cases_service = SimpleNamespace(
        get_case=_get_case,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseCommentsService",
        lambda session, role: SimpleNamespace(
            list_comment_threads=_list_comment_threads
        ),
    )

    result = await _tool(mcp_server.list_case_comment_threads)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
    )
    payload = _payload(result)
    assert len(payload) == 1
    assert payload[0]["comment"]["content"] == "Root comment"
    assert payload[0]["reply_count"] == 1
    assert len(payload[0]["replies"]) == 1


@pytest.mark.anyio
async def test_create_case_comment(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)
    captured: dict[str, Any] = {}

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _create_comment(c, params):
        captured["content"] = params.content
        captured["parent_id"] = params.parent_id

    cases_service = SimpleNamespace(
        get_case=_get_case,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseCommentsService",
        lambda session, role: SimpleNamespace(create_comment=_create_comment),
    )

    result = await _tool(mcp_server.create_case_comment)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        content="Needs escalation",
    )
    payload = _payload(result)
    assert "created successfully" in payload["message"]
    assert captured["content"] == "Needs escalation"
    assert captured["parent_id"] is None


@pytest.mark.anyio
async def test_create_case_comment_reply(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)
    captured: dict[str, Any] = {}

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _create_comment(c, params):
        captured["content"] = params.content
        captured["parent_id"] = params.parent_id

    cases_service = SimpleNamespace(
        get_case=_get_case,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseCommentsService",
        lambda session, role: SimpleNamespace(create_comment=_create_comment),
    )

    result = await _tool(mcp_server.create_case_comment)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        content="Replying here",
        parent_id=str(parent_id),
    )
    payload = _payload(result)
    assert "created successfully" in payload["message"]
    assert captured["parent_id"] == parent_id


@pytest.mark.anyio
async def test_update_case_comment(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    comment_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)
    comment = SimpleNamespace(id=comment_id)
    captured: dict[str, Any] = {}

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _get_comment_in_case(cid, comid):
        return comment

    async def _update_comment(c, params):
        captured["content"] = params.content

    cases_service = SimpleNamespace(
        get_case=_get_case,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseCommentsService",
        lambda session, role: SimpleNamespace(
            get_comment_in_case=_get_comment_in_case,
            update_comment=_update_comment,
        ),
    )

    result = await _tool(mcp_server.update_case_comment)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        comment_id=str(comment_id),
        content="Updated text",
    )
    payload = _payload(result)
    assert "updated successfully" in payload["message"]
    assert captured["content"] == "Updated text"


@pytest.mark.anyio
async def test_update_case_comment_not_found(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _get_comment_in_case(cid, comid):
        return None

    cases_service = SimpleNamespace(
        get_case=_get_case,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseCommentsService",
        lambda session, role: SimpleNamespace(
            get_comment_in_case=_get_comment_in_case,
        ),
    )

    with pytest.raises(ToolError, match="not found"):
        await _tool(mcp_server.update_case_comment)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(case_id),
            comment_id=str(uuid.uuid4()),
            content="Won't work",
        )


@pytest.mark.anyio
async def test_delete_case_comment(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    comment_id = uuid.uuid4()
    case = SimpleNamespace(id=case_id)
    comment = SimpleNamespace(id=comment_id)
    deleted = []

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _get_comment_in_case(cid, comid):
        return comment

    async def _delete_comment(c):
        deleted.append(c.id)

    cases_service = SimpleNamespace(
        get_case=_get_case,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "CaseCommentsService",
        lambda session, role: SimpleNamespace(
            get_comment_in_case=_get_comment_in_case,
            delete_comment=_delete_comment,
        ),
    )

    result = await _tool(mcp_server.delete_case_comment)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        comment_id=str(comment_id),
    )
    payload = _payload(result)
    assert "deleted successfully" in payload["message"]
    assert deleted == [comment_id]


# ---------------------------------------------------------------------------
# Case tasks tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_case_tasks(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(UTC)

    tasks = [
        SimpleNamespace(
            id=task_id,
            created_at=now,
            updated_at=now,
            case_id=case_id,
            title="Investigate source IP",
            description="Check threat intel",
            priority=SimpleNamespace(value="high"),
            status=SimpleNamespace(value="todo"),
            assignee=None,
            workflow_id=None,
            default_trigger_values=None,
        )
    ]

    async def _list_tasks(parsed_case_id):
        return tasks

    task_service = SimpleNamespace(list_tasks=_list_tasks)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    result = await _tool(mcp_server.list_case_tasks)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
    )
    payload = _payload(result)
    assert len(payload) == 1
    assert payload[0]["title"] == "Investigate source IP"
    assert payload[0]["status"] == "todo"
    assert payload[0]["priority"] == "high"


@pytest.mark.anyio
async def test_get_case_task(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(UTC)

    task = SimpleNamespace(
        id=task_id,
        created_at=now,
        updated_at=now,
        case_id=case_id,
        title="Block IP",
        description=None,
        priority=SimpleNamespace(value="critical"),
        status=SimpleNamespace(value="in_progress"),
        assignee=None,
        workflow_id=None,
        default_trigger_values=None,
    )

    async def _get_task(parsed_task_id):
        return task

    task_service = SimpleNamespace(get_task=_get_task)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    result = await _tool(mcp_server.get_case_task)(
        workspace_id=str(uuid.uuid4()),
        task_id=str(task_id),
    )
    payload = _payload(result)
    assert payload["id"] == str(task_id)
    assert payload["title"] == "Block IP"
    assert payload["status"] == "in_progress"


@pytest.mark.anyio
async def test_create_case_task(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(UTC)
    captured: dict[str, Any] = {}

    async def _create_task(parsed_case_id, params):
        captured["case_id"] = parsed_case_id
        captured["title"] = params.title
        captured["priority"] = params.priority
        captured["status"] = params.status
        return SimpleNamespace(
            id=task_id,
            created_at=now,
            updated_at=now,
            case_id=case_id,
            title=params.title,
            description=params.description,
            priority=params.priority,
            status=params.status,
            assignee=None,
            workflow_id=None,
            default_trigger_values=None,
        )

    task_service = SimpleNamespace(create_task=_create_task)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    result = await _tool(mcp_server.create_case_task)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        title="Isolate host",
        priority="high",
        status="todo",
    )
    payload = _payload(result)
    assert payload["id"] == str(task_id)
    assert payload["title"] == "Isolate host"
    assert captured["case_id"] == case_id
    assert str(captured["priority"]) == "high"
    assert str(captured["status"]) == "todo"


@pytest.mark.anyio
async def test_update_case_task(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(UTC)
    captured: dict[str, Any] = {}

    existing_task = SimpleNamespace(
        id=task_id,
        case_id=case_id,
    )

    async def _get_task(parsed_task_id):
        return existing_task

    async def _update_task(parsed_task_id, params):
        captured["task_id"] = parsed_task_id
        captured["status"] = params.status
        captured["title"] = params.title
        return SimpleNamespace(
            id=task_id,
            created_at=now,
            updated_at=now,
            case_id=case_id,
            title=params.title or "Isolate host",
            description=None,
            priority=SimpleNamespace(value="high"),
            status=params.status or SimpleNamespace(value="todo"),
            assignee=None,
            workflow_id=None,
            default_trigger_values=None,
        )

    task_service = SimpleNamespace(get_task=_get_task, update_task=_update_task)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    result = await _tool(mcp_server.update_case_task)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        task_id=str(task_id),
        status="completed",
        title="Host isolated",
    )
    payload = _payload(result)
    assert payload["id"] == str(task_id)
    assert captured["task_id"] == task_id
    assert str(captured["status"]) == "completed"


@pytest.mark.anyio
async def test_update_case_task_wrong_case(monkeypatch):
    """Updating a task that belongs to a different case should fail."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    task_id = uuid.uuid4()

    existing_task = SimpleNamespace(
        id=task_id,
        case_id=uuid.uuid4(),  # Different case
    )

    async def _get_task(parsed_task_id):
        return existing_task

    task_service = SimpleNamespace(get_task=_get_task)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    with pytest.raises(ToolError, match="Task not found in the specified case"):
        await _tool(mcp_server.update_case_task)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(uuid.uuid4()),
            task_id=str(task_id),
            status="completed",
        )


@pytest.mark.anyio
async def test_delete_case_task(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    task_id = uuid.uuid4()
    deleted = []

    existing_task = SimpleNamespace(id=task_id, case_id=case_id)

    async def _get_task(parsed_task_id):
        return existing_task

    async def _delete_task(parsed_task_id):
        deleted.append(parsed_task_id)

    task_service = SimpleNamespace(get_task=_get_task, delete_task=_delete_task)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    result = await _tool(mcp_server.delete_case_task)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        task_id=str(task_id),
    )
    payload = _payload(result)
    assert "deleted successfully" in payload["message"]
    assert deleted == [task_id]


@pytest.mark.anyio
async def test_run_case_task(monkeypatch):
    ws_id = uuid.uuid4()

    async def _resolve(_workspace_id):
        return ws_id, SimpleNamespace()

    case_id = uuid.uuid4()
    task_id = uuid.uuid4()
    wf_id = uuid.uuid4()

    existing_task = SimpleNamespace(
        id=task_id,
        case_id=case_id,
        workflow_id=wf_id,
        default_trigger_values={"env": "prod"},
    )

    async def _get_task(parsed_task_id):
        return existing_task

    task_service = SimpleNamespace(get_task=_get_task)

    # Mock the workflow definition fetch
    defn = SimpleNamespace(
        content={
            "title": "Test Workflow",
            "description": "A test workflow",
            "entrypoint": {"expects": None, "ref": "start"},
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "hello"},
                }
            ],
            "config": {"scheduler": "static"},
        },
        registry_lock=None,
    )

    class _FakeSession:
        async def execute(self, stmt):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: defn))

    exec_response = {
        "wf_id": wf_id,
        "wf_exec_id": "exec-123",
        "message": "Workflow started",
    }

    class _ExecService:
        async def create_workflow_execution_wait_for_start(self, **kwargs):
            return exec_response

    async def _connect(*, role):
        return _ExecService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )
    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(_FakeSession()),
    )
    monkeypatch.setattr(mcp_server.WorkflowExecutionsService, "connect", _connect)

    result = await _tool(mcp_server.run_case_task)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
        task_id=str(task_id),
        inputs='{"override_key": "override_val"}',
    )
    payload = _payload(result)
    assert payload["execution_id"] == "exec-123"
    assert payload["task_id"] == str(task_id)
    assert payload["workflow_id"] == str(wf_id)


@pytest.mark.anyio
async def test_run_case_task_no_workflow(monkeypatch):
    """Running a task without a workflow_id should fail."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    task_id = uuid.uuid4()

    existing_task = SimpleNamespace(
        id=task_id,
        case_id=case_id,
        workflow_id=None,
        default_trigger_values=None,
    )

    async def _get_task(parsed_task_id):
        return existing_task

    task_service = SimpleNamespace(get_task=_get_task)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    with pytest.raises(ToolError, match="no associated workflow"):
        await _tool(mcp_server.run_case_task)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(case_id),
            task_id=str(task_id),
        )


@pytest.mark.anyio
async def test_run_case_task_wrong_case(monkeypatch):
    """Running a task belonging to a different case should fail."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    task_id = uuid.uuid4()

    existing_task = SimpleNamespace(
        id=task_id,
        case_id=uuid.uuid4(),  # Different case
        workflow_id=uuid.uuid4(),
        default_trigger_values=None,
    )

    async def _get_task(parsed_task_id):
        return existing_task

    task_service = SimpleNamespace(get_task=_get_task)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CaseTasksService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(task_service)),
    )

    with pytest.raises(ToolError, match="Task not found in the specified case"):
        await _tool(mcp_server.run_case_task)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(uuid.uuid4()),
            task_id=str(task_id),
        )


# ---------------------------------------------------------------------------
# Case events tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_case_events(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    case_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    case = SimpleNamespace(id=case_id)

    db_events = [
        SimpleNamespace(
            type="case_created",
            user_id=user_id,
            created_at=now,
            data={"type": "case_created"},
        ),
    ]

    fake_users = [
        SimpleNamespace(
            id=user_id,
            email="analyst@example.com",
            first_name="Test",
            last_name="User",
            role="basic",
            settings={},
            is_active=True,
            is_superuser=False,
            is_verified=True,
        ),
    ]

    async def _get_case(parsed_id, **kwargs):
        return case

    async def _list_events(c):
        return db_events

    async def _search_users(*, session, user_ids):
        return fake_users

    events_svc = SimpleNamespace(list_events=_list_events)
    cases_service = SimpleNamespace(
        get_case=_get_case,
        events=events_svc,
        session=SimpleNamespace(),
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )
    monkeypatch.setattr(mcp_server, "search_users", _search_users)

    result = await _tool(mcp_server.list_case_events)(
        workspace_id=str(uuid.uuid4()),
        case_id=str(case_id),
    )
    payload = _payload(result)
    assert len(payload["events"]) == 1
    assert len(payload["users"]) == 1
    assert payload["users"][0]["email"] == "analyst@example.com"


@pytest.mark.anyio
async def test_list_case_events_not_found(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _get_case(parsed_id, **kwargs):
        return None

    cases_service = SimpleNamespace(get_case=_get_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    with pytest.raises(ToolError, match="not found"):
        await _tool(mcp_server.list_case_events)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------


def _make_tool_context(
    arguments: dict[str, object] | None = None,
    tool_name: str = "test_tool",
) -> MiddlewareContext[CallToolRequestParams]:
    """Build a minimal MiddlewareContext for tool-call middleware tests."""
    params = CallToolRequestParams(name=tool_name, arguments=arguments)
    return MiddlewareContext(
        message=params,
        method="tools/call",
    )


@pytest.mark.anyio
async def test_input_size_limit_allows_normal_input():
    from tracecat.mcp.middleware import MCPInputSizeLimitMiddleware

    mw = MCPInputSizeLimitMiddleware(max_bytes=1024)
    ctx = _make_tool_context(arguments={"yaml": "small string"})

    sentinel = object()

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        return cast(ToolResult, sentinel)

    result = await mw.on_call_tool(ctx, _call_next)
    assert result is sentinel


@pytest.mark.anyio
async def test_input_size_limit_rejects_oversized_input():
    from tracecat.mcp.middleware import MCPInputSizeLimitMiddleware

    # Use a very small limit so a normal string exceeds it
    mw = MCPInputSizeLimitMiddleware(max_bytes=10)
    big_string = "x" * 200
    ctx = _make_tool_context(arguments={"yaml": big_string})

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        raise AssertionError("should not be called")

    with pytest.raises(ToolError, match="exceeds maximum size"):
        await mw.on_call_tool(ctx, _call_next)


@pytest.mark.anyio
async def test_input_size_limit_counts_utf8_bytes():
    from tracecat.mcp.middleware import MCPInputSizeLimitMiddleware

    mw = MCPInputSizeLimitMiddleware(max_bytes=4)
    # 3 chars, 6 bytes in UTF-8.
    big_string = "ééé"
    ctx = _make_tool_context(arguments={"yaml": big_string})

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        raise AssertionError("should not be called")

    with pytest.raises(ToolError, match="exceeds maximum size"):
        await mw.on_call_tool(ctx, _call_next)


@pytest.mark.anyio
async def test_input_size_limit_passes_non_string_args():
    from tracecat.mcp.middleware import MCPInputSizeLimitMiddleware

    mw = MCPInputSizeLimitMiddleware(max_bytes=10)
    ctx = _make_tool_context(arguments={"count": 999999})

    sentinel = object()

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        return cast(ToolResult, sentinel)

    result = await mw.on_call_tool(ctx, _call_next)
    assert result is sentinel


@pytest.mark.anyio
async def test_timeout_middleware_allows_fast_calls():
    from tracecat.mcp.middleware import MCPTimeoutMiddleware

    mw = MCPTimeoutMiddleware(timeout_seconds=5)
    ctx = _make_tool_context()

    sentinel = object()

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        return cast(ToolResult, sentinel)

    result = await mw.on_call_tool(ctx, _call_next)
    assert result is sentinel


@pytest.mark.anyio
async def test_timeout_middleware_raises_on_slow_calls():
    from tracecat.mcp.middleware import MCPTimeoutMiddleware

    mw = MCPTimeoutMiddleware(timeout_seconds=0)
    ctx = _make_tool_context()

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        await asyncio.sleep(10)
        return ToolResult(content=None)

    with pytest.raises(ToolError, match="timed out"):
        await mw.on_call_tool(ctx, _call_next)


@pytest.mark.anyio
async def test_watchtower_middleware_blocks_when_telemetry_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.context import Context

    from tracecat.mcp.auth import MCPTokenIdentity
    from tracecat.mcp.middleware import WatchtowerMonitorMiddleware

    mw = WatchtowerMonitorMiddleware()
    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="test_tool", arguments=None),
        method="tools/call",
        fastmcp_context=cast(Context, SimpleNamespace(session_id="test-session-id")),
    )

    async def _get_watchtower_context(**_kwargs: object) -> tuple[object, str]:
        return object(), "blocked by policy"

    async def _record_watchtower_call(**_kwargs: object) -> None:
        raise RuntimeError("telemetry unavailable")

    ee_mod = ModuleType("tracecat_ee")
    watchtower_mod = ModuleType("tracecat_ee.watchtower")
    service_mod = ModuleType("tracecat_ee.watchtower.service")
    cast(Any, service_mod).get_watchtower_tool_call_context = _get_watchtower_context
    cast(Any, service_mod).record_watchtower_tool_call = _record_watchtower_call
    cast(Any, watchtower_mod).service = service_mod
    cast(Any, ee_mod).watchtower = watchtower_mod
    monkeypatch.setitem(sys.modules, "tracecat_ee", ee_mod)
    monkeypatch.setitem(sys.modules, "tracecat_ee.watchtower", watchtower_mod)
    monkeypatch.setitem(sys.modules, "tracecat_ee.watchtower.service", service_mod)

    monkeypatch.setattr(
        "tracecat.mcp.middleware._safe_get_token_identity",
        lambda: MCPTokenIdentity(client_id="client", email="user@example.com"),
    )

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        raise AssertionError("blocked calls should not execute downstream tools")

    with pytest.raises(ToolError, match="blocked by policy"):
        await mw.on_call_tool(ctx, _call_next)


@pytest.mark.anyio
async def test_get_mcp_client_id_extracts_email():
    from fastmcp.server.context import Context

    from tracecat.mcp.middleware import get_mcp_client_id

    token = SimpleNamespace(claims={"email": "user@example.com"})
    fastmcp_ctx = SimpleNamespace(get_access_token=lambda: token)
    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="t", arguments=None),
        fastmcp_context=cast(Context, fastmcp_ctx),
        method="tools/call",
    )
    assert get_mcp_client_id(ctx) == "user@example.com"


@pytest.mark.anyio
async def test_get_mcp_client_id_extracts_email_from_upstream_claims():
    from fastmcp.server.context import Context

    from tracecat.mcp.middleware import get_mcp_client_id

    token = SimpleNamespace(claims={"upstream_claims": {"email": " user@example.com "}})
    fastmcp_ctx = SimpleNamespace(get_access_token=lambda: token)
    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="t", arguments=None),
        fastmcp_context=cast(Context, fastmcp_ctx),
        method="tools/call",
    )
    assert get_mcp_client_id(ctx) == "user@example.com"


@pytest.mark.anyio
async def test_get_mcp_client_id_returns_anonymous_without_token():
    from fastmcp.server.context import Context

    from tracecat.mcp.middleware import get_mcp_client_id

    fastmcp_ctx = SimpleNamespace(get_access_token=lambda: None)
    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="t", arguments=None),
        fastmcp_context=cast(Context, fastmcp_ctx),
        method="tools/call",
    )
    assert get_mcp_client_id(ctx) == "anonymous"


@pytest.mark.anyio
async def test_get_mcp_client_id_falls_back_to_client_id_claim():
    from fastmcp.server.context import Context

    from tracecat.mcp.middleware import get_mcp_client_id

    token = SimpleNamespace(claims={"client_id": "tracecat-client"})
    fastmcp_ctx = SimpleNamespace(get_access_token=lambda: token)
    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="t", arguments=None),
        fastmcp_context=cast(Context, fastmcp_ctx),
        method="tools/call",
    )
    assert get_mcp_client_id(ctx) == "tracecat-client"


@pytest.mark.anyio
async def test_get_mcp_client_id_returns_anonymous_without_context():
    from tracecat.mcp.middleware import get_mcp_client_id

    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="t", arguments=None),
        fastmcp_context=None,
        method="tools/call",
    )
    assert get_mcp_client_id(ctx) == "anonymous"


def test_watchtower_status_mapping() -> None:
    from tracecat.mcp.middleware import _derive_tool_call_status

    assert _derive_tool_call_status("Tool timed out after 5 seconds") == "timeout"
    assert _derive_tool_call_status("Request blocked by admin policy") == "blocked"
    assert _derive_tool_call_status("Forbidden") == "rejected"
    assert _derive_tool_call_status("Unhandled failure") == "error"


def test_watchtower_workspace_resolution_prefers_claimed_scope() -> None:
    from tracecat.mcp.auth import MCPTokenIdentity
    from tracecat.mcp.middleware import _resolve_workspace_id

    scoped_workspace_id = uuid.uuid4()
    identity = MCPTokenIdentity(
        client_id="client",
        email="user@example.com",
        organization_ids=frozenset(),
        workspace_ids=frozenset({scoped_workspace_id}),
    )
    resolved = _resolve_workspace_id(
        identity=identity,
        arguments={"workspace_id": str(uuid.uuid4())},
    )
    assert resolved == scoped_workspace_id


def test_watchtower_workspace_resolution_uses_tool_argument() -> None:
    from tracecat.mcp.auth import MCPTokenIdentity
    from tracecat.mcp.middleware import _resolve_workspace_id

    workspace_id = uuid.uuid4()
    identity = MCPTokenIdentity(
        client_id="client",
        email="user@example.com",
        organization_ids=frozenset(),
        workspace_ids=frozenset(),
    )
    resolved = _resolve_workspace_id(
        identity=identity,
        arguments={"workspace_id": str(workspace_id)},
    )
    assert resolved == workspace_id


# ---------------------------------------------------------------------------
# Resource registration tests
# ---------------------------------------------------------------------------


def test_dsl_reference_resource_registered():
    """The DSL reference constant contains expected content."""
    text = mcp_server._DSL_REFERENCE_TEXT
    assert isinstance(text, str)
    assert "Tracecat Workflow DSL Reference" in text
    assert "FN." in text
    assert "TRIGGER" in text
    assert "ACTIONS" in text
    assert "SECRETS" in text


def test_dsl_reference_contains_all_fn_categories():
    """Verify the DSL reference covers all major FN function categories."""
    text = mcp_server._DSL_REFERENCE_TEXT
    for category in [
        "capitalize",  # String
        "is_equal",  # Comparison
        "regex_extract",  # Regex
        "flatten",  # Array
        "add",  # Math
        "merge",  # JSON/Dict
        "now",  # Time
        "to_base64",  # Encoding
        "hash_sha256",  # Hash
        "extract_cves",  # IOC
    ]:
        assert category in text, f"FN function {category!r} missing from DSL reference"


def test_domain_reference_resource_registered():
    """The domain reference constant contains expected enum values."""
    text = mcp_server._DOMAIN_REFERENCE_TEXT
    assert isinstance(text, str)
    assert "Domain Reference" in text
    # Case management enums
    for term in ["Priority", "Severity", "Status", "Task Status", "Case Event Types"]:
        assert term in text, f"Section {term!r} missing from domain reference"
    # Specific enum values
    for value in [
        "critical",
        "informational",
        "in_progress",
        "case_created",
        "dropdown_value_changed",
    ]:
        assert value in text, f"Enum value {value!r} missing from domain reference"
    # Table column types
    for col_type in ["TEXT", "INTEGER", "JSONB", "MULTI_SELECT"]:
        assert col_type in text, (
            f"Column type {col_type!r} missing from domain reference"
        )
    # Workflow control flow
    for term in ["join_strategy", "loop_strategy", "fail_strategy", "edge_type"]:
        assert term.replace("_", " ").title().replace(" ", " ") in text or any(
            kw in text.lower() for kw in [term]
        ), f"Control flow {term!r} missing from domain reference"
    # Workflow execution
    for value in ["manual", "scheduled", "webhook", "draft", "published"]:
        assert value in text, f"Execution value {value!r} missing from domain reference"


@pytest.mark.anyio
async def test_action_catalog_resource(monkeypatch):
    """The action catalog resource returns actions grouped by namespace."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _secret_inventory(_role):
        return {"slack": {"SLACK_BOT_TOKEN"}}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description

    entries = [
        (_IndexEntry("core", "http_request", "Make an HTTP request"), "platform"),
        (_IndexEntry("core.transform", "reshape", "Reshape data"), "platform"),
        (_IndexEntry("tools.slack", "post_message", "Post a message"), "platform"),
        (_IndexEntry("tools.slack", "list_channels", "List channels"), "platform"),
    ]

    indexed_action = SimpleNamespace(
        manifest=SimpleNamespace(),
        index_entry=SimpleNamespace(options=None),
    )

    class _RegistryService:
        async def list_actions_from_index(self, **_kwargs):
            return entries

        async def search_actions_from_index(self, _query, *, limit=None):
            _ = limit
            return entries

        async def get_action_from_index(self, _action_name):
            return indexed_action

        def aggregate_secrets_from_manifest(self, _manifest, _action_name):
            return []

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )

    result = await mcp_server._build_action_catalog(str(uuid.uuid4()))
    payload = _payload(result)
    assert payload["total_actions"] == 4
    assert "core" in payload["namespaces"]
    assert "tools.slack" in payload["namespaces"]
    assert payload["namespaces"]["core"]["action_count"] == 2
    assert payload["namespaces"]["tools.slack"]["action_count"] == 2
    slack_actions = payload["namespaces"]["tools.slack"]["actions"]
    action_names = [a["name"] for a in slack_actions]
    assert "tools.slack.post_message" in action_names


# ---------------------------------------------------------------------------
# list_actions browse mode tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_actions_browse_without_query(monkeypatch):
    """list_actions with no query should browse all actions."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _secret_inventory(_role):
        return {}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description

    entries = [
        (_IndexEntry("core", "http_request", "Make an HTTP request"), "platform"),
        (_IndexEntry("tools.slack", "post_message", "Post a message"), "platform"),
    ]
    indexed_action = SimpleNamespace(
        manifest=SimpleNamespace(),
        index_entry=SimpleNamespace(options=None),
    )

    class _RegistryService:
        async def list_actions_from_index(self, **_kwargs):
            return entries

        async def search_actions_from_index(self, _query, *, limit=None):
            _ = limit
            return entries

        async def get_action_from_index(self, _action_name):
            return indexed_action

        def aggregate_secrets_from_manifest(self, _manifest, _action_name):
            return []

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )

    result = await _tool(mcp_server.list_actions)(
        workspace_id=str(uuid.uuid4()),
    )
    payload = _payload(result)
    assert len(payload["items"]) == 2
    assert payload["items"][0]["action_name"] == "core.http_request"
    assert payload["items"][1]["action_name"] == "tools.slack.post_message"
    assert payload["has_more"] is False


@pytest.mark.anyio
async def test_list_actions_browse_with_namespace(monkeypatch):
    """list_actions with namespace but no query should filter by namespace."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _secret_inventory(_role):
        return {}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description

    entries = [
        (_IndexEntry("tools.slack", "post_message", "Post a message"), "platform"),
    ]
    indexed_action = SimpleNamespace(
        manifest=SimpleNamespace(),
        index_entry=SimpleNamespace(options=None),
    )

    captured_kwargs = {}

    class _RegistryService:
        async def list_actions_from_index(self, **kwargs):
            captured_kwargs.update(kwargs)
            return entries

        async def get_action_from_index(self, _action_name):
            return indexed_action

        def aggregate_secrets_from_manifest(self, _manifest, _action_name):
            return []

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )

    result = await _tool(mcp_server.list_actions)(
        workspace_id=str(uuid.uuid4()),
        namespace="tools.slack",
    )
    payload = _payload(result)
    assert len(payload["items"]) == 1
    assert captured_kwargs.get("namespace") == "tools.slack"


@pytest.mark.anyio
async def test_list_actions_paginates_and_rejects_mismatched_cursor(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _secret_inventory(_role):
        return {}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description

    entries = [
        (_IndexEntry("core", "one", "One"), "platform"),
        (_IndexEntry("core", "two", "Two"), "platform"),
        (_IndexEntry("core", "three", "Three"), "platform"),
    ]
    indexed_action = SimpleNamespace(
        manifest=SimpleNamespace(),
        index_entry=SimpleNamespace(options=None),
    )

    class _RegistryService:
        async def list_actions_from_index(self, **_kwargs):
            return entries

        async def search_actions_from_index(self, _query, *, limit=None):
            _ = limit
            return entries

        async def get_action_from_index(self, _action_name):
            return indexed_action

        def aggregate_secrets_from_manifest(self, _manifest, _action_name):
            return []

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )

    first_page = _payload(
        await _tool(mcp_server.list_actions)(
            workspace_id=str(uuid.uuid4()),
            limit=2,
        )
    )
    assert len(first_page["items"]) == 2
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] is not None

    second_page = _payload(
        await _tool(mcp_server.list_actions)(
            workspace_id=str(uuid.uuid4()),
            limit=2,
            cursor=first_page["next_cursor"],
        )
    )
    assert [item["action_name"] for item in second_page["items"]] == ["core.three"]

    with pytest.raises(ToolError, match="Cursor no longer matches current filters"):
        await _tool(mcp_server.list_actions)(
            workspace_id=str(uuid.uuid4()),
            query="two",
            cursor=first_page["next_cursor"],
        )


@pytest.mark.anyio
async def test_list_workspaces_applies_org_scope(monkeypatch):
    async def _list_workspaces_for_request() -> list[dict[str, str]]:
        return [{"id": str(uuid.uuid4()), "name": "SOC", "role": "member"}]

    monkeypatch.setattr(
        mcp_server,
        "list_workspaces_for_request",
        _list_workspaces_for_request,
    )
    result = await _tool(mcp_server.list_workspaces)()
    payload = _payload(result)

    assert len(payload["items"]) == 1


# ---------------------------------------------------------------------------
# Multitenant isolation tests
# ---------------------------------------------------------------------------

WS_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
WS_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")


@pytest.mark.anyio
async def test_list_workspaces_returns_multi_org_workspaces(monkeypatch):
    """list_workspaces faithfully returns workspaces spanning multiple orgs."""
    ws_list = [
        {"id": str(WS_A), "name": "SOC", "role": "admin"},
        {"id": str(WS_B), "name": "Engineering", "role": "member"},
    ]

    async def _list_workspaces_for_request() -> list[dict[str, str]]:
        return ws_list

    monkeypatch.setattr(
        mcp_server, "list_workspaces_for_request", _list_workspaces_for_request
    )
    result = await _tool(mcp_server.list_workspaces)()
    payload = _payload(result)

    assert len(payload["items"]) == 2
    returned_ids = {w["id"] for w in payload["items"]}
    assert str(WS_A) in returned_ids
    assert str(WS_B) in returned_ids


def test_tool_namespace_mapping_includes_get_agent_preset() -> None:
    assert mcp_server._TOOL_NAMESPACE_BY_NAME["get_agent_preset"] == "agents"
    assert mcp_server._TOOL_NAMESPACE_BY_NAME["update_agent_preset"] == "agents"


@pytest.mark.anyio
async def test_create_table_routes_to_correct_workspace(monkeypatch):
    """create_table passes the resolved workspace role to TablesService."""
    role_a = SimpleNamespace(workspace_id=WS_A)
    role_b = SimpleNamespace(workspace_id=WS_B)
    captured_roles: list[SimpleNamespace] = []

    async def _resolve(workspace_id: str):
        ws = uuid.UUID(workspace_id)
        role = role_a if ws == WS_A else role_b
        return ws, role

    async def _create_table(params):
        return SimpleNamespace(id=uuid.uuid4(), name=params.name)

    class _FakeTablesService:
        def __init__(self, role):
            captured_roles.append(role)

        async def create_table(self, params):
            return await _create_table(params)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.tables.service.TablesService.with_session",
        lambda role: _AsyncContext(_FakeTablesService(role)),
    )

    await _tool(mcp_server.create_table)(workspace_id=str(WS_A), name="iocs_soc")
    await _tool(mcp_server.create_table)(workspace_id=str(WS_B), name="iocs_eng")

    assert len(captured_roles) == 2
    assert captured_roles[0].workspace_id == WS_A
    assert captured_roles[1].workspace_id == WS_B


@pytest.mark.anyio
async def test_list_workflow_tree_paginates_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, SimpleNamespace()

    class _Item:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
            assert mode == "json"
            return self._payload

    class _FolderService:
        async def get_directory_items(
            self, path: str, order_by: str = "desc"
        ) -> list[_Item]:
            assert order_by == "desc"
            if path == "/":
                return [
                    _Item({"type": "folder", "path": "/a/", "name": "a"}),
                    _Item(
                        {
                            "type": "workflow",
                            "id": str(uuid.uuid4()),
                            "title": "Root workflow",
                            "alias": None,
                            "status": "offline",
                            "tags": [],
                        }
                    ),
                ]
            return []

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )

    first_page = _payload(
        await _tool(mcp_server.list_workflow_tree)(
            workspace_id=str(workspace_id),
            limit=1,
        )
    )
    assert len(first_page["items"]) == 1
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] is not None
    assert first_page["root_path"] == "/"


@pytest.mark.anyio
async def test_search_table_rows_returns_paginated_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    table_id = uuid.uuid4()

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, SimpleNamespace()

    captured: dict[str, Any] = {}

    class _TableService:
        async def get_table(self, parsed_table_id: uuid.UUID) -> SimpleNamespace:
            assert parsed_table_id == table_id
            return SimpleNamespace(id=table_id)

        async def search_rows(
            self,
            _table: Any,
            *,
            search_term: str | None = None,
            limit: int | None = None,
            cursor: str | None = None,
        ) -> Any:
            captured["search_term"] = search_term
            captured["limit"] = limit
            captured["cursor"] = cursor
            return mcp_server.MCPPaginatedResponse[dict[str, Any]](
                items=[{"city": "NYC"}],
                next_cursor="cursor-1",
                prev_cursor=None,
                has_more=True,
                has_previous=False,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.TablesService,
        "with_session",
        lambda role: _AsyncContext(_TableService()),
    )

    payload = _payload(
        await _tool(mcp_server.search_table_rows)(
            workspace_id=str(workspace_id),
            table_id=str(table_id),
            search_term="ny",
            limit=25,
            cursor="cursor-0",
        )
    )
    assert payload["items"] == [{"city": "NYC"}]
    assert payload["next_cursor"] == "cursor-1"
    assert captured == {"search_term": "ny", "limit": 25, "cursor": "cursor-0"}


@pytest.mark.anyio
async def test_list_workflow_executions_forwards_prev_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    workflow_id = mcp_server.WorkflowUUID.new_uuid4()
    start_time = datetime.now(UTC)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _ExecutionService:
        async def list_executions_paginated(
            self,
            *,
            pagination: Any,
            workflow_id: Any,
        ) -> SimpleNamespace:
            assert pagination.limit == 20
            assert pagination.cursor == "cursor-2"
            assert workflow_id == mcp_server.WorkflowUUID.new(str(workflow_id))
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        id="wf_example/exec_123",
                        run_id="run-123",
                        status=1,
                        start_time=start_time,
                        close_time=None,
                        typed_search_attributes=None,
                    )
                ],
                next_cursor="cursor-3",
                prev_cursor="cursor-1",
                has_more=True,
                has_previous=True,
            )

    async def _connect(*, role: Any) -> _ExecutionService:
        assert role is not None
        return _ExecutionService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server.WorkflowExecutionsService, "connect", _connect)

    payload = _payload(
        await _tool(mcp_server.list_workflow_executions)(
            workspace_id=str(workspace_id),
            workflow_id=str(workflow_id),
            cursor="cursor-2",
        )
    )
    assert payload["prev_cursor"] == "cursor-1"
    assert payload["has_previous"] is True
    assert payload["next_cursor"] == "cursor-3"


@pytest.mark.anyio
async def test_concurrent_workspace_calls_do_not_cross(monkeypatch):
    """Concurrent tool calls for different workspaces resolve independently."""
    resolved: dict[str, uuid.UUID] = {}

    async def _resolve(workspace_id: str):
        ws = uuid.UUID(workspace_id)
        await asyncio.sleep(0)
        resolved[workspace_id] = ws
        return ws, SimpleNamespace(workspace_id=ws)

    async def _create_table(params):
        return SimpleNamespace(id=uuid.uuid4(), name=params.name)

    class _FakeTablesService:
        def __init__(self, _role):
            pass

        async def create_table(self, params):
            return await _create_table(params)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.tables.service.TablesService.with_session",
        lambda role: _AsyncContext(_FakeTablesService(role)),
    )

    results = await asyncio.gather(
        _tool(mcp_server.create_table)(
            workspace_id=str(WS_A),
            name="table_a",
        ),
        _tool(mcp_server.create_table)(
            workspace_id=str(WS_B),
            name="table_b",
        ),
    )

    assert len(results) == 2
    assert resolved[str(WS_A)] == WS_A
    assert resolved[str(WS_B)] == WS_B


def test_import_csv_tool_removed():
    assert not hasattr(mcp_server, "import_csv")


@pytest.mark.anyio
async def test_export_csv_remote_returns_download_metadata(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    table_id = uuid.uuid4()
    uploaded: dict[str, Any] = {}

    class _FakeColumn:
        def __init__(self, name):
            self.name = name

    fake_table = SimpleNamespace(
        id=table_id,
        name="remote_table",
        columns=[_FakeColumn("city")],
    )

    class _TablesService:
        async def get_table(self, _table_id):
            return fake_table

        async def search_rows(self, _table, *, limit=1000, cursor=None):
            _ = limit, cursor
            return SimpleNamespace(
                items=[{"city": "NYC"}],
                has_more=False,
                next_cursor=None,
            )

    async def _upload_file(
        content: bytes, key: str, bucket: str, content_type: str | None = None
    ):
        uploaded["content"] = content
        uploaded["key"] = key
        uploaded["bucket"] = bucket
        uploaded["content_type"] = content_type

    async def _download_url(
        *,
        key: str,
        bucket: str,
        expiry: int | None = None,
        override_content_type: str | None = None,
    ):
        uploaded["download_args"] = {
            "key": key,
            "bucket": bucket,
            "expiry": expiry,
            "override_content_type": override_content_type,
        }
        return "https://example.test/table.csv"

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        TablesService,
        "with_session",
        lambda role: _AsyncContext(_TablesService()),
    )
    monkeypatch.setattr(mcp_server.blob, "upload_file", _upload_file)
    monkeypatch.setattr(
        mcp_server.blob, "generate_presigned_download_url", _download_url
    )

    payload = _payload(
        await _tool(mcp_server.export_csv)(
            workspace_id=str(uuid.uuid4()),
            table_id=str(table_id),
            ctx=_fake_ctx(session_id="csv-session"),
        )
    )
    lines = uploaded["content"].decode("utf-8").strip().splitlines()
    assert payload["download_url"] == "https://example.test/table.csv"
    assert payload["transport"] == "streamable-http"
    assert uploaded["content_type"] == "text/csv"
    assert "/mcp/table-csv/csv-session/" in uploaded["key"]
    assert uploaded["key"].count("/mcp/table-csv/") == 1
    assert lines == ["city", "NYC"]


@pytest.mark.anyio
async def test_export_csv_rejects_stdio_transport(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    table_id = uuid.uuid4()

    class _FakeColumn:
        def __init__(self, name):
            self.name = name

    fake_table = SimpleNamespace(
        id=table_id,
        name="collision_table",
        columns=[_FakeColumn("city")],
    )

    class _TablesService:
        async def get_table(self, _table_id):
            return fake_table

        async def search_rows(self, _table, *, limit=1000, cursor=None):
            _ = limit, cursor
            return SimpleNamespace(
                items=[{"city": "NYC"}], has_more=False, next_cursor=None
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        TablesService,
        "with_session",
        lambda role: _AsyncContext(_TablesService()),
    )

    with pytest.raises(
        ToolError, match="only supported for remote streamable-http MCP clients"
    ):
        await _tool(mcp_server.export_csv)(
            workspace_id=str(uuid.uuid4()),
            table_id=str(table_id),
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_list_agent_presets_returns_lightweight_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    now = datetime.now(UTC)
    presets = [
        SimpleNamespace(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name="Security triage",
            slug="security-triage",
            description="Investigate alerts",
            instructions="Very long system prompt",
            model_name="gpt-4o-mini",
            model_provider="openai",
            actions=["tools.alpha"],
            namespaces=["tools"],
            current_version_id=None,
            created_at=now,
            updated_at=now,
        )
    ]

    class _PresetService:
        async def list_presets(self) -> list[SimpleNamespace]:
            return presets

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = await _tool(mcp_server.list_agent_presets)(workspace_id=str(workspace_id))

    assert _payload(result) == {
        "items": [{"slug": "security-triage", "name": "Security triage"}],
        "next_cursor": None,
        "prev_cursor": None,
        "has_more": False,
        "has_previous": False,
    }


@pytest.mark.anyio
async def test_get_agent_preset_returns_full_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    now = datetime.now(UTC)
    preset = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Security triage",
        slug="security-triage",
        description="Investigate alerts",
        instructions="Very long system prompt",
        model_name="gpt-4o-mini",
        model_provider="openai",
        base_url=None,
        output_type=None,
        actions=["tools.alpha"],
        namespaces=["tools"],
        tool_approvals={"tools.alpha": False},
        mcp_integrations=[str(uuid.uuid4())],
        retries=3,
        enable_thinking=True,
        enable_internet_access=False,
        current_version_id=None,
        created_at=now,
        updated_at=now,
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _PresetService(_PresetReadBuilder):
        async def get_preset_by_slug(self, preset_slug: str) -> SimpleNamespace:
            assert preset_slug == "security-triage"
            return preset

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = await _tool(mcp_server.get_agent_preset)(
        workspace_id=str(workspace_id),
        preset_slug="security-triage",
    )

    payload = _payload(result)
    assert payload["slug"] == "security-triage"
    assert payload["instructions"] == "Very long system prompt"
    assert payload["actions"] == ["tools.alpha"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("last_stream_id", "expected_start_id"),
    [
        ("1717426372766-0", "1717426372766-0"),
        (None, "0-0"),
    ],
)
async def test_run_agent_preset_uses_session_stream_cursor(
    monkeypatch: pytest.MonkeyPatch,
    last_stream_id: str | None,
    expected_start_id: str,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    preset = SimpleNamespace(id=uuid.uuid4())
    version = SimpleNamespace(id=uuid.uuid4())
    session = SimpleNamespace(id=uuid.uuid4(), last_stream_id=last_stream_id)
    created_session_request: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _PresetService:
        async def get_preset_by_slug(self, _preset_slug: str) -> SimpleNamespace:
            return preset

        async def resolve_agent_preset_version(
            self,
            *,
            slug: str,
            preset_version: int | None = None,
        ) -> SimpleNamespace:
            assert slug == "triage"
            assert preset_version is None
            return version

    class _SessionService:
        async def create_session(self, create: Any) -> SimpleNamespace:
            created_session_request["value"] = create
            return session

        async def run_turn(self, _session_id: uuid.UUID, _request: Any) -> None:
            return None

    captured: dict[str, Any] = {}

    async def _collect(
        session_id: uuid.UUID,
        workspace_id_arg: uuid.UUID,
        timeout: float,
        last_id: str,
    ) -> str:
        captured["session_id"] = session_id
        captured["workspace_id"] = workspace_id_arg
        captured["timeout"] = timeout
        captured["last_id"] = last_id
        return "agent response"

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )
    monkeypatch.setattr(
        mcp_server.AgentSessionService,
        "with_session",
        lambda role: _AsyncContext(_SessionService()),
    )
    monkeypatch.setattr(mcp_server, "_collect_agent_response", _collect)

    result = await _tool(mcp_server.run_agent_preset)(
        workspace_id=str(workspace_id),
        preset_slug="triage",
        prompt="check alerts",
    )

    assert result == "agent response"
    assert created_session_request["value"].agent_preset_id == preset.id
    assert created_session_request["value"].agent_preset_version_id == version.id
    assert captured["session_id"] == session.id
    assert captured["workspace_id"] == workspace_id
    assert captured["timeout"] == 120
    assert captured["last_id"] == expected_start_id


@pytest.mark.anyio
async def test_list_integrations_returns_mcp_and_provider_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    current_user_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, user_id=current_user_id)
    oauth_integration = SimpleNamespace(
        provider_id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        user_id=current_user_id,
        status=SimpleNamespace(value="connected"),
    )
    other_user_integration = SimpleNamespace(
        provider_id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        user_id=uuid.uuid4(),
        status=SimpleNamespace(value="configured"),
    )
    mcp_integration_id = uuid.uuid4()
    mcp_integration = SimpleNamespace(
        id=mcp_integration_id,
        name="GitHub MCP",
        slug="github-mcp",
        description="GitHub tools",
        server_type="http",
        auth_type=MCPAuthType.NONE,
        oauth_integration_id=None,
        timeout=30,
    )
    custom_provider = SimpleNamespace(
        provider_id="custom-crm",
        name="Custom CRM",
        description="Internal CRM",
        grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SlackProvider:
        id = "slack"
        grant_type = OAuthGrantType.AUTHORIZATION_CODE
        metadata = SimpleNamespace(
            name="Slack",
            description="Slack integration",
            enabled=True,
            requires_config=False,
        )

    class _IntegrationService:
        async def list_integrations(self):
            return [other_user_integration, oauth_integration]

        async def list_mcp_integrations(self):
            return [mcp_integration]

        async def list_custom_providers(self):
            return [custom_provider]

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "all_providers", lambda: [_SlackProvider])
    monkeypatch.setattr(
        mcp_server.IntegrationService,
        "with_session",
        lambda role: _AsyncContext(_IntegrationService()),
    )

    result = await _tool(mcp_server.list_integrations)(workspace_id=str(workspace_id))

    payload = _payload(result)
    assert payload["mcp_integrations"][0]["id"] == str(mcp_integration_id)
    assert payload["mcp_integrations"][0]["attachable_to_agent_preset"] is True
    assert payload["oauth_providers"][0]["provider_id"] == "slack"
    assert payload["oauth_providers"][0]["integration_status"] == "connected"
    assert payload["oauth_providers"][1]["provider_id"] == "custom-crm"
    assert payload["oauth_providers"][1]["integration_status"] == "not_configured"
    assert (
        payload["truncation"]["collections"]["mcp_integrations"]["truncated"] is False
    )


@pytest.mark.anyio
async def test_get_workflow_authoring_context_truncates_embedded_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    async def _secret_inventory(_role: Any) -> dict[str, set[str]]:
        return {"alpha": {"TOKEN"}, "beta": {"TOKEN"}}

    class _IndexEntry:
        def __init__(self, namespace: str, name: str) -> None:
            self.namespace = namespace
            self.name = name

    indexed_action = SimpleNamespace(manifest=SimpleNamespace())

    class _RegistryService:
        async def get_action_from_index(self, _action_name: str) -> Any:
            return indexed_action

        async def search_actions_from_index(
            self, _query: str, limit: int = 20
        ) -> list[tuple[_IndexEntry, str]]:
            _ = limit
            return [
                (_IndexEntry("tools", "one"), "platform"),
                (_IndexEntry("tools", "two"), "platform"),
            ]

        def aggregate_secrets_from_manifest(
            self, _manifest: Any, action_name: str
        ) -> list[Any]:
            return [RegistrySecret(name=action_name, keys=["TOKEN"])]

    class _VariablesService:
        async def list_variables(self, environment: str) -> list[Any]:
            assert environment == "default"
            return [
                SimpleNamespace(
                    name="var_one", values={"k": "v"}, environment="default"
                ),
                SimpleNamespace(
                    name="var_two", values={"k": "v"}, environment="default"
                ),
            ]

    class _Tool:
        description = "desc"
        parameters_json_schema = {"type": "object", "properties": {}}

    async def _create_tool(*_args: Any) -> _Tool:
        return _Tool()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(mcp_server, "_MCP_EMBEDDED_COLLECTION_LIMIT", 1)
    monkeypatch.setattr(mcp_server, "create_tool_from_registry", _create_tool)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )
    monkeypatch.setattr(
        mcp_server.VariablesService,
        "with_session",
        lambda role: _AsyncContext(_VariablesService()),
    )

    payload = _payload(
        await _tool(mcp_server.get_workflow_authoring_context)(
            workspace_id=str(workspace_id),
            query="tools",
        )
    )
    assert len(payload["actions"]) == 1
    assert len(payload["variable_hints"]) == 1
    assert len(payload["secret_hints"]) == 1
    assert payload["truncation"]["collections"]["actions"]["truncated"] is True
    assert payload["truncation"]["collections"]["variable_hints"]["truncated"] is True
    assert payload["truncation"]["collections"]["secret_hints"]["truncated"] is True


@pytest.mark.anyio
async def test_get_agent_preset_authoring_context_includes_output_type_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    async def _secret_inventory(_role: Any) -> dict[str, set[str]]:
        return {"slack": {"TOKEN"}}

    async def _integrations_inventory(_role: Any) -> dict[str, Any]:
        return {"mcp_integrations": [], "oauth_providers": [], "notes": []}

    class _Model:
        def __init__(self, name: str, provider: str) -> None:
            self._payload = {
                "name": name,
                "provider": provider,
                "org_secret_name": provider,
                "secrets": {"required": ["API_KEY"]},
            }

        def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
            assert mode == "json"
            return self._payload

    class _AgentManagementService:
        async def list_models(self) -> dict[str, _Model]:
            return {"gpt-4o-mini": _Model("gpt-4o-mini", "openai")}

        async def get_default_model(self) -> str | None:
            return "gpt-4o-mini"

        async def get_providers_status(self) -> dict[str, bool]:
            return {"openai": True}

        async def get_workspace_providers_status(self) -> dict[str, bool]:
            return {"openai": False}

    class _VariablesService:
        async def list_variables(self, environment: str):
            assert environment == "default"
            return [
                SimpleNamespace(
                    name="splunk",
                    values={"base_url": "https://splunk.example.com"},
                    environment="default",
                )
            ]

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(
        mcp_server, "_build_integrations_inventory", _integrations_inventory
    )
    monkeypatch.setattr(
        mcp_server.AgentManagementService,
        "with_session",
        lambda role: _AsyncContext(_AgentManagementService()),
    )
    monkeypatch.setattr(
        mcp_server.VariablesService,
        "with_session",
        lambda role: _AsyncContext(_VariablesService()),
    )

    result = await _tool(mcp_server.get_agent_preset_authoring_context)(
        workspace_id=str(workspace_id)
    )

    payload = _payload(result)
    assert payload["default_model"] == "gpt-4o-mini"
    assert payload["models"][0]["provider"] == "openai"
    assert payload["agent_credentials"]["providers"][0]["provider"] == "openai"
    assert payload["agent_credentials"]["providers"][0]["configured_org"] is True
    assert payload["agent_credentials"]["providers"][0]["configured_workspace"] is False
    assert (
        payload["agent_credentials"]["providers"][0]["ready_for_agent_presets"] is False
    )
    assert payload["agent_credentials"]["default_model_workspace_ready"] is False
    assert payload["workspace_variables"][0]["name"] == "splunk"
    assert payload["workspace_secret_hints"][0]["name"] == "slack"
    assert "str" in payload["output_type_context"]["supported_literals"]
    assert payload["output_type_context"]["accepts_json_schema"] is True
    assert payload["output_type_context"]["examples"]["structured"]["type"] == "object"
    assert payload["truncation"]["collections"]["models"]["truncated"] is False


@pytest.mark.anyio
async def test_create_agent_preset_uses_default_model_and_passes_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    created: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AgentManagementService:
        async def get_default_model(self) -> str | None:
            return "gpt-4o-mini"

        async def get_model_config(self, model_name: str) -> SimpleNamespace:
            assert model_name == "gpt-4o-mini"
            return SimpleNamespace(name="gpt-4o-mini", provider="openai")

        async def check_workspace_provider_credentials(self, provider: str) -> bool:
            assert provider == "openai"
            return True

    class _PresetService(_PresetReadBuilder):
        async def create_preset(self, params: Any) -> SimpleNamespace:
            created["params"] = params
            now = datetime.now(UTC)
            return SimpleNamespace(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=params.name,
                slug=params.slug or "security-triage",
                description=params.description,
                instructions=params.instructions,
                model_name=params.model_name,
                model_provider=params.model_provider,
                base_url=params.base_url,
                output_type=params.output_type,
                actions=params.actions,
                namespaces=params.namespaces,
                tool_approvals=params.tool_approvals,
                mcp_integrations=params.mcp_integrations,
                retries=params.retries,
                enable_thinking=params.enable_thinking,
                enable_internet_access=params.enable_internet_access,
                current_version_id=None,
                created_at=now,
                updated_at=now,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentManagementService,
        "with_session",
        lambda role: _AsyncContext(_AgentManagementService()),
    )
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = await _tool(mcp_server.create_agent_preset)(
        workspace_id=str(workspace_id),
        name="Security triage",
        description="Investigate alerts",
        instructions="Summarize the incident.",
        output_type={"type": "object", "properties": {"summary": {"type": "string"}}},
        actions=["tools.slack.post_message"],
        namespaces=["tools.slack"],
        tool_approvals={"tools.slack.post_message": False},
        mcp_integration_ids=[str(uuid.uuid4())],
        retries=5,
        enable_thinking=False,
        enable_internet_access=True,
    )

    payload = _payload(result)
    params = created["params"]
    assert params.model_name == "gpt-4o-mini"
    assert params.model_provider == "openai"
    assert params.mcp_integrations is not None
    assert params.enable_thinking is False
    assert params.enable_internet_access is True
    assert payload["model_name"] == "gpt-4o-mini"
    assert payload["output_type"]["type"] == "object"
    assert payload["enable_thinking"] is False


@pytest.mark.anyio
async def test_update_agent_preset_updates_existing_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    updated: dict[str, Any] = {}
    now = datetime.now(UTC)
    preset = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Security triage",
        slug="security-triage",
        description="Investigate alerts",
        instructions="Original prompt",
        model_name="gpt-4o-mini",
        model_provider="openai",
        base_url=None,
        output_type=None,
        actions=["tools.alpha"],
        namespaces=["tools"],
        tool_approvals={"tools.alpha": False},
        mcp_integrations=[str(uuid.uuid4())],
        retries=3,
        enable_thinking=True,
        enable_internet_access=False,
        current_version_id=None,
        created_at=now,
        updated_at=now,
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _PresetService(_PresetReadBuilder):
        async def get_preset_by_slug(self, preset_slug: str) -> SimpleNamespace:
            assert preset_slug == "security-triage"
            return preset

        async def update_preset(
            self, current_preset: Any, params: Any
        ) -> SimpleNamespace:
            assert current_preset is preset
            updated["params"] = params
            updated_fields = {
                **preset.__dict__,
                "instructions": params.instructions,
                "actions": params.actions,
                "mcp_integrations": params.mcp_integrations,
                "retries": params.retries,
                "enable_thinking": params.enable_thinking,
                "enable_internet_access": params.enable_internet_access,
                "updated_at": datetime.now(UTC),
            }
            return SimpleNamespace(**updated_fields)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    integration_id = str(uuid.uuid4())
    result = await _tool(mcp_server.update_agent_preset)(
        workspace_id=str(workspace_id),
        preset_slug="security-triage",
        instructions="Updated prompt",
        actions=["tools.bravo"],
        mcp_integration_ids=[integration_id],
        retries=5,
        enable_thinking=False,
        enable_internet_access=True,
    )

    payload = _payload(result)
    params = updated["params"]
    assert params.instructions == "Updated prompt"
    assert params.actions == ["tools.bravo"]
    assert params.mcp_integrations == [integration_id]
    assert params.retries == 5
    assert params.enable_thinking is False
    assert params.enable_internet_access is True
    assert payload["instructions"] == "Updated prompt"
    assert payload["actions"] == ["tools.bravo"]
    assert payload["mcp_integrations"] == [integration_id]
    assert payload["retries"] == 5
    assert payload["enable_thinking"] is False
    assert payload["enable_internet_access"] is True


@pytest.mark.anyio
async def test_update_agent_preset_resolves_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    now = datetime.now(UTC)
    preset = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Security triage",
        slug="security-triage",
        description=None,
        instructions="Original prompt",
        model_name="gpt-4o-mini",
        model_provider="openai",
        base_url=None,
        output_type=None,
        actions=None,
        namespaces=None,
        tool_approvals=None,
        mcp_integrations=None,
        retries=3,
        enable_thinking=True,
        enable_internet_access=False,
        current_version_id=None,
        created_at=now,
        updated_at=now,
    )
    captured: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    async def _resolve_model(
        resolved_role: Any,
        *,
        model_name: str | None,
        model_provider: str | None,
    ) -> tuple[str, str]:
        assert resolved_role is role
        assert model_name == "gpt-5-mini"
        assert model_provider == "openai"
        return "gpt-5-mini", "openai"

    class _PresetService(_PresetReadBuilder):
        async def get_preset_by_slug(self, preset_slug: str) -> SimpleNamespace:
            assert preset_slug == "security-triage"
            return preset

        async def update_preset(
            self, current_preset: Any, params: Any
        ) -> SimpleNamespace:
            assert current_preset is preset
            captured["params"] = params
            updated_fields = {
                **preset.__dict__,
                "model_name": params.model_name,
                "model_provider": params.model_provider,
                "updated_at": datetime.now(UTC),
            }
            return SimpleNamespace(**updated_fields)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_resolve_agent_preset_model", _resolve_model)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = await _tool(mcp_server.update_agent_preset)(
        workspace_id=str(workspace_id),
        preset_slug="security-triage",
        model_name="gpt-5-mini",
        model_provider="openai",
    )

    payload = _payload(result)
    params = captured["params"]
    assert params.model_name == "gpt-5-mini"
    assert params.model_provider == "openai"
    assert payload["model_name"] == "gpt-5-mini"
    assert payload["model_provider"] == "openai"


@pytest.mark.anyio
async def test_update_agent_preset_requires_existing_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _PresetService:
        async def get_preset_by_slug(self, preset_slug: str) -> None:
            assert preset_slug == "missing-preset"
            return None

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    with pytest.raises(ToolError, match="Agent preset 'missing-preset' not found"):
        await _tool(mcp_server.update_agent_preset)(
            workspace_id=str(workspace_id),
            preset_slug="missing-preset",
            instructions="Updated prompt",
        )


@pytest.mark.anyio
async def test_create_agent_preset_requires_default_model_when_model_not_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AgentManagementService:
        async def get_default_model(self) -> str | None:
            return None

        async def check_workspace_provider_credentials(self, provider: str) -> bool:
            return True

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentManagementService,
        "with_session",
        lambda role: _AsyncContext(_AgentManagementService()),
    )

    with pytest.raises(ToolError, match="No default model configured"):
        await _tool(mcp_server.create_agent_preset)(
            workspace_id=str(workspace_id),
            name="Security triage",
        )


@pytest.mark.anyio
async def test_create_agent_preset_omitted_retry_fields_use_schema_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    created: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AgentManagementService:
        async def get_default_model(self) -> str | None:
            return "gpt-4o-mini"

        async def get_model_config(self, model_name: str) -> SimpleNamespace:
            assert model_name == "gpt-4o-mini"
            return SimpleNamespace(name="gpt-4o-mini", provider="openai")

        async def check_workspace_provider_credentials(self, provider: str) -> bool:
            assert provider == "openai"
            return True

    class _PresetService(_PresetReadBuilder):
        async def create_preset(self, params: Any) -> SimpleNamespace:
            created["params"] = params
            now = datetime.now(UTC)
            return SimpleNamespace(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=params.name,
                slug="security-triage",
                description=params.description,
                instructions=params.instructions,
                model_name=params.model_name,
                model_provider=params.model_provider,
                base_url=params.base_url,
                output_type=params.output_type,
                actions=params.actions,
                namespaces=params.namespaces,
                tool_approvals=params.tool_approvals,
                mcp_integrations=params.mcp_integrations,
                retries=params.retries,
                enable_thinking=params.enable_thinking,
                enable_internet_access=params.enable_internet_access,
                current_version_id=None,
                created_at=now,
                updated_at=now,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentManagementService,
        "with_session",
        lambda role: _AsyncContext(_AgentManagementService()),
    )
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = await _tool(mcp_server.create_agent_preset)(
        workspace_id=str(workspace_id),
        name="Security triage",
    )

    payload = _payload(result)
    params = created["params"]
    assert params.retries == 3
    assert params.enable_thinking is True
    assert params.enable_internet_access is False
    assert payload["retries"] == 3
    assert payload["enable_thinking"] is True
    assert payload["enable_internet_access"] is False


@pytest.mark.anyio
async def test_create_agent_preset_validates_explicit_model_provider_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AgentManagementService:
        async def get_model_config(self, model_name: str) -> SimpleNamespace:
            assert model_name == "gpt-4o-mini"
            return SimpleNamespace(name="gpt-4o-mini", provider="openai")

        async def check_workspace_provider_credentials(self, provider: str) -> bool:
            assert provider == "openai"
            return True

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentManagementService,
        "with_session",
        lambda role: _AsyncContext(_AgentManagementService()),
    )

    with pytest.raises(ToolError, match="belongs to provider 'openai', not 'opneai'"):
        await _tool(mcp_server.create_agent_preset)(
            workspace_id=str(workspace_id),
            name="Security triage",
            model_name="gpt-4o-mini",
            model_provider="opneai",
        )


@pytest.mark.anyio
async def test_create_agent_preset_allows_custom_model_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    created: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    async def _resolve_model(
        role: object,
        *,
        model_name: str | None,
        model_provider: str | None,
    ) -> tuple[str, str]:
        assert role is not None
        assert model_name == "customer-alias"
        assert model_provider == "custom-model-provider"
        return "customer-alias", "custom-model-provider"

    class _PresetService(_PresetReadBuilder):
        async def create_preset(self, params: Any) -> SimpleNamespace:
            created["params"] = params
            now = datetime.now(UTC)
            return SimpleNamespace(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=params.name,
                slug=params.slug or "security-triage",
                description=params.description,
                instructions=params.instructions,
                model_name=params.model_name,
                model_provider=params.model_provider,
                base_url=params.base_url,
                output_type=params.output_type,
                actions=params.actions,
                namespaces=params.namespaces,
                tool_approvals=params.tool_approvals,
                mcp_integrations=params.mcp_integrations,
                retries=params.retries,
                enable_thinking=params.enable_thinking,
                enable_internet_access=params.enable_internet_access,
                current_version_id=None,
                created_at=now,
                updated_at=now,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_resolve_agent_preset_model", _resolve_model)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = await _tool(mcp_server.create_agent_preset)(
        workspace_id=str(workspace_id),
        name="Security triage",
        model_name="customer-alias",
        model_provider="custom-model-provider",
    )

    payload = _payload(result)
    params = created["params"]
    assert params.model_name == "customer-alias"
    assert params.model_provider == "custom-model-provider"
    assert params.base_url is None
    assert payload["model_provider"] == "custom-model-provider"


@pytest.mark.anyio
async def test_create_agent_preset_requires_workspace_credentials_for_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AgentManagementService:
        async def get_default_model(self) -> str | None:
            return "gpt-4o-mini"

        async def get_model_config(self, model_name: str) -> SimpleNamespace:
            assert model_name == "gpt-4o-mini"
            return SimpleNamespace(name="gpt-4o-mini", provider="openai")

        async def check_workspace_provider_credentials(self, provider: str) -> bool:
            assert provider == "openai"
            return False

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentManagementService,
        "with_session",
        lambda role: _AsyncContext(_AgentManagementService()),
    )

    with pytest.raises(
        ToolError,
        match="Workspace credentials for provider 'openai' are not configured",
    ):
        await _tool(mcp_server.create_agent_preset)(
            workspace_id=str(workspace_id),
            name="Security triage",
        )


@pytest.mark.anyio
async def test_upload_skill_uses_workspace_skill_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    captured: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def upload_skill(self, params):
            captured["params"] = params
            now = datetime.now(UTC)
            return SkillRead(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=params.name,
                description=None,
                current_version_id=None,
                draft_revision=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
                current_version=None,
                is_draft_publishable=True,
                draft_validation_errors=[],
                draft_file_count=len(params.files),
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.upload_skill)(
        workspace_id=str(workspace_id),
        name="triage-skill",
        files=[
            SkillUploadFile(
                path="SKILL.md",
                content_base64=base64.b64encode(
                    b"---\nname: triage-skill\n---\n\n# Triage\n"
                ).decode("ascii"),
                content_type="text/markdown; charset=utf-8",
            )
        ],
    )

    payload = _payload(result)
    params = captured["params"]
    assert params.name == "triage-skill"
    assert len(params.files) == 1
    assert params.files[0].path == "SKILL.md"
    assert payload["name"] == "triage-skill"
    assert payload["draft_file_count"] == 1


@pytest.mark.anyio
async def test_upload_skill_preserves_uploaded_skill_markdown_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    captured: dict[str, Any] = {}
    existing_skill_md = (
        "---\n"
        "name: triage-skill\n"
        "description: Existing description\n"
        "tags:\n"
        "  - keep\n"
        "---\n"
        "\n"
        "# Real instructions\n"
        "\n"
        "Use the uploaded body.\n"
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def upload_skill(self, params):
            captured["upload_params"] = params
            now = datetime.now(UTC)
            return SkillRead(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=params.name,
                description="Updated description",
                current_version_id=None,
                draft_revision=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
                current_version=None,
                is_draft_publishable=True,
                draft_validation_errors=[],
                draft_file_count=len(params.files),
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.upload_skill)(
        workspace_id=str(workspace_id),
        name="triage-skill",
        description="Updated description",
        files=[
            SkillUploadFile(
                path="SKILL.md",
                content_base64=base64.b64encode(
                    existing_skill_md.encode("utf-8")
                ).decode("ascii"),
                content_type="text/markdown; charset=utf-8",
            )
        ],
    )

    payload = _payload(result)
    upload_file = captured["upload_params"].files[0]
    uploaded_content = base64.b64decode(upload_file.content_base64).decode("utf-8")

    assert payload["description"] == "Updated description"
    assert "name: triage-skill" in uploaded_content
    assert "description: Updated description" in uploaded_content
    assert "tags:" in uploaded_content
    assert "# Real instructions" in uploaded_content


@pytest.mark.anyio
async def test_upload_skill_tolerates_malformed_uploaded_frontmatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    captured: dict[str, Any] = {}
    malformed_skill_md = (
        "---\nname: [broken\n---\n\n# Real instructions\n\nKeep this body.\n"
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def upload_skill(self, params):
            captured["upload_params"] = params
            uploaded_content = base64.b64decode(params.files[0].content_base64).decode(
                "utf-8"
            )
            name, description = mcp_server.SkillService._extract_frontmatter(
                uploaded_content
            )
            assert name == "triage-skill"
            assert description == "Recovered description"
            now = datetime.now(UTC)
            return SkillRead(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=params.name,
                description="Recovered description",
                current_version_id=None,
                draft_revision=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
                current_version=None,
                is_draft_publishable=True,
                draft_validation_errors=[],
                draft_file_count=len(params.files),
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.upload_skill)(
        workspace_id=str(workspace_id),
        name="triage-skill",
        description="Recovered description",
        files=[
            SkillUploadFile(
                path="SKILL.md",
                content_base64=base64.b64encode(
                    malformed_skill_md.encode("utf-8")
                ).decode("ascii"),
                content_type="text/markdown; charset=utf-8",
            )
        ],
    )

    payload = _payload(result)
    upload_file = captured["upload_params"].files[0]
    uploaded_content = base64.b64decode(upload_file.content_base64).decode("utf-8")
    _, _, remainder = uploaded_content.partition("---\n")
    frontmatter, separator, body = remainder.partition("\n---\n")

    assert payload["name"] == "triage-skill"
    assert payload["description"] == "Recovered description"
    assert separator == "\n---\n"
    assert yaml.safe_load(frontmatter) == {
        "name": "triage-skill",
        "description": "Recovered description",
    }
    assert "# Real instructions" in body
    assert "Keep this body." in body
    assert "Describe when this skill should be used" not in uploaded_content


@pytest.mark.anyio
async def test_upload_skill_merges_metadata_for_large_skill_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    captured: dict[str, Any] = {}
    large_skill_md = "---\nname: triage-skill\n---\n\n# Real instructions\n\n" + (
        "A" * 300_000
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def upload_skill(self, params):
            captured["upload_params"] = params
            now = datetime.now(UTC)
            return SkillRead(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=params.name,
                description="Updated description",
                current_version_id=None,
                draft_revision=1,
                created_at=now,
                updated_at=now,
                archived_at=None,
                current_version=None,
                is_draft_publishable=True,
                draft_validation_errors=[],
                draft_file_count=len(params.files),
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.upload_skill)(
        workspace_id=str(workspace_id),
        name="triage-skill",
        description="Updated description",
        files=[
            SkillUploadFile(
                path="SKILL.md",
                content_base64=base64.b64encode(large_skill_md.encode("utf-8")).decode(
                    "ascii"
                ),
                content_type="text/markdown; charset=utf-8",
            )
        ],
    )

    payload = _payload(result)
    upload_file = captured["upload_params"].files[0]
    uploaded_content = base64.b64decode(upload_file.content_base64).decode("utf-8")

    assert payload["name"] == "triage-skill"
    assert payload["description"] == "Updated description"
    assert "# Real instructions" in uploaded_content
    assert "Updated description" in uploaded_content
    assert len(uploaded_content) > 300_000


@pytest.mark.anyio
async def test_upload_skill_rejects_missing_root_skill_markdown_before_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    upload_called = False

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def upload_skill(self, params):
            del params
            nonlocal upload_called
            upload_called = True
            raise AssertionError("upload_skill should not be called")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    with pytest.raises(
        ToolError,
        match="Uploaded skill must include a root SKILL.md",
    ):
        await _tool(mcp_server.upload_skill)(
            workspace_id=str(workspace_id),
            name="triage-skill",
            description="Updated description",
            files=[
                SkillUploadFile(
                    path="helper.py",
                    content_base64="cHJpbnQoJ29rJykK",
                    content_type="text/x-python; charset=utf-8",
                )
            ],
        )

    assert upload_called is False


@pytest.mark.anyio
async def test_upload_skill_rejects_non_utf8_root_skill_markdown_before_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    upload_called = False

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def upload_skill(self, params):
            del params
            nonlocal upload_called
            upload_called = True
            raise AssertionError("upload_skill should not be called")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    with pytest.raises(ToolError, match="Uploaded skill SKILL.md must be UTF-8 text"):
        await _tool(mcp_server.upload_skill)(
            workspace_id=str(workspace_id),
            name="triage-skill",
            description="Updated description",
            files=[
                SkillUploadFile(
                    path="SKILL.md",
                    content_base64="//4=",
                    content_type="text/markdown; charset=utf-8",
                )
            ],
        )

    assert upload_called is False


def test_mcp_instructions_include_agent_preset_authoring_tools() -> None:
    assert "get_agent_preset_authoring_context" in mcp_server._MCP_INSTRUCTIONS
    assert "list_integrations" in mcp_server._MCP_INSTRUCTIONS
    assert "create_agent_preset" in mcp_server._MCP_INSTRUCTIONS
    assert "update_agent_preset" in mcp_server._MCP_INSTRUCTIONS


@pytest.mark.anyio
async def test_collect_agent_response_returns_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    class _Stream:
        async def _stream_events(self, _not_disconnected, *, last_id: str):
            assert last_id == "1717426372766-0"
            yield StreamDelta(
                id="1717426372767-0",
                event=UnifiedStreamEvent(
                    type=StreamEventType.TEXT_DELTA,
                    text="hello ",
                ),
            )
            yield StreamDelta(
                id="1717426372768-0",
                event=UnifiedStreamEvent(
                    type=StreamEventType.TEXT_DELTA,
                    text="world",
                ),
            )
            yield StreamEnd(id="1717426372769-0")

    async def _new(_session_id: uuid.UUID, _workspace_id: uuid.UUID) -> _Stream:
        return _Stream()

    monkeypatch.setattr(mcp_server.AgentStream, "new", _new)
    result = await mcp_server._collect_agent_response(
        session_id=session_id,
        workspace_id=workspace_id,
        timeout=5.0,
        last_id="1717426372766-0",
    )

    assert result == "hello world"


@pytest.mark.anyio
async def test_collect_agent_response_surfaces_approval_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    class _Stream:
        async def _stream_events(self, _not_disconnected, *, last_id: str):
            assert last_id == "1717426372766-0"
            yield StreamDelta(
                id="1717426372767-0",
                event=UnifiedStreamEvent(
                    type=StreamEventType.TEXT_DELTA,
                    text="I can do that.",
                ),
            )
            yield StreamDelta(
                id="1717426372768-0",
                event=UnifiedStreamEvent.approval_request_event(
                    [
                        ToolCallContent(
                            id="toolu_123",
                            name="core.http_request",
                            input={"url": "https://example.com"},
                        )
                    ]
                ),
            )
            yield StreamEnd(id="1717426372769-0")

    async def _new(_session_id: uuid.UUID, _workspace_id: uuid.UUID) -> _Stream:
        return _Stream()

    monkeypatch.setattr(mcp_server.AgentStream, "new", _new)
    result = await mcp_server._collect_agent_response(
        session_id=session_id,
        workspace_id=workspace_id,
        timeout=5.0,
        last_id="1717426372766-0",
    )
    payload = _payload(result)

    assert payload["status"] == "awaiting_approval"
    assert payload["session_id"] == str(session_id)
    assert payload["partial_output"] == "I can do that."
    assert payload["items"] == [
        {
            "tool_call_id": "toolu_123",
            "tool_name": "core.http_request",
            "args": {"url": "https://example.com"},
        }
    ]
