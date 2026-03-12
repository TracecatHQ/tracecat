from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams
from tracecat_registry import RegistrySecret

import tracecat.mcp.auth as mcp_auth
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.stream.events import StreamDelta, StreamEnd
from tracecat.expressions.common import ExprType
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
async def test_validate_template_action_stdio_requires_template_path_or_artifact(
    monkeypatch,
):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(ToolError, match="template_path is required"):
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(uuid.uuid4()),
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_validate_template_action_stdio_reads_file(monkeypatch, tmp_path: Path):
    workspace_id = uuid.uuid4()
    resolved_role = SimpleNamespace(
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
    )
    template_path = tmp_path / "template.yaml"
    template_path.write_text(
        "definition:\n  action: tools.test.run\n", encoding="utf-8"
    )

    async def _resolve(_workspace_id):
        return workspace_id, resolved_role

    async def _validate_text(*, role: Any, template_text: str, check_db: bool):
        assert role is resolved_role
        assert check_db is False
        assert "tools.test.run" in template_text
        return json.dumps(
            {"valid": True, "action_name": "tools.test.run", "errors": []}
        )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_validate_template_action_text", _validate_text)

    payload = _payload(
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(workspace_id),
            template_path=str(template_path),
            ctx=_fake_ctx(transport="stdio"),
        )
    )
    assert payload["valid"] is True
    assert payload["action_name"] == "tools.test.run"


@pytest.mark.anyio
async def test_prepare_template_file_upload_stores_artifact(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=organization_id)
    fake_redis = _FakeRedis()

    async def _resolve(_workspace_id):
        return workspace_id, role

    async def _upload_url(
        *,
        key: str,
        bucket: str,
        expiry: int | None = None,
        content_type: str | None = None,
    ):
        _ = bucket, expiry, content_type
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
async def test_validate_template_action_remote_rejects_template_path(
    monkeypatch, tmp_path: Path
):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    template_path = tmp_path / "template.yaml"
    template_path.write_text(
        "definition:\n  action: tools.test.run\n", encoding="utf-8"
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(ToolError, match="template_path is only supported for stdio"):
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(uuid.uuid4()),
            template_path=str(template_path),
            ctx=_fake_ctx(),
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
    # step1 at depth 0 → y=150, step2 at depth 1 → y=300
    assert action_positions["step1"][1] == 150
    assert action_positions["step2"][1] == 300


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
async def test_update_workflow_does_not_accept_definition_yaml(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(TypeError, match="definition_yaml"):
        await _tool(mcp_server.update_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
            definition_yaml="definition: {}",
        )


@pytest.mark.anyio
async def test_get_workflow_returns_metadata_only(monkeypatch):
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
    )

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
    assert "definition_yaml" not in payload


@pytest.mark.anyio
async def test_create_workflow_does_not_accept_definition_yaml(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(TypeError, match="definition_yaml"):
        await _tool(mcp_server.create_workflow)(
            workspace_id=str(uuid.uuid4()),
            title="Example",
            description="Desc",
            definition_yaml="definition: {}",
        )


@pytest.mark.anyio
async def test_get_workflow_file_stdio_writes_nested_yaml(monkeypatch, tmp_path: Path):
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=uuid.uuid4())
    workflow = SimpleNamespace(
        id=workflow_id,
        title="Example workflow",
        description="Example description",
        status="offline",
        version=None,
        alias=None,
        entrypoint=None,
        folder_id=uuid.uuid4(),
        trigger_position_x=1.0,
        trigger_position_y=2.0,
        viewport_x=3.0,
        viewport_y=4.0,
        viewport_zoom=0.5,
        actions=[SimpleNamespace(ref="step_a", position_x=10.0, position_y=20.0)],
        schedules=[],
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

        async def build_dsl_from_workflow(self, _workflow):
            return SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "title": "Example workflow",
                    "actions": [],
                }
            )

    class _FolderService:
        def __init__(self, _session, *, role):
            self.role = role

        async def get_folder(self, _folder_id):
            return SimpleNamespace(path="/detections/high/")

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
    monkeypatch.setattr(mcp_server, "WorkflowFolderService", _FolderService)
    monkeypatch.setattr(mcp_server, "CaseTriggersService", _CaseTriggerService)

    result = await _tool(mcp_server.get_workflow_file)(
        workspace_id=str(workspace_id),
        workflow_id=str(workflow_id),
        base_dir=str(tmp_path),
        ctx=_fake_ctx(transport="stdio"),
    )

    payload = _payload(result)
    saved_path = Path(payload["saved_path"])
    assert saved_path == tmp_path / "detections" / "high" / saved_path.name
    assert saved_path.exists()
    exported = yaml.safe_load(saved_path.read_text(encoding="utf-8"))
    assert exported["layout"]["trigger"] == {"x": 1.0, "y": 2.0}
    assert payload["suggested_relative_path"].startswith("detections/high/")


@pytest.mark.anyio
async def test_get_workflow_file_stdio_rejects_collisions(monkeypatch, tmp_path: Path):
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

    file_name = mcp_server._build_workflow_file_name(
        workflow.title,
        mcp_server.WorkflowUUID.new(workflow_id),
    )
    target = tmp_path / file_name
    target.write_text("existing", encoding="utf-8")

    with pytest.raises(ToolError, match="Refusing to overwrite"):
        await _tool(mcp_server.get_workflow_file)(
            workspace_id=str(workspace_id),
            workflow_id=str(workflow_id),
            base_dir=str(tmp_path),
            overwrite=False,
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
    assert uploaded["key"].startswith(
        f"{workspace_id}/mcp/workflow-files/remote-session/"
    )


@pytest.mark.anyio
async def test_get_workflow_file_draft_false_uses_published_definition(
    monkeypatch, tmp_path: Path
):
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, organization_id=uuid.uuid4())
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

    result = await _tool(mcp_server.get_workflow_file)(
        workspace_id=str(workspace_id),
        workflow_id=str(workflow_id),
        draft=False,
        base_dir=str(tmp_path),
        ctx=_fake_ctx(transport="stdio"),
    )

    payload = _payload(result)
    exported = yaml.safe_load(Path(payload["saved_path"]).read_text(encoding="utf-8"))
    assert payload["draft"] is False
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

    async def _upload_url(
        *,
        key: str,
        bucket: str,
        expiry: int | None = None,
        content_type: str | None = None,
    ):
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
    assert payload["mode"] == "patch"
    assert captured["folder_path"] == "/detections/critical/"
    assert captured["workflow_id"] == mcp_server.WorkflowUUID.new(workflow_id)

    with pytest.raises(ToolError, match="already been consumed"):
        await _tool(mcp_server.update_workflow_from_uploaded_file)(
            workspace_id=str(workspace_id),
            workflow_id=str(workflow_id),
            artifact_id=str(artifact.artifact_id),
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


def test_mcp_instructions_warn_about_workflow_file_writes():
    assert (
        "current directory or selected base directory" in mcp_server._MCP_INSTRUCTIONS
    )
    assert "prepare_workflow_file_upload" in mcp_server._MCP_INSTRUCTIONS
    assert "definition_yaml" not in mcp_server._MCP_INSTRUCTIONS
    assert "prepare_template_file_upload" in mcp_server._MCP_INSTRUCTIONS


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
    assert len(payload) == 1
    assert payload[0]["keys"] == ["API_KEY"]


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
    assert payload == [
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
    assert payload[0]["ref"] == "malware"
    assert payload[0]["color"] == "#ff8800"


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
    field_schema = {"severity_band": {"type": "SELECT", "options": ["low", "high"]}}

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
    assert payload[0]["id"] == "status_reason"
    assert payload[0]["type"] == "TEXT"
    assert payload[1]["type"] == "SELECT"
    assert payload[1]["options"] == ["low", "high"]


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
    assert len(payload) == 2
    assert payload[0]["action_name"] == "core.http_request"
    assert payload[1]["action_name"] == "tools.slack.post_message"


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
    assert len(payload) == 1
    assert captured_kwargs.get("namespace") == "tools.slack"


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

    assert len(payload) == 1


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

    assert len(payload) == 2
    returned_ids = {w["id"] for w in payload}
    assert str(WS_A) in returned_ids
    assert str(WS_B) in returned_ids


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


@pytest.mark.anyio
async def test_import_csv(monkeypatch, tmp_path: Path):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    table_id = uuid.uuid4()

    class _FakeColumn:
        def __init__(self, original_name, name):
            self.original_name = original_name
            self.name = name

    class _TablesService:
        async def import_table_from_csv(self, *, contents, table_name, **kwargs):
            self._contents = contents
            self._table_name = table_name
            table = SimpleNamespace(id=table_id, name="test_table")
            columns = [
                _FakeColumn("Name", "name"),
                _FakeColumn("Age", "age"),
            ]
            return table, 3, columns

    svc = _TablesService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        TablesService,
        "with_session",
        lambda role: _AsyncContext(svc),
    )

    csv_text = "Name,Age\nAlice,30\nBob,25\nCharlie,35"
    csv_path = tmp_path / "people.csv"
    csv_path.write_text(csv_text, encoding="utf-8")
    result = await _tool(mcp_server.import_csv)(
        workspace_id=str(uuid.uuid4()),
        csv_path=str(csv_path),
        table_name="test_table",
        ctx=_fake_ctx(transport="stdio"),
    )
    payload = _payload(result)
    assert payload["id"] == str(table_id)
    assert payload["name"] == "test_table"
    assert payload["rows_inserted"] == 3
    assert payload["column_mapping"] == {"Name": "name", "Age": "age"}
    assert svc._contents == csv_text.encode()
    assert svc._table_name == "test_table"


@pytest.mark.anyio
async def test_import_csv_rejects_missing_file(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(ToolError, match="CSV file not found"):
        await _tool(mcp_server.import_csv)(
            workspace_id=str(uuid.uuid4()),
            csv_path="/tmp/does-not-exist.csv",
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_import_csv_remote_rejects_local_path(monkeypatch, tmp_path: Path):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    csv_path = tmp_path / "people.csv"
    csv_path.write_text("Name,Age\nAlice,30\n", encoding="utf-8")

    with pytest.raises(ToolError, match="csv_path is only supported for stdio"):
        await _tool(mcp_server.import_csv)(
            workspace_id=str(uuid.uuid4()),
            csv_path=str(csv_path),
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
async def test_export_csv_stdio_writes_file(monkeypatch, tmp_path: Path):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    table_id = uuid.uuid4()

    class _FakeColumn:
        def __init__(self, name):
            self.name = name

    fake_table = SimpleNamespace(
        id=table_id,
        name="test_table",
        columns=[
            _FakeColumn("id"),
            _FakeColumn("created_at"),
            _FakeColumn("updated_at"),
            _FakeColumn("city"),
            _FakeColumn("age"),
        ],
    )

    limits: list[int] = []

    class _TablesService:
        async def get_table(self, _table_id):
            return fake_table

        async def search_rows(self, _table, *, limit=1000, cursor=None):
            limits.append(limit)
            return SimpleNamespace(
                items=[
                    {"city": "NYC", "age": 30, "id": "1"},
                    {"city": "LA", "age": 25, "id": "2"},
                ],
                has_more=False,
                next_cursor=None,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        TablesService,
        "with_session",
        lambda role: _AsyncContext(_TablesService()),
    )

    payload = _payload(
        await _tool(mcp_server.export_csv)(
            workspace_id=str(uuid.uuid4()),
            table_id=str(table_id),
            base_dir=str(tmp_path),
            ctx=_fake_ctx(transport="stdio"),
        )
    )
    saved_path = Path(payload["saved_path"])
    lines = saved_path.read_text(encoding="utf-8").strip().splitlines()
    assert saved_path.exists()
    assert payload["transport"] == "stdio"
    assert payload["suggested_relative_path"].endswith(".csv")
    assert lines[0] == "city,age"  # preserves table column order, system cols excluded
    assert lines[1] == "NYC,30"
    assert lines[2] == "LA,25"
    assert limits == [mcp_server.config.TRACECAT__LIMIT_CURSOR_MAX]


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
    assert payload["download_url"] == "https://example.test/table.csv"
    assert payload["transport"] == "streamable-http"
    assert uploaded["content_type"] == "text/csv"
    assert "/mcp/table-csv/csv-session/" in uploaded["key"]
    assert uploaded["key"].count("/mcp/table-csv/") == 1


@pytest.mark.anyio
async def test_export_csv_rejects_collisions(monkeypatch, tmp_path: Path):
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

    target = tmp_path / mcp_server._build_table_csv_file_name(fake_table.name, table_id)
    target.write_text("existing", encoding="utf-8")

    with pytest.raises(ToolError, match="Refusing to overwrite"):
        await _tool(mcp_server.export_csv)(
            workspace_id=str(uuid.uuid4()),
            table_id=str(table_id),
            base_dir=str(tmp_path),
            overwrite=False,
            ctx=_fake_ctx(transport="stdio"),
        )


@pytest.mark.anyio
async def test_import_csv_empty_raises(monkeypatch, tmp_path: Path):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    class _TablesService:
        async def import_table_from_csv(self, *, contents, table_name, **kwargs):
            from tracecat.exceptions import TracecatImportError

            raise TracecatImportError("CSV file does not contain any columns")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        TablesService,
        "with_session",
        lambda role: _AsyncContext(_TablesService()),
    )

    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8")

    with pytest.raises(ToolError, match="CSV file is empty"):
        await _tool(mcp_server.import_csv)(
            workspace_id=str(uuid.uuid4()),
            csv_path=str(csv_path),
            ctx=_fake_ctx(transport="stdio"),
        )


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
