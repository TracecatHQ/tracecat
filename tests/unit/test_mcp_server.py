from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import CallToolRequestParams
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowExecutionStatus
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

import tracecat.mcp.auth as mcp_auth
from tracecat.agent import authoring_context
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.preset.schemas import AgentPresetRead
from tracecat.agent.skill.schemas import (
    SkillDownloadPreparedFile,
    SkillDownloadPreparedResponse,
    SkillDraftAttachUploadedBlobOp,
    SkillDraftDeleteFileOp,
    SkillDraftFileRead,
    SkillDraftRead,
    SkillFileEntry,
    SkillUploadSessionBatchRead,
    SkillUploadSessionRead,
)
from tracecat.agent.stream.events import StreamDelta, StreamEnd
from tracecat.auth.types import Role
from tracecat.cases.search_schemas import CaseSearchRequest
from tracecat.db.models import Schedule, Workflow
from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.schemas import ROOT_STREAM, ActionStatement, StreamID
from tracecat.exceptions import (
    BuiltinRegistryHasNoSelectionError,
    EntitlementRequired,
    ScopeDeniedError,
    TracecatValidationError,
)
from tracecat.expressions.common import ExprType
from tracecat.integrations.enums import (
    IntegrationStatus,
    MCPAuthType,
    OAuthGrantType,
)
from tracecat.integrations.schemas import ProviderKey
from tracecat.pagination import CursorPaginatedResponse
from tracecat.storage.object import ExternalObject, ObjectRef
from tracecat.tables.service import TablesService
from tracecat.validation.schemas import (
    ValidationDetail,
    ValidationResult,
    ValidationResultType,
)
from tracecat.workflow.executions.constants import WF_COMPLETED_REF, WF_TRIGGER_REF
from tracecat.workflow.executions.enums import (
    TriggerType,
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import WorkflowExecutionEventCompact
from tracecat.workflow.executions.service import WorkflowExecutionNotFoundError
from tracecat.workflow.executions.shaping import (
    MAX_EVENT_RESULT_CHARS,
    select_execution_events,
    window_result_text,
)
from tracecat.workflow.management import layout as layout_module
from tracecat.workflow.schedules import bridge as schedules_bridge

_original_create_mcp_auth = mcp_auth.create_mcp_auth
try:
    mcp_auth.create_mcp_auth = lambda: None
    from tracecat.mcp import server as mcp_server  # noqa: E402
finally:
    mcp_auth.create_mcp_auth = _original_create_mcp_auth

from tracecat.mcp.workflow_graph import build_workflow_graph  # noqa: E402
from tracecat.workflow.management import draft  # noqa: E402


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


async def _empty_oauth_inventory(_role: Any) -> set[ProviderKey]:
    """Default OAuth inventory stub: no workspace integrations configured."""
    return set()


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


@pytest.fixture
def resolved_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_workspace_id: Any) -> tuple[uuid.UUID, SimpleNamespace]:
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)


def _patch_workflow_service(monkeypatch: pytest.MonkeyPatch, workflow: Any) -> None:
    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id, *, for_update: bool = False):
            _ = for_update
            return workflow

    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )


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
        "expects_order": None,
        "returns": None,
        "config": {},
        "actions": [],
        "schedules": [],
        "case_trigger": None,
        "graph_version": 1,
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


def _case_trigger_stub(**overrides: Any) -> SimpleNamespace:
    data: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workflow_id": uuid.uuid4(),
        "status": "online",
        "event_types": ["case_created"],
        "tag_filters": [],
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


def _edit_role() -> Role:
    """Minimal auditable Role for persist_workflow_edit_document call sites."""
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )


def test_domain_reference_lists_every_trigger_type():
    expected = ", ".join(member.value for member in TriggerType)

    assert f"### Trigger Type\n{expected}\n" in mcp_server._DOMAIN_REFERENCE_RENDERED


@pytest.mark.anyio
async def test_resolve_workspace_role_rejects_invalid_workspace_id():
    with pytest.raises(ToolError, match="Invalid workspace ID"):
        await mcp_server._resolve_workspace_role(cast(Any, "not-a-uuid"))


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
        await mcp_server._resolve_workspace_role(cast(Any, str(uuid.uuid4())))


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
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
    user_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
    )
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
    assert stored.user_id == user_id
    assert (
        upload_args["expiry"]
        == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
    )


@pytest.mark.anyio
async def test_validate_template_action_remote_uses_artifact_across_sessions(
    monkeypatch,
):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    fake_redis = _FakeRedis()
    artifact = mcp_server.TemplateFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        user_id=user_id,
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
            ctx=_fake_ctx(session_id="different-replica-session"),
        )
    )
    assert payload["valid"] is True
    stored = await mcp_server._load_template_file_artifact(artifact.artifact_id)
    assert stored is not None
    assert stored.used is True


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_validate_template_action_rejects_stdio_transport():
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
    user_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    fake_redis = _FakeRedis()
    artifact = mcp_server.TemplateFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        user_id=user_id,
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
    user_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    fake_redis = _FakeRedis()
    artifact = mcp_server.TemplateFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        user_id=user_id,
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


@pytest.mark.anyio
async def test_validate_template_action_remote_rejects_user_mismatch(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=uuid.uuid4(),
    )
    fake_redis = _FakeRedis()
    artifact = mcp_server.TemplateFileArtifact(
        artifact_id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        user_id=uuid.uuid4(),
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
    monkeypatch.setattr(mcp_server, "_current_mcp_client_id", lambda: "client-a")
    await mcp_server._store_template_file_artifact(artifact)

    with pytest.raises(ToolError, match="not valid for this user"):
        await _tool(mcp_server.validate_template_action)(
            workspace_id=str(workspace_id),
            artifact_id=str(artifact.artifact_id),
            ctx=_fake_ctx(session_id="different-replica-session"),
        )


def test_auto_generate_layout_handles_cycles():
    actions: list[layout_module.WorkflowActionLayoutInput] = [
        {"ref": "start", "depends_on": []},
        {"ref": "middle", "depends_on": ["start", "end"]},
        {"ref": "end", "depends_on": ["middle"]},
    ]

    layout = layout_module.auto_generate_layout(actions)
    refs = {item["ref"] for item in layout["actions"]}

    assert refs == {"start", "middle", "end"}
    assert len(layout["actions"]) == 3


def _layout_by_ref(
    actions: list[layout_module.WorkflowActionLayoutInput],
) -> dict[str, tuple[float, float]]:
    layout = layout_module.auto_generate_layout(actions)
    return {item["ref"]: (item["x"], item["y"]) for item in layout["actions"]}


def test_auto_generate_layout_keeps_linear_chain_vertical():
    positions = _layout_by_ref(
        [
            {"ref": "fetch", "depends_on": []},
            {"ref": "enrich", "depends_on": ["fetch"]},
            {"ref": "notify", "depends_on": ["enrich"]},
        ]
    )

    assert {x for x, _ in positions.values()} == {0.0}
    assert [positions[ref][1] for ref in ("fetch", "enrich", "notify")] == [
        500.0,
        800.0,
        1100.0,
    ]


def test_auto_generate_layout_centers_fan_out_and_join():
    positions = _layout_by_ref(
        [
            {"ref": "lookup", "depends_on": []},
            {"ref": "found", "depends_on": ["lookup"]},
            {"ref": "missing", "depends_on": ["lookup"]},
            {"ref": "close", "depends_on": ["found", "missing"]},
        ]
    )

    left, right = sorted(positions[ref][0] for ref in ("found", "missing"))
    assert right - left >= layout_module.NODE_WIDTH
    assert positions["lookup"][0] == pytest.approx((left + right) / 2, abs=1)
    assert positions["close"][0] == pytest.approx(positions["lookup"][0], abs=1)
    assert positions["close"][1] > positions["found"][1]


def test_auto_generate_layout_keeps_branches_local_without_crossings():
    positions = _layout_by_ref(
        [
            {"ref": "root", "depends_on": []},
            {"ref": "a", "depends_on": ["root"]},
            {"ref": "z", "depends_on": ["root"]},
            {"ref": "a_next", "depends_on": ["a"]},
            {"ref": "z_next", "depends_on": ["z"]},
        ]
    )

    assert positions["a_next"][0] == positions["a"][0]
    assert positions["z_next"][0] == positions["z"][0]


def test_auto_generate_layout_places_error_path_below_source():
    positions = _layout_by_ref(
        [
            {"ref": "call_api", "depends_on": []},
            {"ref": "on_error", "depends_on": ["call_api.error"]},
        ]
    )

    assert positions["on_error"] == (positions["call_api"][0], 800.0)


def test_auto_generate_layout_orders_success_before_error_paths():
    positions = _layout_by_ref(
        [
            {"ref": "normalize", "depends_on": []},
            {"ref": "on_error", "depends_on": ["normalize.error"]},
            {"ref": "cleanup", "depends_on": ["normalize", "normalize.error"]},
            {"ref": "guarded", "depends_on": ["normalize"]},
            {"ref": "other", "depends_on": ["normalize"]},
        ]
    )

    xs = [positions[ref][0] for ref in ("guarded", "other", "cleanup", "on_error")]
    assert xs == sorted(xs)
    assert len(set(xs)) == 4


def test_auto_generate_layout_orders_error_paths_regardless_of_action_order():
    positions = _layout_by_ref(
        [
            {"ref": "on_error", "depends_on": ["call_api.error"]},
            {"ref": "parse", "depends_on": ["call_api"]},
            {"ref": "call_api", "depends_on": []},
        ]
    )

    assert positions["parse"][0] < positions["on_error"][0]


def test_auto_generate_layout_keeps_error_handler_right_of_success_chain():
    positions = _layout_by_ref(
        [
            {"ref": "call_api", "depends_on": []},
            {"ref": "on_error", "depends_on": ["call_api.error"]},
            {"ref": "parse", "depends_on": ["call_api"]},
            {"ref": "notify", "depends_on": ["parse"]},
        ]
    )

    assert positions["parse"][0] < positions["on_error"][0]
    assert positions["notify"][0] == positions["parse"][0]


@pytest.mark.parametrize(
    ("previous", "updated", "expected"),
    [
        pytest.param(
            [{"ref": "a", "depends_on": []}, {"ref": "b", "depends_on": ["a"]}],
            [{"ref": "b", "depends_on": ["a"]}, {"ref": "a", "depends_on": []}],
            False,
            id="reordered",
        ),
        pytest.param(
            [{"ref": "a", "depends_on": []}, {"ref": "b", "depends_on": ["a"]}],
            [{"ref": "a", "depends_on": []}],
            False,
            id="leaf-removed",
        ),
        pytest.param(
            [{"ref": "a", "depends_on": []}],
            [{"ref": "a", "depends_on": []}, {"ref": "b", "depends_on": ["a"]}],
            True,
            id="action-added",
        ),
        pytest.param(
            [
                {"ref": "a", "depends_on": []},
                {"ref": "b", "depends_on": ["a"]},
                {"ref": "c", "depends_on": ["a"]},
            ],
            [
                {"ref": "a", "depends_on": []},
                {"ref": "b", "depends_on": ["a"]},
                {"ref": "c", "depends_on": ["b"]},
            ],
            True,
            id="rewired",
        ),
    ],
)
def test_graph_requires_relayout(
    previous: list[layout_module.WorkflowActionLayoutInput],
    updated: list[layout_module.WorkflowActionLayoutInput],
    expected: bool,
):
    assert layout_module.graph_requires_relayout(previous, updated) is expected


def test_build_import_data_from_workflow_yaml_replaces_supplied_layout():
    import_data = mcp_server._build_import_data_from_workflow_yaml(
        definition_yaml=yaml.safe_dump(
            {
                "definition": {
                    "title": "Example",
                    "description": "Example workflow",
                    "entrypoint": {"ref": "fetch"},
                    "actions": [
                        {
                            "ref": "fetch",
                            "action": "core.transform.reshape",
                            "args": {"value": 1},
                        },
                        {
                            "ref": "notify",
                            "action": "core.transform.reshape",
                            "depends_on": ["fetch"],
                            "args": {"value": 2},
                        },
                    ],
                },
                "layout": {
                    "trigger": {"x": 5, "y": 5},
                    "viewport": {"x": 1, "y": 2, "zoom": 0.5},
                    "actions": [
                        {"ref": "fetch", "x": 10, "y": 10},
                        {"ref": "notify", "x": 12, "y": 12},
                    ],
                },
            }
        )
    )

    assert import_data["layout"] == {
        "trigger": {"x": 0, "y": 0},
        "viewport": {"x": 1, "y": 2, "zoom": 0.5},
        "actions": [
            {"ref": "fetch", "x": 0.0, "y": 500.0},
            {"ref": "notify", "x": 0.0, "y": 800.0},
        ],
    }


def test_relayout_edit_document_only_when_graph_changes():
    workflow = _chained_workflow_stub(uuid.uuid4())
    original = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )

    args_only = original.model_copy(deep=True)
    args_only.definition.actions[0].args = {"value": "changed"}
    assert (
        mcp_server._relayout_edit_document_if_graph_changed(original, args_only)
        is args_only
    )

    added = original.model_copy(deep=True)
    added.definition.actions.append(
        ActionStatement(
            ref="notify_owner",
            action="core.transform.reshape",
            depends_on=["build_alert"],
        )
    )
    relaid = mcp_server._relayout_edit_document_if_graph_changed(original, added)

    positions = {item.ref: (item.x, item.y) for item in relaid.layout.actions}
    assert positions == {
        "fetch_events": (0.0, 500.0),
        "classify": (0.0, 800.0),
        "build_alert": (0.0, 1100.0),
        "notify_owner": (0.0, 1400.0),
    }
    assert relaid.layout.viewport == original.layout.viewport


@pytest.mark.parametrize(
    ("layout_data", "expected"),
    [
        pytest.param(
            {
                "trigger": {"x": 10, "y": 20},
                "viewport": {"x": 30, "y": 40, "zoom": 1.5},
                "actions": [
                    {"ref": "step1", "x": 100, "y": 200},
                    {"ref": "step2", "x": 300, "y": 400},
                ],
            },
            ((10, 20), (30, 40, 1.5), {"step1": (100, 200), "step2": (300, 400)}),
            id="full",
        ),
        pytest.param(None, (None, None, None), id="none"),
        pytest.param(
            {"trigger": {"x": 5}, "actions": [{"ref": "a", "y": 99}]},
            ((5, 0.0), None, {"a": (0.0, 99)}),
            id="partial",
        ),
        pytest.param(
            {
                "trigger": {"position": {"x": 10, "y": 20}},
                "actions": [{"ref": "a", "position": {"x": 30, "y": 40}}],
            },
            ((10, 20), None, {"a": (30, 40)}),
            id="nested-position-shape",
        ),
    ],
)
def test_extract_layout_positions(
    layout_data: dict[str, Any] | None, expected: tuple[Any, Any, Any]
) -> None:
    assert draft.extract_layout_positions(layout_data) == expected


def test_auto_generate_layout_round_trips_through_extract():
    """Auto-generated layout can be extracted into position tuples."""
    actions: list[layout_module.WorkflowActionLayoutInput] = [
        {"ref": "step1", "depends_on": []},
        {"ref": "step2", "depends_on": ["step1"]},
    ]
    layout_data = layout_module.auto_generate_layout(actions)
    trigger, viewport, action_positions = draft.extract_layout_positions(layout_data)
    assert trigger == (0, 0)
    assert viewport is None
    assert action_positions is not None
    assert "step1" in action_positions
    assert "step2" in action_positions
    # step1 at depth 0 → y=500, step2 at depth 1 → y=800
    assert action_positions["step1"][1] == 500
    assert action_positions["step2"][1] == 800


@pytest.mark.anyio
async def test_apply_workflow_yaml_update_validates_definition(monkeypatch):
    validation_error = ValidationResult.new(
        type=ValidationResultType.DSL,
        status="error",
        msg="invalid workflow definition",
        detail=[
            ValidationDetail(
                type="action.input",
                msg="bad action args",
                loc=("actions", "start", "args"),
            )
        ],
    )
    replaced = False

    async def _validate_dsl(*_args, **_kwargs):
        return {validation_error}

    async def _replace_workflow_definition_from_dsl(**_kwargs):
        nonlocal replaced
        replaced = True

    monkeypatch.setattr(mcp_server, "validate_dsl", _validate_dsl)
    monkeypatch.setattr(
        mcp_server,
        "replace_workflow_definition_from_dsl",
        _replace_workflow_definition_from_dsl,
    )

    payload = mcp_server.WorkflowYamlPayload(
        definition=mcp_server.DSLInput.model_validate(
            {
                "title": "Example",
                "description": "Example workflow",
                "entrypoint": {"ref": "start"},
                "actions": [
                    {
                        "ref": "start",
                        "action": "core.transform.reshape",
                        "args": {"value": "hello"},
                    }
                ],
                "config": {"scheduler": "static"},
            }
        )
    )

    with pytest.raises(ToolError, match="validation error"):
        await mcp_server._apply_workflow_yaml_update(
            role=cast(Any, SimpleNamespace()),
            service=cast(
                Any,
                SimpleNamespace(
                    session=SimpleNamespace(add=lambda _obj: None),
                ),
            ),
            workflow=cast(Any, SimpleNamespace()),
            workflow_id=mcp_server.WorkflowUUID.new(uuid.uuid4()),
            update_params=mcp_server.WorkflowUpdate(),
            yaml_payload=payload,
            definition_yaml=None,
            update_mode="patch",
        )

    assert replaced is False


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_get_workflow_returns_metadata_only(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

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


def _chained_workflow_stub(workflow_id: uuid.UUID) -> SimpleNamespace:
    """Three trigger-reachable actions; the edit document sorts them by ref."""
    trigger_id = f"trigger-{workflow_id}"
    fetch_events = _action_stub(
        ref="fetch_events",
        type="core.http_request",
        inputs="url: https://example.invalid/events",
        upstream_edges=[{"source_id": trigger_id, "source_type": "trigger"}],
        position_x=10.0,
        position_y=20.0,
    )
    classify = _action_stub(
        ref="classify",
        type="core.transform.reshape",
        inputs="value: ${{ ACTIONS.fetch_events.result }}",
        control_flow={
            "run_if": "${{ FN.length(ACTIONS.fetch_events.result) > 0 }}",
            "environment": "staging",
        },
        upstream_edges=[
            {
                "source_id": str(fetch_events.id),
                "source_type": "udf",
                "source_handle": "success",
            }
        ],
        position_x=30.0,
        position_y=40.0,
    )
    build_alert = _action_stub(
        ref="build_alert",
        type="core.transform.reshape",
        inputs="value: ${{ var.item }}",
        control_flow={"for_each": "${{ for var.item in ACTIONS.classify.result }}"},
        upstream_edges=[
            {
                "source_id": str(classify.id),
                "source_type": "udf",
                "source_handle": "success",
            }
        ],
        position_x=50.0,
        position_y=60.0,
    )
    return _workflow_stub(
        id=workflow_id,
        entrypoint="fetch_events",
        actions=[fetch_events, classify, build_alert],
    )


def _patch_workflow_read(monkeypatch, workflow: SimpleNamespace) -> None:
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    _patch_workflow_service(monkeypatch, workflow)


@pytest.mark.anyio
async def test_list_workflow_actions_returns_index_map(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _chained_workflow_stub(workflow_id)
    _patch_workflow_read(monkeypatch, workflow)

    async def _actions(_ws, _wf):
        return {"classify": SimpleNamespace(locked_execution_id="wf_example/exec_1")}

    monkeypatch.setattr(mcp_server, "_draft_actions_by_ref", _actions)
    expected_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )
    )

    payload = _payload(
        await _tool(mcp_server.list_workflow_actions)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
        )
    )

    assert payload["draft_revision"] == expected_revision
    assert payload["entrypoint"]["ref"] == "fetch_events"
    assert payload["actions"] == [
        {
            "index": 0,
            "ref": "build_alert",
            "action": "core.transform.reshape",
            "depends_on": ["classify"],
            "has_run_if": False,
            "has_for_each": True,
            "environment": None,
            "locked_execution_id": None,
        },
        {
            "index": 1,
            "ref": "classify",
            "action": "core.transform.reshape",
            "depends_on": ["fetch_events"],
            "has_run_if": True,
            "has_for_each": False,
            "environment": "staging",
            "locked_execution_id": "wf_example/exec_1",
        },
        {
            "index": 2,
            "ref": "fetch_events",
            "action": "core.http_request",
            "depends_on": [],
            "has_run_if": False,
            "has_for_each": False,
            "environment": None,
            "locked_execution_id": None,
        },
    ]
    assert "args" not in json.dumps(payload)


@pytest.mark.anyio
async def test_get_workflow_action_returns_full_action_and_layout(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _chained_workflow_stub(workflow_id)
    _patch_workflow_read(monkeypatch, workflow)

    payload = _payload(
        await _tool(mcp_server.get_workflow_action)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            ref="classify",
        )
    )

    assert payload["index"] == 1
    assert payload["action"]["ref"] == "classify"
    assert payload["action"]["args"] == {"value": "${{ ACTIONS.fetch_events.result }}"}
    assert payload["action"]["run_if"] == (
        "${{ FN.length(ACTIONS.fetch_events.result) > 0 }}"
    )
    assert payload["layout"] == {
        "ref": "classify",
        "x": 30.0,
        "y": 40.0,
        "position": None,
    }
    assert payload["draft_revision"]


@pytest.mark.anyio
async def test_get_workflow_graph_returns_mermaid_and_upstream_gates(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _chained_workflow_stub(workflow_id)
    _patch_workflow_read(monkeypatch, workflow)

    payload = _payload(
        await _tool(mcp_server.get_workflow_graph)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
        )
    )

    assert payload["mermaid"].splitlines() == [
        "flowchart TD",
        "  trigger([trigger])",
        '  build_alert["build_alert<br/>core.transform.reshape'
        '<br/>for_each: for var.item in ACTIONS.classify.result"]',
        '  classify["classify<br/>core.transform.reshape'
        '<br/>if: FN.length(ACTIONS.fetch_events.result) #gt; 0"]',
        '  fetch_events["fetch_events<br/>core.http_request"]',
        "  classify --> build_alert",
        "  fetch_events --> classify",
        "  trigger --> fetch_events",
    ]
    gates = {action["ref"]: action["gated_by"] for action in payload["actions"]}
    assert gates == {
        "build_alert": ["classify"],
        "classify": [],
        "fetch_events": [],
    }
    assert "args" not in json.dumps(payload)
    assert payload["draft_revision"]


def test_build_workflow_graph_renders_error_edges_joins_and_reserved_refs():
    mermaid, actions = build_workflow_graph(
        [
            ActionStatement(
                ref="call_api",
                action="core.http_request",
                run_if='${{ TRIGGER.kind == "alert" }}',
            ),
            ActionStatement(
                ref="end",
                action="core.transform.reshape",
                depends_on=["call_api.error"],
            ),
            ActionStatement(
                ref="merge",
                action="core.transform.reshape",
                depends_on=["call_api", "end"],
                join_strategy=JoinStrategy.ANY,
            ),
        ]
    )

    assert (
        '  call_api["call_api<br/>core.http_request'
        '<br/>if: TRIGGER.kind == #quot;alert#quot;"]'
    ) in mermaid
    assert "  call_api -. error .-> end_" in mermaid
    assert (
        '  merge["merge<br/>core.transform.reshape'
        '<br/>join: any (one parent is enough)"]'
    ) in mermaid
    assert "  end_ --> merge" in mermaid
    assert [action.gated_by for action in actions] == [
        [],
        ["call_api"],
        ["call_api"],
    ]


def test_build_workflow_graph_any_join_keeps_only_gates_on_every_path():
    _, actions = build_workflow_graph(
        [
            ActionStatement(
                ref="enrich",
                action="core.http_request",
                run_if="${{ TRIGGER.enrich }}",
            ),
            ActionStatement(ref="lookup", action="core.http_request"),
            ActionStatement(
                ref="any_merge",
                action="core.transform.reshape",
                depends_on=["enrich", "lookup"],
                join_strategy=JoinStrategy.ANY,
            ),
            ActionStatement(
                ref="all_merge",
                action="core.transform.reshape",
                depends_on=["enrich", "lookup"],
            ),
            ActionStatement(
                ref="notify",
                action="core.transform.reshape",
                depends_on=["any_merge"],
            ),
        ]
    )

    gates = {action.ref: action.gated_by for action in actions}
    assert gates == {
        "enrich": [],
        "lookup": [],
        "any_merge": [],
        "all_merge": ["enrich"],
        "notify": [],
    }


@pytest.mark.anyio
async def test_get_workflow_action_unknown_ref_lists_valid_refs(monkeypatch):
    workflow_id = uuid.uuid4()
    _patch_workflow_read(monkeypatch, _chained_workflow_stub(workflow_id))

    with pytest.raises(ToolError, match="not found") as exc_info:
        await _tool(mcp_server.get_workflow_action)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            ref="missing_step",
        )

    message = str(exc_info.value)
    assert "'missing_step'" in message
    assert "'build_alert'" in message
    assert "'classify'" in message
    assert "'fetch_events'" in message


def test_build_workflow_edit_document_normalizes_null_schedule_timeout() -> None:
    workflow = _workflow_stub(schedules=[_schedule_stub(timeout=None)])

    document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
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

    document_one = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow_one)
    )
    document_two = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow_two)
    )

    assert [schedule.cron for schedule in document_one.schedules] == [
        "0 * * * *",
        "30 * * * *",
    ]
    assert [
        schedule.model_dump(mode="json") for schedule in document_one.schedules
    ] == [schedule.model_dump(mode="json") for schedule in document_two.schedules]


def test_build_workflow_edit_document_omits_invalid_online_case_trigger() -> None:
    workflow = _workflow_stub(
        case_trigger=SimpleNamespace(
            id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            status="online",
            event_types=[],
            tag_filters=[],
        )
    )

    document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )

    assert document.case_trigger is None


def test_build_workflow_edit_document_converts_short_legacy_title() -> None:
    """Legacy/imported metadata that violates WorkflowEditMetadata bounds.

    ``DSLInput`` (the import/upload write path) accepts a bare-string title with
    no length bounds, so a 1-2 character title can be persisted. The edit-document
    read path re-validates with ``WorkflowEditMetadata`` (title 3-100 chars).
    Without conversion the raw Pydantic ``ValidationError`` would escape callers
    (which only map ``WorkflowEditError``) as an opaque 500; assert it is a
    structured ``WorkflowEditError`` the agent can read and repair instead.
    """
    workflow = _workflow_stub(title="hi")

    with pytest.raises(draft.WorkflowEditError) as exc_info:
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )

    error = exc_info.value
    assert not error.conflict
    assert error.code == "validation_error"
    assert error.details is not None
    assert error.details["type"] == "validation_error"
    # The offending field is surfaced so the agent knows what to fix.
    error_locs = [tuple(item["loc"]) for item in error.details["errors"]]
    assert ("title",) in error_locs


def test_build_workflow_edit_document_converts_long_legacy_description() -> None:
    """A >1000 char legacy description converts to a WorkflowEditError, not a 500."""
    workflow = _workflow_stub(description="x" * 1001)

    with pytest.raises(draft.WorkflowEditError) as exc_info:
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )

    error = exc_info.value
    assert error.code == "validation_error"
    assert error.details is not None
    error_locs = [tuple(item["loc"]) for item in error.details["errors"]]
    assert ("description",) in error_locs


def test_compute_workflow_edit_revision_normalizes_layout_position_aliases() -> None:
    workflow = _workflow_stub(
        trigger_position_x=10.0,
        trigger_position_y=20.0,
        actions=[_action_stub(position_x=30.0, position_y=40.0)],
    )
    document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    alias_payload = json.loads(
        json.dumps(draft.workflow_edit_document_payload(document))
    )
    alias_payload["layout"]["trigger"] = {"position": {"x": 10.0, "y": 20.0}}
    alias_payload["layout"]["actions"][0] = {
        "ref": "step_a",
        "position": {"x": 30.0, "y": 40.0},
    }
    alias_document = mcp_server.WorkflowEditDocument.model_validate(alias_payload)

    assert alias_document.layout.trigger is not None
    assert alias_document.layout.trigger.x == 10.0
    assert alias_document.layout.trigger.y == 20.0
    assert alias_document.layout.trigger.position is None
    assert alias_document.layout.actions[0].position is None
    assert draft.compute_workflow_edit_revision(
        alias_document
    ) == draft.compute_workflow_edit_revision(document)


def test_workflow_edit_document_rejects_null_layout_actions() -> None:
    workflow = _workflow_stub(actions=[_action_stub()])
    payload = draft.workflow_edit_document_payload(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )
    )
    payload["layout"]["actions"] = None

    with pytest.raises(ValidationError, match="Input should be a valid list"):
        mcp_server.WorkflowEditDocument.model_validate(payload)


@pytest.mark.anyio
async def test_replace_workflow_definition_from_dsl_uses_existing_workflow() -> None:
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(
        id=workflow_id,
        title="Original workflow",
        description="Original description",
        entrypoint="start",
        expects={},
        expects_order=None,
        config={},
        returns=None,
        actions=[],
    )
    updated_payload = draft.workflow_edit_document_payload(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
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
    dsl = draft.workflow_edit_document_to_dsl(updated_document)

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
    await draft.replace_workflow_definition_from_dsl(
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
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_refreshes_related_state_before_response_revision(
    monkeypatch,
):
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

    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(workflow_service),
    )

    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
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
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_validate_only_does_not_persist(monkeypatch):
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
        expects_order=None,
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

    _patch_workflow_service(monkeypatch, workflow)

    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
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
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_validate_only_rejects_invalid_schedule(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    _patch_workflow_service(monkeypatch, workflow)

    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )
    )

    with pytest.raises(ToolError, match="Invalid workflow schedule"):
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/schedules",
                    "value": [{"cron": "not a cron", "status": "online"}],
                }
            ],
            validate_only=True,
        )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_validate_only_runs_full_definition_validation(monkeypatch):
    validation_error = ValidationResult.new(
        type=ValidationResultType.DSL,
        status="error",
        msg="invalid workflow definition",
        detail=[
            ValidationDetail(
                type="action.input",
                msg="bad action args",
                loc=("actions", "step_a", "args"),
            )
        ],
    )
    validation_calls: list[Any] = []

    async def _validate_dsl(*_args, **kwargs):
        validation_calls.append(kwargs)
        return {validation_error}

    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    monkeypatch.setattr(draft, "validate_dsl", _validate_dsl)
    _patch_workflow_service(monkeypatch, workflow)

    draft_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    base_revision = draft.compute_workflow_edit_revision(draft_document)

    with pytest.raises(ToolError) as exc_info:
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/definition/actions",
                    "value": [
                        {
                            "ref": "step_a",
                            "action": "core.transform.reshape",
                            "args": {},
                            "depends_on": [],
                        }
                    ],
                }
            ],
            validate_only=True,
        )

    payload = json.loads(cast(str, exc_info.value.args[0]))
    assert payload["type"] == "validation_error"
    assert payload["errors"][0]["message"] == "invalid workflow definition"
    assert validation_calls


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_updates_metadata_with_disconnected_layout_actions(
    monkeypatch,
):
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

    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
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
async def test_persist_workflow_edit_document_ignores_stale_layout_refs_after_definition_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = uuid.uuid4()
    trigger_id = f"trigger-{workflow_id}"
    connected_action = _action_stub(
        ref="step_a",
        upstream_edges=[{"source_id": trigger_id, "source_type": "trigger"}],
        position_x=10.0,
        position_y=20.0,
    )
    orphan_action = _action_stub(
        ref="step_orphan",
        position_x=30.0,
        position_y=40.0,
    )
    workflow = _workflow_stub(
        id=workflow_id,
        trigger_position_x=1.0,
        trigger_position_y=2.0,
        actions=[orphan_action, connected_action],
    )
    original_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = draft.workflow_edit_document_payload(original_document)
    updated_payload["definition"]["returns"] = {"status": "ok"}
    updated_payload["layout"]["trigger"]["x"] = 99.0
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    async def _replace_definition_from_dsl(**kwargs: Any) -> None:
        kwargs["workflow"].actions = [connected_action]

    monkeypatch.setattr(
        draft,
        "replace_workflow_definition_from_dsl",
        _replace_definition_from_dsl,
    )

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await draft.persist_workflow_edit_document(
        role=_edit_role(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert workflow.trigger_position_x == 99.0
    assert workflow.actions == [connected_action]


@pytest.mark.anyio
async def test_persist_workflow_edit_document_skips_reorder_only_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    original_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = draft.workflow_edit_document_payload(original_document)
    updated_payload["definition"]["actions"] = list(
        reversed(updated_payload["definition"]["actions"])
    )
    updated_payload["layout"]["actions"] = list(
        reversed(updated_payload["layout"]["actions"])
    )
    updated_payload["schedules"] = list(reversed(updated_payload["schedules"]))
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            raise AssertionError(f"unexpected add({obj!r})")

        async def execute(self, stmt: Any) -> None:
            raise AssertionError(f"unexpected execute({stmt!r})")

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            raise AssertionError(f"unexpected refresh({obj!r}, {attrs!r})")

        async def commit(self) -> None:
            raise AssertionError("unexpected commit()")

    async def _replace_definition_from_dsl(**kwargs: Any) -> None:
        raise AssertionError(f"unexpected definition replacement: {kwargs!r}")

    monkeypatch.setattr(
        draft,
        "replace_workflow_definition_from_dsl",
        _replace_definition_from_dsl,
    )

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await draft.persist_workflow_edit_document(
        role=_edit_role(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_validate_only_canonicalizes_draft_revision(monkeypatch):
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

    _patch_workflow_service(monkeypatch, workflow)

    draft_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    base_revision = draft.compute_workflow_edit_revision(draft_document)
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
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_validate_only_revision_drops_inert_case_trigger(
    monkeypatch,
):
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    _patch_workflow_service(monkeypatch, workflow)

    draft_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    base_revision = draft.compute_workflow_edit_revision(draft_document)

    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "add",
                    "path": "/case_trigger",
                    "value": {
                        "status": "offline",
                        "event_types": [],
                        "tag_filters": [],
                    },
                }
            ],
            validate_only=True,
        )
    )

    assert payload["valid"] is True
    assert payload["validate_only"] is True
    assert payload["draft_revision"] == base_revision


@pytest.mark.anyio
async def test_edit_workflow_validate_only_resolves_ref_paths_and_returns_actions(
    monkeypatch,
):
    async def _validate_dsl(*_args, **_kwargs):
        return set()

    workflow_id = uuid.uuid4()
    workflow = _chained_workflow_stub(workflow_id)
    _patch_workflow_read(monkeypatch, workflow)
    monkeypatch.setattr(draft, "validate_dsl", _validate_dsl)

    draft_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    base_revision = draft.compute_workflow_edit_revision(draft_document)

    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                # Remove the first action so later refs must re-resolve.
                {"op": "remove", "path": "/definition/actions/@build_alert"},
                {"op": "remove", "path": "/layout/actions/@build_alert"},
                {
                    "op": "replace",
                    "path": "/definition/actions/@fetch_events/args/url",
                    "value": "https://example.invalid/v2/events",
                },
                {
                    "op": "add",
                    "path": "/definition/actions/@notify_owner",
                    "value": {
                        "ref": "notify_owner",
                        "action": "core.transform.reshape",
                        "depends_on": ["classify"],
                        "args": {"value": "${{ ACTIONS.classify.result }}"},
                    },
                },
                {
                    "op": "add",
                    "path": "/layout/actions/@notify_owner",
                    "value": {"ref": "notify_owner", "x": 70.0, "y": 80.0},
                },
            ],
            validate_only=True,
        )
    )

    assert payload["valid"] is True
    # Reported in post-save (ref-sorted) order, not patch order.
    assert payload["actions"] == [
        {"index": 0, "ref": "classify"},
        {"index": 1, "ref": "fetch_events"},
        {"index": 2, "ref": "notify_owner"},
    ]


@pytest.mark.anyio
async def test_edit_workflow_unknown_ref_path_names_ref(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _chained_workflow_stub(workflow_id)
    _patch_workflow_read(monkeypatch, workflow)
    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )
    )

    with pytest.raises(ToolError, match="Unknown action ref 'ghost'"):
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/definition/actions/@ghost/args/url",
                    "value": "https://example.invalid",
                }
            ],
            validate_only=True,
        )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_apply_returns_post_save_action_map(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _chained_workflow_stub(workflow_id)

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

    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )
    )
    payload = _payload(
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[
                {"op": "replace", "path": "/metadata/title", "value": "Renamed flow"}
            ],
        )
    )

    assert payload["message"] == f"Workflow {workflow_id} updated successfully"
    assert payload["actions"] == [
        {"index": 0, "ref": "build_alert"},
        {"index": 1, "ref": "classify"},
        {"index": 2, "ref": "fetch_events"},
    ]


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_rejects_stale_revision(monkeypatch):
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
        expects_order=None,
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

    _patch_workflow_service(monkeypatch, workflow)

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
    with pytest.raises(draft.WorkflowEditError, match="not editable via edit_workflow"):
        draft.parse_workflow_edit_request(
            base_revision="revision",
            patch_ops=[{"op": "add", "path": path, "value": value}],
            validate_only=False,
        )


@pytest.mark.parametrize(
    ("patch_ops", "expected_path"),
    [
        (
            [{"op": "remove", "path": "/schedules/0/status"}],
            "/schedules/0/status",
        ),
        (
            [
                {
                    "op": "move",
                    "from": "/schedules/0/status",
                    "path": "/metadata/title",
                }
            ],
            "/schedules/0/status",
        ),
        (
            [{"op": "remove", "path": "/case_trigger/status"}],
            "/case_trigger/status",
        ),
        (
            [
                {
                    "op": "move",
                    "from": "/case_trigger/status",
                    "path": "/metadata/title",
                }
            ],
            "/case_trigger/status",
        ),
    ],
)
def test_parse_workflow_edit_request_rejects_removing_trigger_status(
    patch_ops,
    expected_path,
):
    with pytest.raises(
        draft.WorkflowEditError,
        match=re.escape(f"Patch path '{expected_path}' cannot be removed"),
    ):
        draft.parse_workflow_edit_request(
            base_revision="revision",
            patch_ops=patch_ops,
            validate_only=False,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("patch_path", "expected_path"),
    [
        ("/schedules/0", "/schedules/0/status"),
        ("/schedules", "/schedules/0/status"),
        ("/case_trigger", "/case_trigger/status"),
    ],
)
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_rejects_trigger_parent_replace_that_omits_status(
    monkeypatch,
    patch_path,
    expected_path,
):
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(
        id=workflow_id,
        schedules=[_schedule_stub(workflow_id=workflow_id, status="offline")],
        case_trigger=_case_trigger_stub(workflow_id=workflow_id, status="online"),
    )

    _patch_workflow_service(monkeypatch, workflow)

    draft_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    base_revision = draft.compute_workflow_edit_revision(draft_document)
    document_payload = draft.workflow_edit_document_payload(draft_document)
    if patch_path == "/case_trigger":
        value = document_payload["case_trigger"]
        del value["status"]
    else:
        value = document_payload["schedules"][0]
        del value["status"]
        if patch_path == "/schedules":
            value = [value]

    with pytest.raises(
        ToolError,
        match=re.escape(f"Patch path '{expected_path}' cannot be removed"),
    ):
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=[{"op": "replace", "path": patch_path, "value": value}],
            validate_only=True,
        )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_rejects_unknown_nested_fields(monkeypatch):
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
        expects_order=None,
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

    _patch_workflow_service(monkeypatch, workflow)

    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
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
@pytest.mark.parametrize(
    ("patch_ops", "expected_path"),
    [
        (
            [
                {
                    "op": "replace",
                    "path": "/definition/config",
                    "value": {"scheduler": "static"},
                }
            ],
            "/definition/config/scheduler",
        ),
        (
            [
                {
                    "op": "replace",
                    "path": "/definition/actions",
                    "value": [
                        {
                            "id": "00000000-0000-0000-0000-000000000000",
                            "ref": "step_a",
                            "action": "core.noop",
                            "args": {},
                            "depends_on": [],
                        }
                    ],
                }
            ],
            "/definition/actions/0/id",
        ),
    ],
)
@pytest.mark.usefixtures("resolved_workspace")
async def test_edit_workflow_rejects_excluded_nested_fields_in_parent_replace(
    monkeypatch,
    patch_ops,
    expected_path,
):
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    _patch_workflow_service(monkeypatch, workflow)

    base_revision = draft.compute_workflow_edit_revision(
        draft.build_workflow_edit_document(
            cast(draft._WorkflowEditDocumentSource, workflow)
        )
    )

    with pytest.raises(
        ToolError,
        match=re.escape(
            f"Patch path '{expected_path}' is not editable via edit_workflow"
        ),
    ):
        await _tool(mcp_server.edit_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            base_revision=base_revision,
            patch_ops=patch_ops,
            validate_only=True,
        )


@pytest.mark.anyio
async def test_persist_workflow_edit_document_applies_metadata_with_definition_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow_stub(status="offline", alias=None)
    original_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = draft.workflow_edit_document_payload(original_document)
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
        draft,
        "replace_workflow_definition_from_dsl",
        _replace_definition_from_dsl,
    )

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await draft.persist_workflow_edit_document(
        role=_edit_role(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert captured["workflow"] is workflow
    assert workflow.status == "online"
    assert workflow.alias == "new-alias"


@pytest.mark.anyio
async def test_persist_workflow_edit_document_preserves_offline_schedule_status_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow_stub()
    original_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = draft.workflow_edit_document_payload(original_document)
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

        def expire(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    updated_schedules: list[tuple[uuid.UUID, Any]] = []
    replace_schedules_called = False

    async def _replace_schedules(**kwargs: Any) -> None:
        nonlocal replace_schedules_called
        _ = kwargs
        replace_schedules_called = True

    class _FakeWorkflowSchedulesService:
        def __init__(self, session: Any, role: Any) -> None:
            _ = session, role

        async def update_schedule(self, schedule_id: uuid.UUID, params: Any) -> None:
            updated_schedules.append((schedule_id, params))

    monkeypatch.setattr(draft, "replace_workflow_schedules", _replace_schedules)
    monkeypatch.setattr(
        draft,
        "WorkflowSchedulesService",
        _FakeWorkflowSchedulesService,
    )

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await draft.persist_workflow_edit_document(
        role=_edit_role(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert replace_schedules_called is True
    assert updated_schedules == []


@pytest.mark.anyio
async def test_replace_workflow_schedules_creates_schedules_with_payload_status() -> (
    None
):
    workflow_id = uuid.uuid4()
    replace_calls: list[tuple[Any, list[Any], bool]] = []

    class _FakeWorkflowSchedulesService:
        # The edit-document path delegates to the workflow:update-scoped
        # replace_schedules surface (not the per-schedule create/delete scopes),
        # so the fake mirrors that single entry point.
        async def replace_schedules(
            self,
            workflow_id: uuid.UUID,
            schedules: list[Any],
            commit: bool = True,
        ) -> None:
            replace_calls.append((workflow_id, list(schedules), commit))

    await draft.replace_workflow_schedules(
        service=cast(Any, _FakeWorkflowSchedulesService()),
        workflow_id=cast(Any, workflow_id),
        schedules=[
            draft.WorkflowSchedule(
                cron="0 * * * *",
                status="offline",
                inputs={},
                timeout=0,
            ),
            draft.WorkflowSchedule(
                cron="30 * * * *",
                status="online",
                inputs={},
                timeout=0,
            ),
        ],
    )

    assert len(replace_calls) == 1
    called_workflow_id, created_params, commit = replace_calls[0]
    assert called_workflow_id == workflow_id
    assert commit is False
    assert [params.status for params in created_params] == ["offline", "online"]


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_apply_workflow_yaml_update_replaces_schedules_without_stale_state(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: replacing schedules must not leave deleted Schedule
    instances in the eagerly-loaded workflow.schedules collection, where the
    save-update cascade from session.add(workflow) raises
    "Instance '<Schedule ...>' has been deleted".
    """

    async def _create_schedule(**kwargs: Any) -> Any:
        _ = kwargs
        return SimpleNamespace(id="fake-temporal-handle")

    async def _delete_schedule(schedule_id: Any) -> None:
        _ = schedule_id

    monkeypatch.setattr(schedules_bridge, "create_schedule", _create_schedule)
    monkeypatch.setattr(schedules_bridge, "delete_schedule", _delete_schedule)

    workflow = Workflow(
        title="Scheduled workflow",
        description="Schedule replacement regression test",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.flush()
    schedule = Schedule(
        workspace_id=svc_role.workspace_id,
        workflow_id=workflow.id,
        every=timedelta(hours=1),
        inputs={},
        status="offline",
        timeout=0,
    )
    session.add(schedule)
    await session.commit()
    old_schedule_id = schedule.id

    service = mcp_server.WorkflowsManagementService(session, role=svc_role)
    workflow_id = mcp_server.WorkflowUUID.new(workflow.id)
    loaded = await service.get_workflow(workflow_id, for_update=True)
    assert loaded is not None

    await mcp_server._apply_workflow_yaml_update(
        role=svc_role,
        service=service,
        workflow=loaded,
        workflow_id=workflow_id,
        update_params=mcp_server.WorkflowUpdate(),
        yaml_payload=mcp_server.WorkflowYamlPayload(
            schedules=[
                draft.WorkflowSchedule(
                    every=timedelta(hours=2),
                    status="offline",
                    inputs={},
                    timeout=0,
                )
            ]
        ),
        definition_yaml=None,
        update_mode="replace",
    )

    result = await session.execute(
        select(Schedule).where(Schedule.workflow_id == workflow.id)
    )
    persisted = result.scalars().all()
    assert [s.every for s in persisted] == [timedelta(hours=2)]
    assert old_schedule_id not in {s.id for s in persisted}


@pytest.mark.anyio
async def test_apply_workflow_yaml_update_bumps_graph_version_on_definition(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: rewriting the action graph via the YAML update path must
    bump graph_version so a builder holding a stale base_version gets a 409 from
    the graph API instead of silently applying ops against the old graph.
    """

    async def _validate_dsl(*_args: Any, **_kwargs: Any) -> set[Any]:
        return set()

    monkeypatch.setattr(mcp_server, "validate_dsl", _validate_dsl)

    workflow = Workflow(
        title="Graph version workflow",
        description="Graph version regression test",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.commit()
    assert workflow.graph_version == 1

    service = mcp_server.WorkflowsManagementService(session, role=svc_role)
    workflow_id = mcp_server.WorkflowUUID.new(workflow.id)
    loaded = await service.get_workflow(workflow_id, for_update=True)
    assert loaded is not None

    definition_yaml = (
        "definition:\n"
        "  title: Graph version workflow\n"
        "  description: Graph version regression test\n"
        "  entrypoint:\n"
        "    ref: start\n"
        "  actions:\n"
        "    - ref: start\n"
        "      action: core.transform.reshape\n"
        "      args:\n"
        "        value: hello\n"
        "  config:\n"
        "    scheduler: static\n"
    )
    yaml_payload = mcp_server._parse_workflow_yaml_payload(definition_yaml)

    await mcp_server._apply_workflow_yaml_update(
        role=svc_role,
        service=service,
        workflow=loaded,
        workflow_id=workflow_id,
        update_params=mcp_server.WorkflowUpdate(),
        yaml_payload=yaml_payload,
        definition_yaml=definition_yaml,
        update_mode="replace",
    )
    await session.commit()

    refreshed = (
        await session.execute(select(Workflow).where(Workflow.id == workflow.id))
    ).scalar_one()
    assert refreshed.graph_version == 2


@pytest.mark.anyio
async def test_persist_workflow_edit_document_bumps_graph_version_on_definition(
    session: AsyncSession, svc_role: Role
) -> None:
    """Regression test: rewriting the action graph through the edit-document path
    must bump graph_version so concurrent builder graph ops with a stale
    base_version are rejected with a 409 instead of applied against the old graph.
    """
    workflow = Workflow(
        title="Edit doc workflow",
        description="Edit-document graph version regression test",
        status="offline",
        workspace_id=svc_role.workspace_id,
    )
    session.add(workflow)
    await session.commit()
    assert workflow.graph_version == 1

    service = mcp_server.WorkflowsManagementService(session, role=svc_role)
    workflow_id = mcp_server.WorkflowUUID.new(workflow.id)
    loaded = await service.get_workflow(workflow_id, for_update=True)
    assert loaded is not None

    original_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, loaded)
    )
    updated_payload = draft.workflow_edit_document_payload(original_document)
    updated_payload["definition"]["actions"] = [
        {
            "ref": "step_a",
            "action": "core.transform.reshape",
            "args": {"value": "hello"},
            "depends_on": [],
            "description": "",
        }
    ]
    updated_payload["layout"]["actions"] = [{"ref": "step_a", "x": 0.0, "y": 0.0}]
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    await draft.persist_workflow_edit_document(
        role=svc_role,
        service=service,
        workflow=loaded,
        original_document=original_document,
        updated_document=updated_document,
    )

    refreshed = (
        await session.execute(select(Workflow).where(Workflow.id == workflow.id))
    ).scalar_one()
    assert refreshed.graph_version == 2


def _drop_layout_fields(payload: dict[str, Any]) -> None:
    del payload["layout"]["trigger"]["x"]
    del payload["layout"]["viewport"]["x"]
    del payload["layout"]["actions"][0]["x"]


def _drop_first_action_layout(payload: dict[str, Any]) -> None:
    payload["layout"]["actions"] = [payload["layout"]["actions"][1]]


def _drop_layout_object(payload: dict[str, Any]) -> None:
    del payload["layout"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            _drop_layout_fields,
            {
                "trigger": (0.0, 20.0),
                "viewport": (0.0, 40.0, 1.5),
                "step_a": (0.0, 50.0),
                "step_b": (60.0, 70.0),
            },
            id="fields",
        ),
        pytest.param(
            _drop_first_action_layout,
            {
                "trigger": (10.0, 20.0),
                "viewport": (30.0, 40.0, 1.5),
                "step_a": (0.0, 0.0),
                "step_b": (60.0, 70.0),
            },
            id="actions",
        ),
        pytest.param(
            _drop_layout_object,
            {
                "trigger": (0.0, 0.0),
                "viewport": (0.0, 0.0, 1.0),
                "step_a": (0.0, 0.0),
                "step_b": (0.0, 0.0),
            },
            id="object",
        ),
    ],
)
async def test_persist_workflow_edit_document_resets_removed_layout(
    mutate: Callable[[dict[str, Any]], None],
    expected: dict[str, tuple[float, ...]],
) -> None:
    def _action(ref: str, x: float, y: float) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.uuid4(),
            ref=ref,
            type="core.noop",
            title=ref,
            description="",
            status="offline",
            inputs="{}",
            control_flow={},
            is_interactive=False,
            interaction=None,
            upstream_edges=[],
            position_x=x,
            position_y=y,
        )

    action_a = _action("step_a", 40.0, 50.0)
    action_b = _action("step_b", 60.0, 70.0)
    workflow = _workflow_stub(
        trigger_position_x=10.0,
        trigger_position_y=20.0,
        viewport_x=30.0,
        viewport_y=40.0,
        viewport_zoom=1.5,
        actions=[action_a, action_b],
    )
    original_document = draft.build_workflow_edit_document(
        cast(draft._WorkflowEditDocumentSource, workflow)
    )
    updated_payload = draft.workflow_edit_document_payload(original_document)
    mutate(updated_payload)
    updated_document = mcp_server.WorkflowEditDocument.model_validate(updated_payload)

    class _FakeSession:
        def add(self, obj: Any) -> None:
            _ = obj

        async def refresh(self, obj: Any, attrs: list[str] | None = None) -> None:
            _ = obj, attrs

        async def commit(self) -> None:
            return None

    service = SimpleNamespace(session=_FakeSession(), workspace_id=uuid.uuid4())
    await draft.persist_workflow_edit_document(
        role=_edit_role(),
        service=cast(Any, service),
        workflow=cast(Any, workflow),
        original_document=original_document,
        updated_document=updated_document,
    )

    assert (workflow.trigger_position_x, workflow.trigger_position_y) == expected[
        "trigger"
    ]
    assert (
        workflow.viewport_x,
        workflow.viewport_y,
        workflow.viewport_zoom,
    ) == expected["viewport"]
    assert (action_a.position_x, action_a.position_y) == expected["step_a"]
    assert (action_b.position_x, action_b.position_y) == expected["step_b"]


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_get_workflow_returns_inline_definition_when_requested(monkeypatch):
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
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
@pytest.mark.usefixtures("resolved_workspace")
async def test_get_workflow_returns_too_large_metadata_when_inline_is_too_large(
    monkeypatch,
):
    workflow_id = uuid.uuid4()
    workflow = _workflow_stub(id=workflow_id)

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(
        mcp_server,
        "_build_workflow_yaml_envelope",
        lambda **_kwargs: asyncio.sleep(
            0,
            result={
                "definition": {
                    "description": "x"
                    * (mcp_server._inline_workflow_yaml_max_bytes() + 1024)
                }
            },
        ),
    )

    payload = _payload(
        await _tool(mcp_server.get_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(workflow_id),
            include_definition_yaml=True,
        )
    )
    assert payload["definition_transport"] == "too_large"
    assert payload["definition_size_bytes"] > payload["inline_limit_bytes"]
    assert "definition_yaml" not in payload


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_create_workflow_rejects_oversized_inline_definition_yaml():
    with pytest.raises(ToolError, match="edit_workflow"):
        await _tool(mcp_server.create_workflow)(
            workspace_id=str(uuid.uuid4()),
            title="Example",
            definition_yaml="x" * (mcp_server._inline_workflow_yaml_max_bytes() + 1),
        )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_create_workflow_surfaces_builtin_registry_sync_pending(monkeypatch):
    class _WorkflowService:
        async def create_workflow_from_external_definition(self, *_args, **_kwargs):
            raise BuiltinRegistryHasNoSelectionError(
                "Builtin registry sync is still in progress. Please retry shortly.",
                detail={"origin": "tracecat_registry"},
            )

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
@pytest.mark.usefixtures("resolved_workspace")
async def test_update_workflow_rejects_oversized_inline_definition_yaml():
    with pytest.raises(ToolError, match="edit_workflow"):
        await _tool(mcp_server.update_workflow)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
            definition_yaml="x" * (mcp_server._inline_workflow_yaml_max_bytes() + 1),
        )


@pytest.mark.anyio
async def test_publish_workflow_builtin_registry_not_ready_returns_validation_failure(
    monkeypatch,
):
    from tracecat.workflow.management.management import WorkflowPublishResult

    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    role = SimpleNamespace()

    # The publish orchestration lives in WorkflowsManagementService.publish_workflow;
    # the MCP tool just renders its WorkflowPublishResult. Simulate the
    # builtin-sync-pending failure as the service would return it.
    failure = WorkflowPublishResult(
        version=None,
        errors=[
            ValidationResult.new(
                type=ValidationResultType.DSL,
                status="error",
                msg="Builtin registry sync is still in progress. Please retry shortly.",
                detail=[
                    ValidationDetail(
                        type="registry.builtin_sync_pending",
                        msg="Builtin registry sync is still in progress. Please retry shortly.",
                        loc=("registry_lock",),
                    )
                ],
            )
        ],
    )

    class _WorkflowService:
        def __init__(self):
            self.session = object()

        async def publish_workflow(self, wf_id):
            assert wf_id == mcp_server.WorkflowUUID.new(workflow_id)
            return failure

    async def _resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )

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


_SLACK_SECRET_REQ: authoring_context.ActionRequirementPayload = {
    "type": "secret",
    "name": "slack",
    "required_keys": ["SLACK_BOT_TOKEN"],
    "optional_keys": [],
    "optional": False,
}
_URLSCAN_SECRET_REQ: authoring_context.ActionRequirementPayload = {
    "type": "secret",
    "name": "urlscan",
    "required_keys": ["URLSCAN_API_KEY"],
    "optional_keys": [],
    "optional": False,
}
# A wholly-optional secret that declares keys (e.g. the mtls / ca_cert secrets
# inherited from core.http_request) must not block readiness.
_OPTIONAL_MTLS_SECRET_REQ: authoring_context.ActionRequirementPayload = {
    "type": "secret",
    "name": "mtls",
    "required_keys": ["TLS_CERTIFICATE", "TLS_PRIVATE_KEY"],
    "optional_keys": [],
    "optional": True,
}
_GITHUB_KEY = ProviderKey(id="github", grant_type=OAuthGrantType.AUTHORIZATION_CODE)


def _github_oauth_requirements(
    *, optional: bool = False
) -> list[authoring_context.ActionRequirementPayload]:
    return authoring_context.secrets_to_requirements(
        [
            RegistryOAuthSecret(
                provider_id="github",
                grant_type="authorization_code",
                optional=optional,
            )
        ]
    )


@pytest.mark.parametrize(
    ("requirements", "workspace_inventory", "oauth_inventory", "expected"),
    [
        pytest.param(
            [_SLACK_SECRET_REQ],
            {"slack": set()},
            None,
            (False, ["missing key: slack.SLACK_BOT_TOKEN"]),
            id="missing-workspace-secret-key",
        ),
        pytest.param(
            _github_oauth_requirements(),
            {},
            {_GITHUB_KEY},
            (True, []),
            id="oauth-configured",
        ),
        pytest.param(
            _github_oauth_requirements(),
            {},
            set(),
            (False, ["missing oauth integration: github (authorization_code)"]),
            id="oauth-missing",
        ),
        pytest.param(
            _github_oauth_requirements(optional=True),
            {},
            set(),
            (True, []),
            id="optional-oauth-skipped",
        ),
        pytest.param(
            [_OPTIONAL_MTLS_SECRET_REQ],
            {},
            None,
            (True, []),
            id="optional-secret-with-keys-skipped",
        ),
        pytest.param(
            [_URLSCAN_SECRET_REQ, _OPTIONAL_MTLS_SECRET_REQ],
            {"urlscan": {"URLSCAN_API_KEY"}},
            None,
            (True, []),
            id="required-present-optional-absent",
        ),
        pytest.param(
            [_URLSCAN_SECRET_REQ, _OPTIONAL_MTLS_SECRET_REQ],
            {},
            None,
            (False, ["missing secret: urlscan"]),
            id="reports-required-not-optional",
        ),
    ],
)
def test_evaluate_configuration(
    requirements: list[authoring_context.ActionRequirementPayload],
    workspace_inventory: dict[str, set[str]],
    oauth_inventory: set[ProviderKey] | None,
    expected: tuple[bool, list[str]],
) -> None:
    assert (
        authoring_context.evaluate_configuration(
            requirements, workspace_inventory, oauth_inventory
        )
        == expected
    )


def test_secrets_to_requirements_represents_oauth_as_oauth():
    requirements = authoring_context.secrets_to_requirements(
        [RegistryOAuthSecret(provider_id="github", grant_type="authorization_code")]
    )

    assert requirements == [
        {
            "type": "oauth",
            "name": "github_oauth",
            "provider_id": "github",
            "grant_type": "authorization_code",
            "optional": False,
        }
    ]


@pytest.mark.anyio
async def test_load_oauth_inventory_includes_only_connected(monkeypatch):
    # Only CONNECTED integrations can inject a token at runtime; a
    # configured-but-not-connected provider must be excluded from readiness.
    connected = SimpleNamespace(
        provider_id="github",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        status=IntegrationStatus.CONNECTED,
    )
    configured_only = SimpleNamespace(
        provider_id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        status=IntegrationStatus.CONFIGURED,
    )
    not_configured = SimpleNamespace(
        provider_id="notion",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        status=IntegrationStatus.NOT_CONFIGURED,
    )

    class _IntegrationService:
        async def list_integrations(self):
            return [connected, configured_only, not_configured]

    monkeypatch.setattr(
        mcp_server.IntegrationService,
        "with_session",
        lambda role: _AsyncContext(_IntegrationService()),
    )

    inventory = await authoring_context.load_oauth_inventory(
        cast(Any, SimpleNamespace(scopes=frozenset({"integration:read"})))
    )

    assert inventory == {
        ProviderKey(id="github", grant_type=OAuthGrantType.AUTHORIZATION_CODE)
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_get_action_context_includes_configuration(monkeypatch):
    indexed_action = SimpleNamespace(
        manifest=SimpleNamespace(),
        index_entry=SimpleNamespace(options=None),
    )
    registry_service = SimpleNamespace(
        aggregate_secrets_from_manifest=lambda _manifest, _action_name: [
            RegistrySecret(name="slack", keys=["SLACK_BOT_TOKEN"]),
            RegistrySecret(
                name="mtls",
                keys=["TLS_CERTIFICATE", "TLS_PRIVATE_KEY"],
                optional=True,
            ),
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

    async def _secret_inventory(_role):
        return {"slack": {"SLACK_BOT_TOKEN"}}

    async def _oauth_inventory(_role):
        return set()

    monkeypatch.setattr(
        mcp_server,
        "load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(mcp_server, "load_oauth_inventory", _oauth_inventory)
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
    # The optional mtls secret is absent but must not block configuration.
    assert payload["configured"] is True
    assert payload["missing_requirements"] == []
    assert payload["required_secrets"][0]["name"] == "slack"
    assert payload["optional_secrets"] == ["mtls"]


def _github_oauth_registry_service() -> SimpleNamespace:
    """Registry service stub for a GitHub OAuth-backed REST action."""
    indexed_action = SimpleNamespace(
        manifest=SimpleNamespace(),
        index_entry=SimpleNamespace(options=None),
    )
    registry_service = SimpleNamespace(
        aggregate_secrets_from_manifest=lambda _manifest, _action_name: [
            RegistryOAuthSecret(provider_id="github", grant_type="authorization_code")
        ],
    )

    async def _get_indexed(_action_name):
        return indexed_action

    registry_service.get_action_from_index = _get_indexed
    return registry_service


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_get_action_context_oauth_configured_when_integration_exists(monkeypatch):
    async def _create_tool(_action_name, _indexed):
        return SimpleNamespace(
            description="Get a GitHub issue",
            parameters_json_schema={
                "type": "object",
                "properties": {"issue_number": {"type": "integer"}},
                "required": ["issue_number"],
            },
        )

    async def _secret_inventory(_role):
        return {}

    async def _oauth_inventory(_role):
        return {ProviderKey(id="github", grant_type=OAuthGrantType.AUTHORIZATION_CODE)}

    monkeypatch.setattr(mcp_server, "load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(mcp_server, "load_oauth_inventory", _oauth_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_github_oauth_registry_service()),
    )
    monkeypatch.setattr(mcp_server, "create_tool_from_registry", _create_tool)

    result = await _tool(mcp_server.get_action_context)(
        workspace_id=str(uuid.uuid4()),
        action_name="tools.github.get_issue",
    )
    payload = _payload(result)
    assert payload["configured"] is True
    assert payload["missing_requirements"] == []
    oauth_req = payload["required_secrets"][0]
    assert oauth_req["type"] == "oauth"
    assert oauth_req["provider_id"] == "github"
    assert oauth_req["grant_type"] == "authorization_code"
    # Regression: OAuth requirements must not be reported as workspace secrets.
    assert "missing secret: github_oauth" not in payload["missing_requirements"]


@pytest.mark.anyio
async def test_list_secrets_metadata_returns_keys_not_values(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(scopes=frozenset({"secret:read"}))

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
# Case field tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_create_case_field_parses_kind(monkeypatch):
    captured: dict[str, Any] = {}

    async def _create_field(params):
        captured["name"] = params.name
        captured["type"] = params.type
        captured["kind"] = params.kind

    field_service = SimpleNamespace(create_field=_create_field)

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
@pytest.mark.usefixtures("resolved_workspace")
async def test_create_case_field_rejects_invalid_kind_pair(monkeypatch):
    field_service = SimpleNamespace(create_field=lambda _params: None)

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


# ---------------------------------------------------------------------------
# Case dropdown tests
# ---------------------------------------------------------------------------


def _fake_dropdown_definition(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Threat Level",
        "ref": "threat_level",
        "icon_name": None,
        "is_ordered": True,
        "required_on_closure": False,
        "position": 0,
        "options": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_dropdown_option(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "label": "High",
        "ref": "high",
        "icon_name": None,
        "color": None,
        "position": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_dropdown_definitions_service(monkeypatch, service: SimpleNamespace) -> None:
    monkeypatch.setattr(
        mcp_server,
        "CaseDropdownDefinitionsService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(service)),
    )


def _patch_dropdown_values_service(monkeypatch, service: SimpleNamespace) -> None:
    monkeypatch.setattr(
        mcp_server,
        "CaseDropdownValuesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(service)),
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_list_case_dropdowns_missing_scope(monkeypatch):
    async def _list_definitions():
        raise ScopeDeniedError(
            required_scopes=["case:read"], missing_scopes=["case:read"]
        )

    _patch_dropdown_definitions_service(
        monkeypatch, SimpleNamespace(list_definitions=_list_definitions)
    )

    with pytest.raises(ToolError, match="Missing required scope: case:read"):
        await _tool(mcp_server.list_case_dropdowns)(workspace_id=str(uuid.uuid4()))


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_create_case_dropdown_slugifies_refs(monkeypatch):
    captured: dict[str, Any] = {}

    async def _create_definition(params):
        captured["params"] = params
        return _fake_dropdown_definition(
            name=params.name,
            ref=params.ref,
            options=[
                _fake_dropdown_option(
                    label=opt.label, ref=opt.ref, position=opt.position
                )
                for opt in params.options
            ],
        )

    _patch_dropdown_definitions_service(
        monkeypatch, SimpleNamespace(create_definition=_create_definition)
    )

    result = await _tool(mcp_server.create_case_dropdown)(
        workspace_id=str(uuid.uuid4()),
        name="Threat Level",
        options=[
            mcp_server.CaseDropdownOptionInput(label="Very High!"),
            mcp_server.CaseDropdownOptionInput(label="Low"),
        ],
    )
    payload = _payload(result)
    params = captured["params"]
    assert params.ref == "threat_level"
    assert params.options[0].ref == "very_high"
    assert params.options[0].position == 0
    assert params.options[1].ref == "low"
    assert params.options[1].position == 1
    assert payload["ref"] == "threat_level"
    assert payload["options"][0]["ref"] == "very_high"


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_set_case_dropdown_value_not_entitled(monkeypatch):
    async def _set_value_from_input(cid, value):
        raise EntitlementRequired("case_addons")

    _patch_dropdown_values_service(
        monkeypatch, SimpleNamespace(set_value_from_input=_set_value_from_input)
    )

    with pytest.raises(ToolError, match="requires an upgraded plan"):
        await _tool(mcp_server.set_case_dropdown_value)(
            workspace_id=str(uuid.uuid4()),
            case_id=str(uuid.uuid4()),
            definition_ref="threat_level",
            option_ref="high",
        )


# ---------------------------------------------------------------------------
# Case CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("order_by", [None, "assignee", "tags"])
async def test_search_cases(monkeypatch, order_by):
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"case:read"}),
    )

    async def resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", resolve)
    case_id = uuid.uuid4()
    now = datetime.now(UTC)

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

    async def _search_cases(request):
        captured.update(request.model_dump())
        return search_result

    cases_service = SimpleNamespace(query_cases=_search_cases)

    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(with_session=lambda role: _AsyncContext(cases_service)),
    )

    result = await _tool(mcp_server.search_cases)(
        workspace_id=str(workspace_id),
        status="new,in_progress",
        priority="critical",
        search_term="phishing",
        order_by=order_by,
    )
    payload = _payload(result)
    assert len(payload["items"]) == 1
    assert payload["items"][0]["summary"] == "Phishing alert"
    assert captured["search_term"] == "phishing"
    assert captured["order_by"] == order_by
    # Verify enum parsing for comma-separated values
    assert len(captured["status"]) == 2
    assert len(captured["priority"]) == 1


@pytest.mark.anyio
async def test_search_cases_forwards_canonical_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"case:read"}),
    )
    case_id, parent_id = uuid.uuid4(), uuid.uuid4()
    filters = {"not": {"field": "fields.region", "op": "is_null"}}
    result = CursorPaginatedResponse(items=[], total_estimate=None)
    query = AsyncMock(return_value=result)

    async def resolve(_workspace_id):
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(
            with_session=lambda role: _AsyncContext(SimpleNamespace(query_cases=query))
        ),
    )
    response = await _tool(mcp_server.search_cases)(
        workspace_id=workspace_id,
        short_id="CASE-0042",
        case_id=case_id,
        parent_id=parent_id,
        filters=filters,
        tags=["synthetic-tag"],
        dropdown=["verdict:selected"],
        assignee_id=["unassigned"],
        include_rows=True,
        field_ids=["region"],
        include_payload=True,
        include_durations=True,
        include_count=False,
        order_by="short_id",
        sort="asc",
    )
    assert response is result
    assert query.await_args is not None
    request = query.await_args.args[0]
    assert isinstance(request, CaseSearchRequest)
    assert request.case_id == case_id and request.parent_id == parent_id
    assert request.model_dump(mode="json", exclude_none=True)["filters"] == filters
    assert (
        request.include_rows and request.include_payload and request.include_durations
    )
    assert request.include_count is False and request.field_ids == ["region"]
    assert request.tags == ["synthetic-tag"] and request.dropdown == [
        "verdict:selected"
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        EntitlementRequired("case_addons"),
        TracecatValidationError("Unknown case tag refs"),
    ],
)
async def test_search_cases_transports_typed_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset({"case:read"}),
    )

    async def resolve(_workspace_id):
        return role.workspace_id, role

    query = AsyncMock(side_effect=error)
    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", resolve)
    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(
            with_session=lambda role: _AsyncContext(SimpleNamespace(query_cases=query))
        ),
    )
    with pytest.raises(ToolError, match=str(error)):
        await _tool(mcp_server.search_cases)(workspace_id=role.workspace_id)


@pytest.mark.anyio
async def test_search_cases_requires_scope_and_validates_canonical_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=frozenset(),
    )

    async def resolve(_workspace_id):
        return role.workspace_id, role

    gateway = MagicMock()
    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", resolve)
    monkeypatch.setattr(mcp_server, "CasesService", gateway)
    with pytest.raises(ToolError, match="Missing required scope: case:read"):
        await _tool(mcp_server.search_cases)(workspace_id=role.workspace_id)
    gateway.with_session.assert_not_called()
    role = role.model_copy(update={"scopes": frozenset({"case:read"})})
    with pytest.raises(ToolError, match="order_by"):
        await _tool(mcp_server.search_cases)(
            workspace_id=role.workspace_id, order_by="unsupported"
        )
    gateway.with_session.assert_not_called()


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

    async def _get_hierarchy(_cases):
        return {}

    cases_service = SimpleNamespace(
        get_case=_get_case,
        fields=fields_svc,
        session=SimpleNamespace(),
        hierarchy=SimpleNamespace(get_hierarchy=_get_hierarchy),
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
    assert payload["parent"] is None
    assert payload["num_sub_cases"] == 0


@pytest.mark.anyio
async def test_get_case_includes_hierarchy(monkeypatch):
    from tracecat.cases.hierarchy import CaseHierarchy
    from tracecat.cases.schemas import CaseParentRead

    case_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    now = datetime.now(UTC)
    case = SimpleNamespace(
        id=case_id,
        short_id="CASE-0002",
        created_at=now,
        updated_at=now,
        summary="Child alert",
        status=SimpleNamespace(value="new"),
        priority=SimpleNamespace(value="low"),
        severity=SimpleNamespace(value="low"),
        description="",
        assignee=None,
        payload=None,
        tags=[],
    )

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _get_case(_case_id, **_kwargs):
        return case

    async def _empty_dict(*_args):
        return {}

    async def _empty_list():
        return []

    async def _get_hierarchy(cases):
        return {
            c.id: CaseHierarchy(
                parent=CaseParentRead(
                    id=parent_id, short_id="CASE-0001", summary="Parent incident"
                )
            )
            for c in cases
        }

    async def _has_entitlement(_ent):
        return False

    cases_service = SimpleNamespace(
        get_case=_get_case,
        fields=SimpleNamespace(
            get_fields=_empty_dict,
            list_fields=_empty_list,
            get_field_schema=_empty_dict,
        ),
        session=SimpleNamespace(),
        hierarchy=SimpleNamespace(get_hierarchy=_get_hierarchy),
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

    payload = _payload(
        await _tool(mcp_server.get_case)(
            workspace_id=str(uuid.uuid4()), case_id=str(case_id)
        )
    )
    assert payload["parent"]["short_id"] == "CASE-0001"
    assert payload["num_sub_cases"] == 0


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_search_cases_passes_hierarchy_filters(monkeypatch):
    from tracecat.pagination import CursorPaginatedResponse

    captured: dict[str, Any] = {}

    async def _search_cases(params, **kwargs):
        captured.update(kwargs)
        return CursorPaginatedResponse(
            items=[],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )

    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(
            with_session=lambda role: _AsyncContext(
                SimpleNamespace(search_cases=_search_cases)
            )
        ),
    )
    parent_id = uuid.uuid4()
    await _tool(mcp_server.search_cases)(
        workspace_id=str(uuid.uuid4()), parent_id=str(parent_id)
    )
    assert captured["parent_id"] == parent_id
    assert captured["hierarchy"] == "all"


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_search_cases_maps_entitlement_error(monkeypatch):
    from tracecat.exceptions import EntitlementRequired

    async def _search_cases(params, **kwargs):
        raise EntitlementRequired("case_addons")

    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(
            with_session=lambda role: _AsyncContext(
                SimpleNamespace(search_cases=_search_cases)
            )
        ),
    )
    with pytest.raises(ToolError, match="case_addons"):
        await _tool(mcp_server.search_cases)(
            workspace_id=str(uuid.uuid4()), parent_id=str(uuid.uuid4())
        )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_set_and_clear_case_parent(monkeypatch):
    from tracecat.cases.schemas import CaseBatchItemResult, CaseBatchResponse

    calls: list[tuple[str, list[uuid.UUID], uuid.UUID | None]] = []

    def _response(ids: list[uuid.UUID]) -> CaseBatchResponse:
        return CaseBatchResponse(
            results=[CaseBatchItemResult(case_id=i, success=True) for i in ids],
            succeeded=len(ids),
            failed=0,
        )

    async def _set(ids, parent_id):
        calls.append(("set", ids, parent_id))
        return _response(ids)

    async def _clear(ids):
        calls.append(("clear", ids, None))
        return _response(ids)

    monkeypatch.setattr(
        mcp_server,
        "CasesService",
        SimpleNamespace(
            with_session=lambda role: _AsyncContext(
                SimpleNamespace(batch_set_parent=_set, batch_clear_parent=_clear)
            )
        ),
    )
    child_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    payload = _payload(
        await _tool(mcp_server.set_case_parent)(
            workspace_id=str(uuid.uuid4()),
            case_ids=[str(child_id)],
            parent_id=str(parent_id),
        )
    )
    assert payload["succeeded"] == 1
    payload = _payload(
        await _tool(mcp_server.clear_case_parent)(
            workspace_id=str(uuid.uuid4()), case_ids=[str(child_id)]
        )
    )
    assert payload["results"][0]["case_id"] == str(child_id)
    assert calls == [("set", [child_id], parent_id), ("clear", [child_id], None)]

    with pytest.raises(ToolError, match="between 1 and 1000"):
        await _tool(mcp_server.clear_case_parent)(
            workspace_id=str(uuid.uuid4()), case_ids=[]
        )


# ---------------------------------------------------------------------------
# Case tasks tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_update_case_task_wrong_case(monkeypatch):
    """Updating a task that belongs to a different case should fail."""

    task_id = uuid.uuid4()

    existing_task = SimpleNamespace(
        id=task_id,
        case_id=uuid.uuid4(),  # Different case
    )

    async def _get_task(parsed_task_id):
        return existing_task

    task_service = SimpleNamespace(get_task=_get_task)

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

    expected_execution_id = f"{mcp_server.WorkflowUUID.new(wf_id).short()}:exec-123"

    exec_response = {
        "wf_id": wf_id,
        "wf_exec_id": expected_execution_id,
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
        inputs={"override_key": "override_val"},
    )
    payload = _payload(result)
    assert payload["execution_id"] == expected_execution_id
    assert payload["task_id"] == str(task_id)
    assert payload["workflow_id"] == str(wf_id)


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_run_case_task_wrong_case(monkeypatch):
    """Running a task belonging to a different case should fail."""

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
@pytest.mark.usefixtures("resolved_workspace")
async def test_list_case_events(monkeypatch):
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
@pytest.mark.parametrize(
    ("max_bytes", "arguments"),
    [
        pytest.param(1024, {"yaml": "small string"}, id="normal-input"),
        pytest.param(10, {"count": 999999}, id="non-string-args"),
    ],
)
async def test_input_size_limit_allows(
    max_bytes: int, arguments: dict[str, object]
) -> None:
    from tracecat.mcp.middleware import MCPInputSizeLimitMiddleware

    mw = MCPInputSizeLimitMiddleware(max_bytes=max_bytes)
    ctx = _make_tool_context(arguments=arguments)

    sentinel = object()

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        return cast(ToolResult, sentinel)

    result = await mw.on_call_tool(ctx, _call_next)
    assert result is sentinel


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("max_bytes", "value"),
    [
        pytest.param(10, "x" * 200, id="oversized"),
        # 3 chars, 6 bytes in UTF-8.
        pytest.param(4, "ééé", id="counts-utf8-bytes"),
    ],
)
async def test_input_size_limit_rejects(max_bytes: int, value: str) -> None:
    from tracecat.mcp.middleware import MCPInputSizeLimitMiddleware

    mw = MCPInputSizeLimitMiddleware(max_bytes=max_bytes)
    ctx = _make_tool_context(arguments={"yaml": value})

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        raise AssertionError("should not be called")

    with pytest.raises(ToolError, match="exceeds maximum size"):
        await mw.on_call_tool(ctx, _call_next)


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
@pytest.mark.parametrize(
    ("has_context", "claims", "expected"),
    [
        pytest.param(
            True, {"email": "user@example.com"}, "user@example.com", id="email"
        ),
        pytest.param(
            True,
            {"upstream_claims": {"email": " user@example.com "}},
            "user@example.com",
            id="upstream-claims-email",
        ),
        pytest.param(
            True, {"client_id": "tracecat-client"}, "tracecat-client", id="client-id"
        ),
        pytest.param(True, None, "anonymous", id="no-token"),
        pytest.param(False, None, "anonymous", id="no-context"),
    ],
)
async def test_get_mcp_client_id(
    has_context: bool, claims: dict[str, object] | None, expected: str
) -> None:
    from fastmcp.server.context import Context

    from tracecat.mcp.middleware import get_mcp_client_id

    token = SimpleNamespace(claims=claims) if claims is not None else None
    fastmcp_ctx = SimpleNamespace(get_access_token=lambda: token)
    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="t", arguments=None),
        fastmcp_context=cast(Context, fastmcp_ctx) if has_context else None,
        method="tools/call",
    )
    assert get_mcp_client_id(ctx) == expected


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


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_action_catalog_resource(monkeypatch):
    """The action catalog resource returns actions grouped by namespace."""

    async def _secret_inventory(_role):
        return {"slack": {"SLACK_BOT_TOKEN"}}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description
            self.options = {"include_in_schema": True}

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

    monkeypatch.setattr(mcp_server, "load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(mcp_server, "load_oauth_inventory", _empty_oauth_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )

    result = await mcp_server._build_action_catalog(uuid.uuid4())
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
@pytest.mark.usefixtures("resolved_workspace")
async def test_list_actions_surfaces_optional_secrets(monkeypatch):
    """Optional secrets appear under optional_secrets, not missing_requirements."""

    async def _secret_inventory(_role):
        # Only the required urlscan secret is configured.
        return {"urlscan": {"URLSCAN_API_KEY"}}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description
            self.options = {"include_in_schema": True}

    entries = [
        (_IndexEntry("tools.urlscan", "lookup_url", "Lookup a URL"), "platform"),
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
            # Mirrors lookup_url: required urlscan secret plus the optional
            # mtls/ca_cert secrets inherited from core.http_request.
            return [
                RegistrySecret(name="urlscan", keys=["URLSCAN_API_KEY"]),
                RegistrySecret(
                    name="mtls",
                    keys=["TLS_CERTIFICATE", "TLS_PRIVATE_KEY"],
                    optional=True,
                ),
                RegistrySecret(name="ca_cert", keys=["CA_CERTIFICATE"], optional=True),
            ]

    monkeypatch.setattr(mcp_server, "load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(mcp_server, "load_oauth_inventory", _empty_oauth_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )

    result = await _tool(mcp_server.list_actions)(
        workspace_id=str(uuid.uuid4()),
    )
    payload = _payload(result)
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["action_name"] == "tools.urlscan.lookup_url"
    assert item["configured"] is True
    assert item["missing_requirements"] == []
    assert item["optional_secrets"] == ["mtls", "ca_cert"]


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_list_actions_paginates_and_rejects_mismatched_cursor(monkeypatch):
    async def _secret_inventory(_role):
        return {}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description
            self.options = {"include_in_schema": True}

    entries = [
        (_IndexEntry("core", "one", "One"), "platform"),
        (_IndexEntry("core", "two", "Two"), "platform"),
        (_IndexEntry("core", "zthree", "Three"), "platform"),
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

    monkeypatch.setattr(mcp_server, "load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(mcp_server, "load_oauth_inventory", _empty_oauth_inventory)
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
    assert [item["action_name"] for item in second_page["items"]] == ["core.zthree"]

    with pytest.raises(ToolError, match="Cursor no longer matches current filters"):
        await _tool(mcp_server.list_actions)(
            workspace_id=str(uuid.uuid4()),
            query="two",
            cursor=first_page["next_cursor"],
        )


@pytest.mark.anyio
@pytest.mark.usefixtures("resolved_workspace")
async def test_list_actions_enriches_only_requested_page(monkeypatch):
    async def _secret_inventory(_role):
        return {"primary": {"TOKEN"}}

    entries = [
        (
            SimpleNamespace(
                namespace="tools.example",
                name=f"event_{index:03d}",
                description="Search events",
                options={"include_in_schema": True},
            ),
            "platform",
        )
        for index in range(60)
    ]
    fetched: list[str] = []

    class _RegistryService:
        async def search_actions_from_index(self, _query, *, limit=None):
            assert limit is None
            return entries

        async def get_action_from_index(self, action_name):
            fetched.append(action_name)
            return SimpleNamespace(manifest=SimpleNamespace())

        def aggregate_secrets_from_manifest(self, _manifest, action_name):
            secret_name = (
                "primary" if action_name.endswith("event_000") else "secondary"
            )
            return [RegistrySecret(name=secret_name, keys=["TOKEN"])]

    monkeypatch.setattr(mcp_server, "load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(mcp_server, "load_oauth_inventory", _empty_oauth_inventory)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )

    first_page = _payload(
        await _tool(mcp_server.list_actions)(
            workspace_id=str(uuid.uuid4()), query="example", limit=1
        )
    )
    assert len(fetched) == 1
    assert first_page["items"][0]["configured"] is True
    assert first_page["has_more"] is True

    second_page = _payload(
        await _tool(mcp_server.list_actions)(
            workspace_id=str(uuid.uuid4()),
            query="example",
            limit=1,
            cursor=first_page["next_cursor"],
        )
    )
    assert len(fetched) == 2
    assert second_page["items"][0]["action_name"] == "tools.example.event_001"
    assert second_page["items"][0]["configured"] is False
    assert second_page["items"][0]["missing_requirements"] == [
        "missing secret: secondary"
    ]


# ---------------------------------------------------------------------------
# Multitenant isolation tests
# ---------------------------------------------------------------------------

WS_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
WS_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")


def _registry_role(
    *, scopes: frozenset[str] = frozenset({"org:registry:update"})
) -> SimpleNamespace:
    return SimpleNamespace(organization_id=uuid.uuid4(), scopes=scopes)


def _registry_repo(origin: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), origin=origin)


def _sync_response(repo: SimpleNamespace, *, forced: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        repository_id=repo.id,
        origin=repo.origin,
        version="2026.04.25",
        commit_sha="a" * 40,
        actions_count=2,
        forced=forced,
    )


def _patch_sync_custom_registry(
    monkeypatch,
    *,
    role: SimpleNamespace,
    repositories: list[SimpleNamespace] | None = None,
    sync_response: SimpleNamespace | None = None,
    sync_error: Exception | None = None,
) -> AsyncMock:
    """Wire role + a mock RegistryReposService into mcp_server."""

    async def _resolve_org(
        org_id: uuid.UUID | None = None,
    ) -> SimpleNamespace:
        if org_id is not None:
            role.organization_id = org_id
        return role

    monkeypatch.setattr(mcp_server, "_resolve_org_role", _resolve_org)

    repos_service = AsyncMock()
    repos_service.list_repositories.return_value = repositories or []
    if sync_error is not None:
        repos_service.sync_repository.side_effect = sync_error
    elif sync_response is not None:
        repos_service.sync_repository.return_value = sync_response

    def _repos_service_factory(_session, role_arg):
        repos_service.created_role = role_arg
        return repos_service

    monkeypatch.setattr(mcp_server, "RegistryReposService", _repos_service_factory)
    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(MagicMock()),
    )
    return repos_service


@pytest.mark.anyio
async def test_sync_custom_registry_surfaces_scope_denied(monkeypatch):
    """A ScopeDeniedError from the service is surfaced as a ToolError."""
    from tracecat.exceptions import ScopeDeniedError

    repos_service = AsyncMock()
    repos_service.list_repositories.side_effect = ScopeDeniedError(
        required_scopes=["org:registry:read"],
        missing_scopes=["org:registry:read"],
    )

    async def _resolve_org(
        org_id: uuid.UUID | None = None,
    ) -> SimpleNamespace:
        assert org_id is None
        return _registry_role()

    monkeypatch.setattr(mcp_server, "_resolve_org_role", _resolve_org)
    monkeypatch.setattr(
        mcp_server, "RegistryReposService", lambda *_, **__: repos_service
    )
    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(MagicMock()),
    )

    with pytest.raises(ToolError, match="org:registry:read"):
        await _tool(mcp_server.sync_custom_registry)()


@pytest.mark.anyio
async def test_sync_custom_registry_requires_organization_context(monkeypatch):
    role = SimpleNamespace(
        organization_id=None, scopes=frozenset({"org:registry:update"})
    )
    _patch_sync_custom_registry(monkeypatch, role=role)

    with pytest.raises(ToolError, match="organization context"):
        await _tool(mcp_server.sync_custom_registry)()


def test_sync_custom_registry_public_signature_drops_repo_selectors() -> None:
    """Force-sync deletes the current registry version and is not MCP-reachable."""
    signature = inspect.signature(_tool(mcp_server.sync_custom_registry))
    assert "repository_id" not in signature.parameters
    assert "origin" not in signature.parameters
    assert "workspace_id" not in signature.parameters
    assert "force" not in signature.parameters
    assert "org_id" in signature.parameters


@pytest.mark.anyio
async def test_sync_custom_registry_all_excludes_platform_registry(monkeypatch):
    platform_repo = _registry_repo(mcp_server.DEFAULT_REGISTRY_ORIGIN)
    custom_repo = _registry_repo("custom_actions")
    repos_service = _patch_sync_custom_registry(
        monkeypatch,
        role=_registry_role(),
        repositories=[platform_repo, custom_repo],
        sync_response=_sync_response(custom_repo),
    )

    result = await _tool(mcp_server.sync_custom_registry)()
    payload = _payload(result)

    assert payload["success"] is True
    assert repos_service.sync_repository.await_args.args[0] is custom_repo
    assert [item["origin"] for item in payload["results"]] == [custom_repo.origin]


@pytest.mark.anyio
async def test_sync_custom_registry_excludes_local_registry(monkeypatch):
    platform_repo = _registry_repo(mcp_server.DEFAULT_REGISTRY_ORIGIN)
    local_repo = _registry_repo(mcp_server.DEFAULT_LOCAL_REGISTRY_ORIGIN)
    custom_repo = _registry_repo("custom_actions")
    repos_service = _patch_sync_custom_registry(
        monkeypatch,
        role=_registry_role(),
        repositories=[platform_repo, local_repo, custom_repo],
        sync_response=_sync_response(custom_repo),
    )

    result = await _tool(mcp_server.sync_custom_registry)()
    payload = _payload(result)

    assert payload["success"] is True
    assert repos_service.sync_repository.await_args.args[0] is custom_repo
    assert [item["origin"] for item in payload["results"]] == [custom_repo.origin]


@pytest.mark.anyio
async def test_sync_custom_registry_rejects_no_custom_registry(monkeypatch):
    platform_repo = _registry_repo(mcp_server.DEFAULT_REGISTRY_ORIGIN)
    _patch_sync_custom_registry(
        monkeypatch, role=_registry_role(), repositories=[platform_repo]
    )

    with pytest.raises(ToolError, match="No custom registry repository found"):
        await _tool(mcp_server.sync_custom_registry)()


@pytest.mark.anyio
async def test_sync_custom_registry_rejects_multiple_custom_registries(monkeypatch):
    repos = [_registry_repo("custom_one"), _registry_repo("custom_two")]
    _patch_sync_custom_registry(monkeypatch, role=_registry_role(), repositories=repos)

    with pytest.raises(ToolError, match="Expected exactly one custom registry"):
        await _tool(mcp_server.sync_custom_registry)()


@pytest.mark.anyio
async def test_sync_custom_registry_reports_per_repo_failure(monkeypatch):
    from tracecat.exceptions import RegistryError

    bad_repo = _registry_repo("custom_bad")
    _patch_sync_custom_registry(
        monkeypatch,
        role=_registry_role(),
        repositories=[bad_repo],
        sync_error=RegistryError("sync failed"),
    )

    result = await _tool(mcp_server.sync_custom_registry)()
    payload = _payload(result)

    assert payload["success"] is False
    assert len(payload["results"]) == 1
    assert payload["results"][0]["origin"] == bad_repo.origin
    assert payload["results"][0]["success"] is False
    assert payload["results"][0]["error"] == "sync failed"


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
async def test_list_agent_tree_paginates_and_traverses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    calls: list[str] = []

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
            calls.append(path)
            if path == "/":
                return [
                    _Item({"type": "folder", "path": "/soc/", "name": "soc"}),
                    _Item(
                        {
                            "type": "preset",
                            "slug": "root-agent",
                            "name": "Root agent",
                            "model_provider": "openai",
                            "model_name": "gpt-4o-mini",
                            "tags": [],
                        }
                    ),
                ]
            if path == "/soc/":
                return [
                    _Item(
                        {
                            "type": "preset",
                            "slug": "soc-agent",
                            "name": "SOC agent",
                            "model_provider": "openai",
                            "model_name": "gpt-4o",
                            "tags": [{"name": "triage"}],
                        }
                    )
                ]
            return []

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )

    first_page = _payload(
        await _tool(mcp_server.list_agent_tree)(
            workspace_id=str(workspace_id),
            depth=2,
            limit=1,
        )
    )
    assert len(first_page["items"]) == 1
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] is not None
    assert first_page["items"][0]["type"] == "folder"
    assert calls == ["/"]

    full_page = _payload(
        await _tool(mcp_server.list_agent_tree)(
            workspace_id=str(workspace_id),
            depth=2,
            limit=10,
        )
    )
    assert [item["type"] for item in full_page["items"]] == [
        "folder",
        "preset",
        "preset",
    ]
    assert full_page["items"][2]["preset_slug"] == "soc-agent"
    assert full_page["items"][2]["folder_path"] == "/soc/"
    assert calls == ["/", "/", "/soc/"]


@pytest.mark.anyio
async def test_list_skill_tree_paginates_and_traverses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    calls: list[str] = []

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

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
            calls.append(path)
            if path == "/":
                return [
                    _Item({"type": "folder", "path": "/soc/", "name": "soc"}),
                    _Item(
                        {
                            "type": "skill",
                            "name": "Triage",
                            "slug": "triage",
                            "description": "Triage skill",
                            "current_version_id": None,
                            "tags": [],
                            "created_at": "2026-01-01T00:00:00Z",
                            "updated_at": "2026-01-01T00:00:00Z",
                        }
                    ),
                ]
            if path == "/soc/":
                return [
                    _Item(
                        {
                            "type": "skill",
                            "name": "Investigate",
                            "slug": "investigate",
                            "description": None,
                            "current_version_id": None,
                            "tags": [{"name": "ops"}],
                            "created_at": "2026-01-01T00:00:00Z",
                            "updated_at": "2026-01-01T00:00:00Z",
                        }
                    )
                ]
            return []

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )

    result = _payload(
        await _tool(mcp_server.list_skill_tree)(
            workspace_id=str(workspace_id),
            depth=2,
            limit=1,
        )
    )

    assert result["items"][0]["type"] == "folder"
    assert result["has_more"] is True
    assert result["next_cursor"] is not None
    assert calls == ["/"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "service_name"),
    [
        ("create_agent_folder", "AgentFolderService"),
        ("create_skill_folder", "SkillFolderService"),
    ],
)
async def test_create_folder_creates_missing_parents(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    service_name: str,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    created_paths: list[str] = []

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _FolderService:
        def __init__(self) -> None:
            self.folders: dict[str, SimpleNamespace] = {}

        async def get_folder_by_path(self, path: str) -> SimpleNamespace | None:
            return self.folders.get(path)

        async def create_folder(
            self, name: str, parent_path: str = "/", commit: bool = True
        ) -> SimpleNamespace:
            _ = commit
            path = f"{parent_path}{name}/" if parent_path != "/" else f"/{name}/"
            folder = SimpleNamespace(id=uuid.uuid4(), name=name, path=path)
            self.folders[path] = folder
            created_paths.append(path)
            return folder

    folder_service = _FolderService()
    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        getattr(mcp_server, service_name),
        "with_session",
        lambda role: _AsyncContext(folder_service),
    )

    result = _payload(
        await _tool(getattr(mcp_server, tool_name))(
            workspace_id=str(workspace_id),
            path="/soc/triage/",
            parents=True,
        )
    )

    assert result["path"] == "/soc/triage/"
    assert result["created_paths"] == ["/soc/", "/soc/triage/"]
    assert result["already_existed"] is False
    assert created_paths == ["/soc/", "/soc/triage/"]


@pytest.mark.anyio
async def test_create_agent_folder_without_parents_requires_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _FolderService:
        async def get_folder_by_path(self, _path: str) -> None:
            return None

        async def create_folder(
            self, name: str, parent_path: str = "/", commit: bool = True
        ) -> None:
            _ = name, parent_path, commit
            raise TracecatValidationError("Parent path /soc/ not found")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )

    with pytest.raises(ToolError, match="Parent path /soc/ not found"):
        await _tool(mcp_server.create_agent_folder)(
            workspace_id=str(workspace_id),
            path="/soc/triage/",
            parents=False,
        )


@pytest.mark.anyio
async def test_move_agent_presets_dry_run_reports_invalid_and_missing_slugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    destination_folder = SimpleNamespace(id=uuid.uuid4(), path="/soc/")
    preset_id = uuid.uuid4()

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _FolderService:
        session = SimpleNamespace(rollback=AsyncMock())

        async def get_folder_by_path(self, path: str) -> SimpleNamespace | None:
            return destination_folder if path == "/soc/" else None

    class _PresetService:
        async def get_preset_by_slug(self, slug: str) -> SimpleNamespace | None:
            if slug == "triage":
                return SimpleNamespace(id=preset_id, slug=slug, name="Triage")
            return None

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = _payload(
        await _tool(mcp_server.move_agent_presets)(
            workspace_id=str(workspace_id),
            preset_slugs=["triage", "", "missing"],
            destination_path="/soc/",
            dry_run=True,
        )
    )

    assert result["destination_path"] == "/soc/"
    assert result["requested_count"] == 3
    assert result["movable_count"] == 1
    assert result["movable_presets"] == [{"preset_slug": "triage", "name": "Triage"}]
    assert [error["preset_slug"] for error in result["errors"]] == ["", "missing"]


@pytest.mark.anyio
async def test_move_agent_presets_to_root_moves_with_none_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    preset_id = uuid.uuid4()
    moved: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _FolderService:
        session = SimpleNamespace(rollback=AsyncMock())

        async def move_preset(
            self, requested_preset_id: uuid.UUID, folder: Any | None = None
        ) -> None:
            moved["preset_id"] = requested_preset_id
            moved["folder"] = folder

    class _PresetService:
        async def get_preset_by_slug(self, slug: str) -> SimpleNamespace | None:
            return SimpleNamespace(id=preset_id, slug=slug, name="Triage")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    result = _payload(
        await _tool(mcp_server.move_agent_presets)(
            workspace_id=str(workspace_id),
            preset_slugs=["triage"],
            destination_path="/",
        )
    )

    assert moved == {"preset_id": preset_id, "folder": None}
    assert result["moved_count"] == 1
    assert result["moved_presets"][0]["preset_slug"] == "triage"


@pytest.mark.anyio
async def test_move_agent_presets_rejects_missing_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _FolderService:
        async def get_folder_by_path(self, _path: str) -> None:
            return None

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )

    with pytest.raises(ToolError, match="Folder /missing/ not found"):
        await _tool(mcp_server.move_agent_presets)(
            workspace_id=str(workspace_id),
            preset_slugs=["triage"],
            destination_path="/missing/",
        )


@pytest.mark.anyio
async def test_move_skills_dry_run_resolves_slugs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    skill_id = uuid.uuid4()

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _FolderService:
        async def get_folder_by_path(self, _path: str) -> SimpleNamespace:
            return SimpleNamespace(id=uuid.uuid4(), path="/soc/")

    class _SkillService:
        async def get_skill_by_identifier(self, slug: str) -> SimpleNamespace | None:
            if slug == "missing":
                return None
            return SimpleNamespace(id=skill_id, slug=slug, name="Triage")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillFolderService,
        "with_session",
        lambda role: _AsyncContext(_FolderService()),
    )
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = _payload(
        await _tool(mcp_server.move_skills)(
            workspace_id=str(workspace_id),
            skill_slugs=["triage", "missing", " "],
            destination_path="/soc/",
            dry_run=True,
        )
    )

    assert result["requested_count"] == 3
    assert result["movable_count"] == 1
    assert result["movable_skills"][0]["skill_slug"] == "triage"
    assert [error["skill_slug"] for error in result["errors"]] == ["missing", " "]


def test_destructive_case_tools_removed():
    removed_tools = {
        "delete_case",
        "delete_case_task",
        "delete_case_field",
    }
    for tool_name in removed_tools:
        assert not hasattr(mcp_server, tool_name)


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
@pytest.mark.usefixtures("resolved_workspace")
async def test_export_csv_rejects_stdio_transport(monkeypatch):
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
@pytest.mark.parametrize(
    ("last_stream_id", "expected_start_id"),
    [
        ("1717426372766-0", "0-0"),
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

    class _PresetService(_PresetReadBuilder):
        async def get_preset_by_slug(self, _preset_slug: str) -> SimpleNamespace:
            return preset

        async def resolve_agent_preset_version(
            self,
            *,
            slug: str,
        ) -> SimpleNamespace:
            assert slug == "triage"
            return version

    class _SessionService:
        async def create_session(self, create: Any) -> SimpleNamespace:
            created_session_request["value"] = create
            return session

        async def run_turn(
            self,
            _session_id: uuid.UUID,
            _request: Any,
            *,
            active_stream_id: uuid.UUID | None = None,
        ) -> None:
            captured["active_stream_id"] = active_stream_id
            return None

    captured: dict[str, Any] = {}

    async def _collect(
        session_id: uuid.UUID,
        workspace_id_arg: uuid.UUID,
        timeout: float,
        last_id: str,
        stream_id: uuid.UUID | None = None,
    ) -> str:
        captured["session_id"] = session_id
        captured["workspace_id"] = workspace_id_arg
        captured["timeout"] = timeout
        captured["last_id"] = last_id
        captured["stream_id"] = stream_id
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
    # Producer (run_turn) and consumer (_collect) must share the minted id.
    assert captured["stream_id"] is not None
    assert captured["stream_id"] == captured["active_stream_id"]


@pytest.mark.anyio
@pytest.mark.parametrize("tool_name", ["create_agent_preset", "update_agent_preset"])
async def test_agent_preset_tools_expose_use_in_chat_only_on_request(
    tool_name: str,
) -> None:
    tool = next(
        tool for tool in await mcp_server.mcp.list_tools() if tool.name == tool_name
    )

    use_in_chat = tool.parameters["properties"]["use_in_chat"]
    assert "use_in_chat" not in tool.parameters.get("required", [])
    assert use_in_chat["default"] is None
    assert "explicitly asks" in use_in_chat["description"]


@pytest.mark.anyio
async def test_run_agent_preset_does_not_expose_preset_version() -> None:
    tool = next(
        tool
        for tool in await mcp_server.mcp.list_tools()
        if tool.name == "run_agent_preset"
    )

    assert "preset_version" not in tool.parameters["properties"]


@pytest.mark.anyio
async def test_list_integrations_returns_mcp_and_provider_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    current_user_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, user_id=current_user_id)
    # An authorization_code integration connected by another workspace member.
    # Public MCP must report workspace-level status, not the caller's own row.
    other_user_integration = SimpleNamespace(
        provider_id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        user_id=uuid.uuid4(),
        status=IntegrationStatus.CONNECTED,
    )
    # The caller's own row is only configured; listed last to prove the most
    # progressed status wins deterministically regardless of ordering.
    oauth_integration = SimpleNamespace(
        provider_id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        user_id=current_user_id,
        status=IntegrationStatus.CONFIGURED,
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


@pytest.mark.parametrize("reauth_first", [False, True])
@pytest.mark.parametrize(
    "other_status,expected_status",
    [
        (IntegrationStatus.NOT_CONFIGURED, IntegrationStatus.REAUTH_REQUIRED),
        (IntegrationStatus.CONFIGURED, IntegrationStatus.REAUTH_REQUIRED),
        (IntegrationStatus.CONNECTED, IntegrationStatus.CONNECTED),
    ],
)
@pytest.mark.anyio
async def test_integrations_inventory_ranks_status_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    reauth_first: bool,
    other_status: IntegrationStatus,
    expected_status: IntegrationStatus,
) -> None:
    other = SimpleNamespace(
        provider_id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        status=other_status,
    )
    reauth_required = SimpleNamespace(
        provider_id="slack",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        status=IntegrationStatus.REAUTH_REQUIRED,
    )
    integrations = [reauth_required, other]
    if not reauth_first:
        integrations.reverse()

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
            return integrations

        async def list_mcp_integrations(self):
            return []

        async def list_custom_providers(self):
            return []

    monkeypatch.setattr(mcp_server, "all_providers", lambda: [_SlackProvider])
    monkeypatch.setattr(
        mcp_server.IntegrationService,
        "with_session",
        lambda role: _AsyncContext(_IntegrationService()),
    )

    inventory = await mcp_server._build_integrations_inventory(
        cast(Any, SimpleNamespace())
    )

    assert inventory.oauth_providers[0].integration_status == expected_status.value


@pytest.mark.anyio
async def test_get_workflow_authoring_context_truncates_embedded_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"secret:read", "variable:read"}),
    )

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
    monkeypatch.setattr(mcp_server, "load_secret_inventory", _secret_inventory)
    monkeypatch.setattr(mcp_server, "load_oauth_inventory", _empty_oauth_inventory)
    monkeypatch.setattr(mcp_server, "_MCP_EMBEDDED_COLLECTION_LIMIT", 1)
    monkeypatch.setattr(mcp_server, "create_tool_from_registry", _create_tool)
    monkeypatch.setattr(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        lambda role: _AsyncContext(_RegistryService()),
    )
    monkeypatch.setattr(
        "tracecat.agent.authoring_context.VariablesService.with_session",
        lambda role: _AsyncContext(_VariablesService()),
    )
    monkeypatch.setattr(
        "tracecat.agent.authoring_context.load_secret_inventory",
        _secret_inventory,
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
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"variable:read", "secret:read"}),
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    async def _secret_inventory(_role: Any) -> dict[str, set[str]]:
        return {"slack": {"TOKEN"}}

    async def _integrations_inventory(_role: Any) -> Any:
        return mcp_server.IntegrationsInventoryResponse()

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
    monkeypatch.setattr(mcp_server, "load_secret_inventory", _secret_inventory)
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
    catalog_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    created: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AgentManagementService:
        async def get_default_model_selection(self) -> SimpleNamespace:
            return SimpleNamespace(
                catalog_id=catalog_id,
                model_name="gpt-4o-mini",
                model_provider="openai",
            )

    class _AccessService:
        async def is_catalog_enabled(
            self, requested_catalog_id: uuid.UUID, *, workspace_id: uuid.UUID
        ) -> bool:
            assert requested_catalog_id == catalog_id
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
                catalog_id=params.catalog_id,
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
        mcp_server.AgentModelAccessService,
        "with_session",
        lambda role: _AsyncContext(_AccessService()),
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
        use_in_chat=True,
    )

    payload = _payload(result)
    params = created["params"]
    assert params.use_in_chat is True
    assert params.model_name == "gpt-4o-mini"
    assert params.model_provider == "openai"
    assert params.catalog_id == catalog_id
    assert params.mcp_integrations is not None
    assert params.enable_thinking is False
    assert params.enable_internet_access is True
    assert payload["model_name"] == "gpt-4o-mini"
    assert payload["output_type"]["type"] == "object"
    assert payload["enable_thinking"] is False


@pytest.mark.anyio
async def test_update_agent_preset_clear_output_type_sets_explicit_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    captured: dict[str, Any] = {}
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
        catalog_id=None,
        base_url=None,
        output_type={"type": "object"},
        actions=None,
        namespaces=None,
        tool_approvals=None,
        mcp_integrations=None,
        agents={},
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
            captured["params"] = params
            return SimpleNamespace(**{**preset.__dict__, "updated_at": now})

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    await _tool(mcp_server.update_agent_preset)(
        workspace_id=str(workspace_id),
        preset_slug="security-triage",
        clear_output_type=True,
    )

    params = captured["params"]
    assert "output_type" in params.model_fields_set
    assert params.output_type is None


@pytest.mark.anyio
async def test_update_agent_preset_rejects_output_type_with_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)

    with pytest.raises(
        ToolError, match="Pass either output_type or clear_output_type, not both"
    ):
        await _tool(mcp_server.update_agent_preset)(
            workspace_id=str(workspace_id),
            preset_slug="security-triage",
            output_type={"type": "object"},
            clear_output_type=True,
        )


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
    ) -> tuple[str, str, uuid.UUID | None]:
        assert resolved_role is role
        assert model_name == "gpt-5-mini"
        assert model_provider == "openai"
        return "gpt-5-mini", "openai", None

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
        async def get_default_model_selection(self) -> None:
            return None

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentManagementService,
        "with_session",
        lambda role: _AsyncContext(_AgentManagementService()),
    )

    with pytest.raises(ToolError, match="No default model is enabled"):
        await _tool(mcp_server.create_agent_preset)(
            workspace_id=str(workspace_id),
            name="Security triage",
        )


@pytest.mark.anyio
async def test_create_agent_preset_rejects_unenabled_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AccessService:
        async def get_workspace_models(
            self, _workspace_id: uuid.UUID
        ) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    id=uuid.uuid4(),
                    custom_provider_id=None,
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                )
            ]

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentModelAccessService,
        "with_session",
        lambda role: _AsyncContext(_AccessService()),
    )

    with pytest.raises(ToolError, match="not enabled for this workspace"):
        await _tool(mcp_server.create_agent_preset)(
            workspace_id=str(workspace_id),
            name="Security triage",
            model_name="gpt-4o-mini",
            model_provider="opneai",
        )


@pytest.mark.anyio
async def test_create_agent_preset_resolves_catalog_id_for_custom_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    catalog_id = uuid.uuid4()
    custom_provider_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    created: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _AccessService:
        async def get_workspace_models(
            self, requested_workspace_id: uuid.UUID
        ) -> list[SimpleNamespace]:
            assert requested_workspace_id == workspace_id
            return [
                SimpleNamespace(
                    id=catalog_id,
                    custom_provider_id=custom_provider_id,
                    model_name="customer-alias",
                    model_provider="custom-model-provider",
                )
            ]

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
                catalog_id=params.catalog_id,
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
        mcp_server.AgentModelAccessService,
        "with_session",
        lambda role: _AsyncContext(_AccessService()),
    )
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
    assert params.catalog_id == catalog_id
    assert payload["model_provider"] == "custom-model-provider"


@pytest.mark.anyio
async def test_update_agent_preset_can_clear_skill_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    captured: dict[str, Any] = {}
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
        catalog_id=None,
        base_url=None,
        output_type=None,
        actions=None,
        namespaces=None,
        tool_approvals=None,
        mcp_integrations=None,
        agents={},
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
            captured["params"] = params
            return SimpleNamespace(**{**preset.__dict__, "updated_at": now})

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.AgentPresetService,
        "with_session",
        lambda role: _AsyncContext(_PresetService()),
    )

    await _tool(mcp_server.update_agent_preset)(
        workspace_id=str(workspace_id),
        preset_slug="security-triage",
        skills=[],
    )

    assert captured["params"].skills == []


@pytest.mark.anyio
async def test_get_skill_returns_mutable_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:read"}),
    )
    files = [
        SkillFileEntry(
            path="SKILL.md",
            blob_id=uuid.uuid4(),
            sha256="1" * 64,
            size_bytes=42,
            content_type="text/markdown; charset=utf-8",
        ),
        SkillFileEntry(
            path="scripts/helper.py",
            blob_id=uuid.uuid4(),
            sha256="2" * 64,
            size_bytes=84,
            content_type="text/x-python; charset=utf-8",
        ),
    ]

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def get_draft(self, requested_skill_id):
            assert requested_skill_id == skill_id
            return SkillDraftRead(
                skill_id=skill_id,
                skill_name="triage-skill",
                draft_revision=7,
                name="triage-skill",
                description="Triage alerts",
                files=files,
                is_publishable=True,
                validation_errors=[],
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.get_skill)(
        workspace_id=str(workspace_id),
        skill_id=skill_id,
    )

    payload = _payload(result)
    assert payload["skill_id"] == str(skill_id)
    assert payload["draft_revision"] == 7
    assert payload["is_publishable"] is True
    assert payload["validation_errors"] == []
    assert [file["path"] for file in payload["files"]] == [
        "SKILL.md",
        "scripts/helper.py",
    ]
    assert [file["sha256"] for file in payload["files"]] == ["1" * 64, "2" * 64]
    assert [file["size_bytes"] for file in payload["files"]] == [42, 84]


@pytest.mark.anyio
async def test_get_skill_with_path_returns_inline_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    expected_skill_id = skill_id
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:read"}),
    )
    text_content = "---\nname: triage-skill\n---\n"

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def get_draft_file(self, *, skill_id, path, url_expiry_seconds):
            assert skill_id == expected_skill_id
            assert path == "SKILL.md"
            assert (
                url_expiry_seconds
                == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
            )
            return SkillDraftFileRead(
                kind="inline",
                path=path,
                content_type="text/markdown; charset=utf-8",
                size_bytes=len(text_content.encode()),
                sha256="3" * 64,
                text_content=text_content,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.get_skill)(
        workspace_id=str(workspace_id),
        skill_id=skill_id,
        path="SKILL.md",
        ctx=_fake_ctx(),
    )

    payload = _payload(result)
    assert payload["kind"] == "inline"
    assert payload["path"] == "SKILL.md"
    assert payload["sha256"] == "3" * 64
    assert payload["text_content"] == text_content
    assert payload["download_url"] is None


@pytest.mark.anyio
async def test_get_skill_with_path_returns_presigned_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    expected_skill_id = skill_id
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:read"}),
    )
    download_url = "https://downloads.example/helper.py?signature=secret"

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def get_draft_file(self, *, skill_id, path, url_expiry_seconds):
            assert skill_id == expected_skill_id
            assert path == "scripts/helper.py"
            assert (
                url_expiry_seconds
                == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
            )
            return SkillDraftFileRead(
                kind="download",
                path=path,
                content_type="application/octet-stream",
                size_bytes=1_000_000,
                sha256="4" * 64,
                download_url=download_url,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.get_skill)(
        workspace_id=str(workspace_id),
        skill_id=skill_id,
        path="scripts/helper.py",
        ctx=_fake_ctx(),
    )

    payload = _payload(result)
    assert payload["kind"] == "download"
    assert payload["download_url"] == download_url
    assert payload["text_content"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "extra_kwargs"),
    [
        ("get_skill", {"path": "SKILL.md"}),
        ("prepare_skill_download", {}),
    ],
)
async def test_skill_file_tools_reject_stdio_transport(
    tool_name: str, extra_kwargs: dict[str, Any]
) -> None:
    with pytest.raises(
        ToolError, match="only supported for remote streamable-http MCP clients"
    ):
        await _tool(getattr(mcp_server, tool_name))(
            workspace_id=str(uuid.uuid4()),
            skill_id=uuid.uuid4(),
            ctx=_fake_ctx(transport="stdio"),
            **extra_kwargs,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("path", [None, "SKILL.md"])
async def test_get_skill_requires_agent_read_scope(
    monkeypatch: pytest.MonkeyPatch,
    path: str | None,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, scopes=frozenset())

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    def _unexpected_with_session(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("SkillService must not be opened without agent:read")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        _unexpected_with_session,
    )

    with pytest.raises(ToolError, match="Missing required scope: agent:read"):
        await _tool(mcp_server.get_skill)(
            workspace_id=str(workspace_id),
            skill_id=uuid.uuid4(),
            path=path,
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
async def test_prepare_skill_download_returns_complete_presigned_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:read"}),
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    expected_skill_id = skill_id
    files = [
        SkillDownloadPreparedFile(
            path="SKILL.md",
            sha256="5" * 64,
            size_bytes=42,
            content_type="text/markdown; charset=utf-8",
            download_url="https://downloads.example/SKILL.md?signature=one",
            expires_at=expires_at,
        ),
        SkillDownloadPreparedFile(
            path="scripts/helper.py",
            sha256="6" * 64,
            size_bytes=84,
            content_type="text/x-python; charset=utf-8",
            download_url="https://downloads.example/helper.py?signature=two",
            expires_at=expires_at,
        ),
    ]

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def prepare_draft_download(self, *, skill_id, url_expiry_seconds):
            assert skill_id == expected_skill_id
            assert (
                url_expiry_seconds
                == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
            )
            return SkillDownloadPreparedResponse(
                workspace_id=workspace_id,
                skill_id=skill_id,
                skill_name="triage-skill",
                draft_revision=7,
                files=files,
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.prepare_skill_download)(
        workspace_id=str(workspace_id),
        skill_id=skill_id,
        ctx=_fake_ctx(),
    )

    payload = _payload(result)
    assert payload["skill_id"] == str(skill_id)
    assert payload["skill_name"] == "triage-skill"
    assert payload["draft_revision"] == 7
    assert [file["download_url"] for file in payload["files"]] == [
        "https://downloads.example/SKILL.md?signature=one",
        "https://downloads.example/helper.py?signature=two",
    ]
    assert [file["sha256"] for file in payload["files"]] == ["5" * 64, "6" * 64]


@pytest.mark.anyio
async def test_prepare_skill_download_requires_agent_read_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, scopes=frozenset())

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    def _unexpected_with_session(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("SkillService must not be opened without agent:read")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        _unexpected_with_session,
    )

    with pytest.raises(ToolError, match="Missing required scope: agent:read"):
        await _tool(mcp_server.prepare_skill_download)(
            workspace_id=str(workspace_id),
            skill_id=uuid.uuid4(),
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
async def test_prepare_skill_upload_creates_skill_and_presigned_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:create", "agent:update"}),
    )
    now = datetime.now(UTC)
    upload_ids = [uuid.uuid4(), uuid.uuid4()]
    captured: dict[str, Any] = {"uploads": []}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def prepare_new_skill_draft_uploads(
            self,
            *,
            skill_params,
            params,
            url_expiry_seconds,
        ):
            captured["create"] = skill_params
            captured["uploads"] = list(params)
            assert (
                url_expiry_seconds
                == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
            )
            return SkillUploadSessionBatchRead(
                skill_id=skill_id,
                draft_revision=4,
                created=True,
                uploads=[
                    SkillUploadSessionRead(
                        upload_id=upload_ids[index],
                        upload_url=(
                            f"https://uploads.example/{index}?signature=secret"
                        ),
                        headers={
                            "Content-Type": upload.content_type,
                            "Content-Length": str(upload.size_bytes),
                        },
                        expires_at=now + timedelta(minutes=5),
                        bucket="skills",
                        key=f"staged/{index}",
                    )
                    for index, upload in enumerate(params)
                ],
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.prepare_skill_upload)(
        workspace_id=str(workspace_id),
        name="triage-skill",
        description="Triage alerts",
        files=[
            mcp_server.SkillUploadFileMetadata(
                path="SKILL.md",
                sha256="A" * 64,
                size_bytes=42,
                content_type="text/markdown; charset=utf-8",
            ),
            mcp_server.SkillUploadFileMetadata(
                path="scripts/__init__.py",
                sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                size_bytes=0,
                content_type="text/x-python; charset=utf-8",
            ),
        ],
        ctx=_fake_ctx(),
    )

    payload = _payload(result)
    assert captured["create"].name == "triage-skill"
    assert captured["uploads"][1].size_bytes == 0
    assert payload["workspace_id"] == str(workspace_id)
    assert payload["skill_id"] == str(skill_id)
    assert payload["base_revision"] == 4
    assert payload["created"] is True
    assert payload["files"][0]["sha256"] == "a" * 64
    assert payload["files"][0]["upload_id"] == str(upload_ids[0])
    assert payload["files"][0]["upload_url"].startswith("https://uploads.example/")
    assert "bucket" not in payload["files"][0]
    assert "key" not in payload["files"][0]


@pytest.mark.anyio
async def test_prepare_skill_upload_reuses_existing_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    upload_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:update"}),
    )
    now = datetime.now(UTC)
    captured: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def prepare_draft_uploads(
            self,
            *,
            skill_id,
            params,
            url_expiry_seconds,
        ):
            captured["skill_id"] = skill_id
            captured["params"] = list(params)
            assert (
                url_expiry_seconds
                == mcp_server.TRACECAT_MCP__FILE_TRANSFER_URL_EXPIRY_SECONDS
            )
            return SkillUploadSessionBatchRead(
                skill_id=skill_id,
                draft_revision=6,
                created=False,
                uploads=[
                    SkillUploadSessionRead(
                        upload_id=upload_id,
                        upload_url=("https://uploads.example/skill?signature=secret"),
                        headers={
                            "Content-Type": params[0].content_type,
                            "Content-Length": str(params[0].size_bytes),
                        },
                        expires_at=now + timedelta(minutes=5),
                        bucket="skills",
                        key="staged/skill",
                    )
                ],
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.prepare_skill_upload)(
        workspace_id=str(workspace_id),
        skill_id=skill_id,
        files=[
            mcp_server.SkillUploadFileMetadata(
                path="SKILL.md",
                sha256="a" * 64,
                size_bytes=42,
                content_type="text/markdown; charset=utf-8",
            )
        ],
        ctx=_fake_ctx(),
    )

    payload = _payload(result)
    assert captured["skill_id"] == skill_id
    assert payload["skill_id"] == str(skill_id)
    assert payload["base_revision"] == 6
    assert payload["created"] is False


@pytest.mark.anyio
async def test_prepare_skill_upload_checks_update_scope_before_creating_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:create"}),
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    def _unexpected_with_session(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("SkillService must not be opened before all scopes are checked")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        _unexpected_with_session,
    )

    with pytest.raises(ToolError, match="Missing required scope: agent:update"):
        await _tool(mcp_server.prepare_skill_upload)(
            workspace_id=str(workspace_id),
            name="triage-skill",
            files=[
                mcp_server.SkillUploadFileMetadata(
                    path="SKILL.md",
                    sha256="a" * 64,
                    size_bytes=42,
                    content_type="text/markdown; charset=utf-8",
                )
            ],
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "paths",
    [
        ["SKILL.md", "references", "references/example.md"],
        ["SKILL.md", "references/example.md", "references"],
    ],
)
async def test_prepare_skill_upload_rejects_path_prefix_collisions_before_service(
    monkeypatch: pytest.MonkeyPatch,
    paths: list[str],
) -> None:
    def _unexpected_with_session(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("SkillService must not open for a path prefix collision")

    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        _unexpected_with_session,
    )

    files = [
        mcp_server.SkillUploadFileMetadata(
            path=path,
            sha256="a" * 64,
            size_bytes=1,
            content_type="text/plain",
        )
        for path in paths
    ]
    with pytest.raises(ToolError, match="conflicts with file path 'references'"):
        await _tool(mcp_server.prepare_skill_upload)(
            workspace_id=str(uuid.uuid4()),
            name="triage-skill",
            files=files,
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
async def test_prepare_skill_upload_rejects_oversized_manifest_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_server.config,
        "TRACECAT__MAX_SKILL_MANIFEST_SIZE_BYTES",
        1,
    )

    def _unexpected_with_session(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("SkillService must not open for an oversized manifest")

    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        _unexpected_with_session,
    )

    with pytest.raises(ToolError, match="Root SKILL.md exceeds the size limit"):
        await _tool(mcp_server.prepare_skill_upload)(
            workspace_id=str(uuid.uuid4()),
            name="triage-skill",
            files=[
                mcp_server.SkillUploadFileMetadata(
                    path="SKILL.md",
                    sha256="a" * 64,
                    size_bytes=2,
                    content_type="text/markdown; charset=utf-8",
                )
            ],
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
async def test_complete_skill_upload_replaces_the_entire_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:update"}),
    )
    skill_md_upload_id = uuid.uuid4()
    helper_upload_id = uuid.uuid4()
    existing_files = [
        SkillFileEntry(
            path="SKILL.md",
            blob_id=uuid.uuid4(),
            sha256="1" * 64,
            size_bytes=10,
            content_type="text/markdown; charset=utf-8",
        ),
        SkillFileEntry(
            path="references/obsolete.md",
            blob_id=uuid.uuid4(),
            sha256="2" * 64,
            size_bytes=20,
            content_type="text/markdown; charset=utf-8",
        ),
    ]
    captured: dict[str, Any] = {}

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def get_draft(self, requested_skill_id):
            assert requested_skill_id == skill_id
            return SkillDraftRead(
                skill_id=skill_id,
                skill_name="triage-skill",
                draft_revision=7,
                name="triage-skill",
                description="Triage alerts",
                files=existing_files,
                is_publishable=True,
                validation_errors=[],
            )

        async def patch_draft(self, *, skill_id, params):
            captured["skill_id"] = skill_id
            captured["params"] = params
            return SkillDraftRead(
                skill_id=skill_id,
                skill_name="triage-skill",
                draft_revision=8,
                name="triage-skill",
                description="Triage alerts",
                files=[],
                is_publishable=True,
                validation_errors=[],
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    result = await _tool(mcp_server.complete_skill_upload)(
        workspace_id=str(workspace_id),
        skill_id=skill_id,
        base_revision=7,
        files=[
            mcp_server.SkillUploadedFile(path="SKILL.md", upload_id=skill_md_upload_id),
            mcp_server.SkillUploadedFile(
                path="scripts/helper.py", upload_id=helper_upload_id
            ),
        ],
        ctx=_fake_ctx(),
    )

    payload = _payload(result)
    params = captured["params"]
    assert captured["skill_id"] == skill_id
    assert params.base_revision == 7
    assert isinstance(params.operations[0], SkillDraftDeleteFileOp)
    assert params.operations[0].path == "references/obsolete.md"
    assert isinstance(params.operations[1], SkillDraftAttachUploadedBlobOp)
    assert params.operations[1].path == "SKILL.md"
    assert params.operations[1].upload_id == skill_md_upload_id
    assert isinstance(params.operations[2], SkillDraftAttachUploadedBlobOp)
    assert params.operations[2].path == "scripts/helper.py"
    assert params.operations[2].upload_id == helper_upload_id
    assert payload["draft_revision"] == 8


@pytest.mark.anyio
async def test_complete_skill_upload_reports_current_revision_on_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    role = SimpleNamespace(
        workspace_id=workspace_id,
        scopes=frozenset({"agent:update"}),
    )

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    class _SkillService:
        async def get_draft(self, requested_skill_id):
            assert requested_skill_id == skill_id
            return SkillDraftRead(
                skill_id=skill_id,
                skill_name="triage-skill",
                draft_revision=9,
                name="triage-skill",
                description=None,
                files=[],
                is_publishable=True,
                validation_errors=[],
            )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        lambda role: _AsyncContext(_SkillService()),
    )

    with pytest.raises(
        ToolError,
        match="draft_revision_conflict.*current_revision.*9",
    ):
        await _tool(mcp_server.complete_skill_upload)(
            workspace_id=str(workspace_id),
            skill_id=skill_id,
            base_revision=8,
            files=[
                mcp_server.SkillUploadedFile(path="SKILL.md", upload_id=uuid.uuid4())
            ],
            ctx=_fake_ctx(),
        )


@pytest.mark.anyio
async def test_complete_skill_upload_checks_update_scope_before_reading_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id, scopes=frozenset())

    async def _resolve(_workspace_id: str) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    def _unexpected_with_session(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("SkillService must not be opened before all scopes are checked")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.SkillService,
        "with_session",
        _unexpected_with_session,
    )

    with pytest.raises(ToolError, match="Missing required scope: agent:update"):
        await _tool(mcp_server.complete_skill_upload)(
            workspace_id=str(workspace_id),
            skill_id=skill_id,
            base_revision=7,
            files=[
                mcp_server.SkillUploadedFile(path="SKILL.md", upload_id=uuid.uuid4())
            ],
            ctx=_fake_ctx(),
        )


def _prompt_source_text() -> str:
    return "\n".join(
        [
            mcp_server._MCP_INSTRUCTIONS_RENDERED,
            mcp_server._DSL_REFERENCE_TEXT,
            mcp_server._AUTHORING_GUIDE_TEXT,
            inspect.cleandoc(mcp_server.edit_workflow.__doc__ or ""),
        ]
    )


def _prompt_fenced_blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)\n```", _prompt_source_text(), re.DOTALL)


def _prompt_yaml_action_fragments() -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for block in _prompt_fenced_blocks("yaml"):
        parsed = yaml.safe_load(block)
        if isinstance(parsed, list):
            fragments.extend(
                item
                for item in parsed
                if isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and isinstance(item.get("action"), str)
            )
        elif isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
            fragments.extend(
                item
                for item in parsed["actions"]
                if isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and isinstance(item.get("action"), str)
            )
        elif (
            isinstance(parsed, dict)
            and isinstance(parsed.get("definition"), dict)
            and isinstance(parsed["definition"].get("actions"), list)
        ):
            fragments.extend(
                item
                for item in parsed["definition"]["actions"]
                if isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and isinstance(item.get("action"), str)
            )
        elif (
            isinstance(parsed, dict)
            and isinstance(parsed.get("ref"), str)
            and isinstance(parsed.get("action"), str)
        ):
            fragments.append(parsed)
    return fragments


def test_prompt_json_patch_examples_are_structurally_valid() -> None:
    examples = [
        parsed
        for block in _prompt_fenced_blocks("json")
        if isinstance(parsed := json.loads(block), dict) and parsed.get("patch_ops")
    ]

    assert len(examples) >= 1
    for example in examples:
        for op in example["patch_ops"]:
            assert op["op"] in {"add", "copy", "move", "remove", "replace", "test"}
            path_parts = [part for part in op["path"].split("/") if part]
            assert path_parts[0] in {"definition", "layout"}


def test_prompt_complete_workflow_yaml_examples_are_schema_valid() -> None:
    complete_examples = []
    for block in _prompt_fenced_blocks("yaml"):
        parsed = yaml.safe_load(block)
        if isinstance(parsed, dict) and parsed.get("definition") is not None:
            complete_examples.append(parsed)

    assert complete_examples
    for block in complete_examples:
        payload = mcp_server.WorkflowYamlPayload.model_validate(block)
        assert payload.definition is not None
        assert payload.definition.entrypoint.expects
        assert payload.definition.actions
        assert payload.definition.returns


def test_prompt_action_args_match_registry_signatures() -> None:
    from tracecat_registry.core.http import http_paginate, http_request
    from tracecat_registry.core.python import run_python

    action_functions = {
        "core.script.run_python": run_python,
        "core.http_request": http_request,
        "core.http_paginate": http_paginate,
    }

    seen_actions: set[str] = set()
    for fragment in _prompt_yaml_action_fragments():
        if (action_name := fragment["action"]) not in action_functions:
            continue
        seen_actions.add(action_name)
        action_refs = set(
            re.findall(r"ACTIONS\.([A-Za-z_][A-Za-z0-9_]*)", json.dumps(fragment))
        )
        upstream_stubs = [
            {"ref": ref, "action": "core.transform.reshape", "args": {"value": {}}}
            for ref in sorted(action_refs - {fragment["ref"]})
        ]
        actions = [*upstream_stubs, fragment]
        dsl = mcp_server.DSLInput.model_validate(
            {
                "title": f"Prompt eval {fragment['ref']}",
                "description": "Validate MCP prompt action fragment.",
                "entrypoint": {"ref": actions[0]["ref"], "expects": {}},
                "actions": actions,
            }
        )
        assert dsl.actions[-1].ref == fragment["ref"]

        signature = inspect.signature(action_functions[fragment["action"]])
        params = set(signature.parameters)
        args = set(fragment["args"])
        required = {
            name
            for name, param in signature.parameters.items()
            if param.default is inspect.Parameter.empty
            and param.kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        }
        assert args <= params
        assert required <= args

    assert seen_actions == set(action_functions)


def test_prompt_expressions_respect_prompt_action_result_shapes() -> None:
    action_refs = {
        fragment["ref"]: fragment["action"]
        for fragment in _prompt_yaml_action_fragments()
    }
    list_result_refs = {
        ref
        for ref, action_name in action_refs.items()
        if action_name == "core.http_paginate"
    }

    invalid_dereferences = []
    for ref in list_result_refs:
        invalid_dereferences.extend(
            re.findall(
                rf"ACTIONS\.{re.escape(ref)}\.result\.([A-Za-z_][A-Za-z0-9_]*)",
                _prompt_source_text(),
            )
        )

    assert not invalid_dereferences


def test_mcp_instruction_text_stays_within_context_budget() -> None:
    # The rendered skill-transfer warning is longer than its placeholder and is
    # required guidance for clients that cannot read local skill directories.
    assert len(mcp_server._MCP_INSTRUCTIONS_RENDERED) <= 10000, (
        "MCP instructions exceeded the prompt budget. Always-on instructions "
        "carry only rules that fail hard when violated; move long-form guidance "
        "into the `tracecat://platform/authoring-guide` resource or a tool "
        "docstring, or intentionally raise this ceiling with a clear reason."
    )


def test_mcp_instruction_named_placeholders_are_all_rendered() -> None:
    assert not re.findall(
        r"\{_[A-Z][A-Z0-9_]*\}",
        mcp_server._MCP_INSTRUCTIONS_RENDERED,
    )
    assert mcp_server._SKILL_FILE_WARNING in mcp_server._MCP_INSTRUCTIONS_RENDERED


def test_dsl_reference_text_stays_within_context_budget() -> None:
    # Raised from 15500 for the "Form Trigger" section.
    assert len(mcp_server._DSL_REFERENCE_TEXT) <= 17900


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

    async def _new(
        *,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        stream_id: uuid.UUID | None = None,
    ) -> _Stream:
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

    async def _new(
        *,
        session_id: uuid.UUID,
        workspace_id: uuid.UUID,
        stream_id: uuid.UUID | None = None,
    ) -> _Stream:
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


def _minimal_edit_document_payload() -> dict[str, Any]:
    """A minimal valid WorkflowEditDocument payload: trigger -> a -> b."""
    return {
        "metadata": {"title": "Test workflow", "description": "", "status": "offline"},
        "definition": {
            "entrypoint": {"ref": "a", "expects": {}},
            "actions": [
                {"ref": "a", "action": "core.transform.reshape", "args": {"value": 1}},
                {"ref": "b", "action": "core.transform.reshape", "args": {"value": 2}},
            ],
        },
    }


def test_validate_patch_payload_accepts_error_edge_dependency() -> None:
    """`depends_on: ['<ref>.error']` is the supported on-failure branch syntax."""
    payload = _minimal_edit_document_payload()
    # Run a new action only when action `a` fails.
    payload["definition"]["actions"].append(
        {
            "ref": "a_error_handler",
            "action": "core.transform.reshape",
            "args": {"value": "handled"},
            "depends_on": ["a.error"],
        }
    )

    document = draft.validate_workflow_patch_payload(payload)

    handler = next(
        action
        for action in document.definition.actions
        if action.ref == "a_error_handler"
    )
    assert handler.depends_on == ["a.error"]


def test_validate_patch_payload_rejects_invented_on_error_field() -> None:
    """An invented `on_error` action field yields a structured 400-able error.

    Regression: this previously escaped as a raw pydantic ValidationError and
    surfaced to the agent as an opaque 500. It must now be a WorkflowEditError
    that names the offending field and points at the real error-edge syntax.
    """
    payload = _minimal_edit_document_payload()
    payload["definition"]["actions"][0]["on_error"] = "a_error_handler"

    with pytest.raises(draft.WorkflowEditError) as exc_info:
        draft.validate_workflow_patch_payload(payload)

    error = exc_info.value
    assert error.code == "validation_error"
    assert error.details is not None
    assert error.details["type"] == "validation_error"
    # The structured errors pinpoint the offending field.
    locs = {tuple(item["loc"]) for item in error.details["errors"]}
    assert ("definition", "actions", 0, "on_error") in locs
    # The message steers the model to the real error-edge syntax.
    assert "depends_on" in error.message
    assert ".error" in error.message


@pytest.mark.anyio
async def test_request_audit_middleware_sets_context_during_tool_call(
    monkeypatch: pytest.MonkeyPatch,
):
    from types import SimpleNamespace

    from tracecat.contexts import RequestAuditContext, ctx_request_audit
    from tracecat.mcp import middleware as mcp_middleware

    fake_request = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1", "User-Agent": "curl/8.5"},
        client=SimpleNamespace(host="10.0.0.2"),
    )
    monkeypatch.setattr(mcp_middleware, "get_http_request", lambda: fake_request)
    mw = mcp_middleware.MCPRequestAuditMiddleware()
    sentinel = object()
    seen: dict[str, RequestAuditContext | None] = {}

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        seen["audit"] = ctx_request_audit.get()
        return cast(ToolResult, sentinel)

    result = await mw.on_call_tool(_make_tool_context(), _call_next)
    assert result is sentinel
    audit = seen["audit"]
    assert audit is not None
    assert audit.client_ip == "203.0.113.7"
    assert audit.user_agent == "curl/8.5"
    assert ctx_request_audit.get() is None


@pytest.mark.anyio
async def test_request_audit_middleware_tolerates_missing_http_request(
    monkeypatch: pytest.MonkeyPatch,
):
    from tracecat.contexts import ctx_request_audit
    from tracecat.mcp import middleware as mcp_middleware

    def _raise() -> object:
        raise RuntimeError("no active HTTP request")

    monkeypatch.setattr(mcp_middleware, "get_http_request", _raise)
    mw = mcp_middleware.MCPRequestAuditMiddleware()
    sentinel = object()

    async def _call_next(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> ToolResult:
        assert ctx_request_audit.get() is None
        return cast(ToolResult, sentinel)

    result = await mw.on_call_tool(_make_tool_context(), _call_next)
    assert result is sentinel


# ---------------------------------------------------------------------------
# Execution results: filtering, result stripping, and per-action windows
# ---------------------------------------------------------------------------


def _compact_event(
    ref: str,
    *,
    result: Any,
    source_event_id: int = 1,
    stream_id: StreamID = ROOT_STREAM,
) -> WorkflowExecutionEventCompact[Any, Any, Any]:
    now = datetime.now(UTC)
    return WorkflowExecutionEventCompact(
        source_event_id=source_event_id,
        schedule_time=now,
        start_time=now,
        close_time=now,
        curr_event_type=WorkflowEventType.ACTIVITY_TASK_COMPLETED,
        status=WorkflowExecutionEventStatus.COMPLETED,
        action_name="core.transform.reshape",
        action_ref=ref,
        action_result=result,
        stream_id=stream_id,
    )


def _patch_execution_read(
    monkeypatch,
    events: list[WorkflowExecutionEventCompact[Any, Any, Any]],
) -> str:
    """Fake the workspace, workflow, and execution lookups; return an exec id."""
    wf_id = mcp_server.WorkflowUUID.new(uuid.uuid4())
    execution_id = f"{wf_id.short()}:exec-{uuid.uuid4().hex[:8]}"

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    class _WorkflowService:
        async def get_workflow(self, _wf_id):
            return SimpleNamespace(id=wf_id)

    class _ExecService:
        async def get_execution(self, _execution_id):
            return SimpleNamespace(
                id=execution_id,
                run_id=uuid.uuid4(),
                status=WorkflowExecutionStatus.COMPLETED,
                start_time=datetime.now(UTC),
                close_time=datetime.now(UTC),
                typed_search_attributes=None,
                history_length=len(events),
            )

        async def list_workflow_execution_events_compact(self, _execution_id):
            return events

    async def _connect(*, role):
        return _ExecService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(mcp_server.WorkflowExecutionsService, "connect", _connect)
    return execution_id


def test_window_result_text_pages_by_bytes() -> None:
    text = "0123456789"

    first = window_result_text(text, offset=0, max_bytes=4)
    assert (first.result, first.total_bytes, first.offset) == ("0123", 10, 0)
    assert first.truncated is True
    assert first.next_offset == 4

    last = window_result_text(text, offset=8, max_bytes=4)
    assert last.result == "89"
    assert last.truncated is False
    assert last.next_offset is None

    beyond = window_result_text(text, offset=50, max_bytes=4)
    assert (beyond.result, beyond.offset, beyond.truncated) == ("", 10, False)


def test_window_result_text_never_splits_multibyte_characters() -> None:
    text = "abécd"  # "é" is two bytes in UTF-8

    window = window_result_text(text, offset=0, max_bytes=3)
    assert window.result == "ab"
    assert window.next_offset == 2

    rest = window_result_text(text, offset=2, max_bytes=100)
    assert rest.result == "écd"
    assert rest.truncated is False


def test_select_execution_events_keeps_synthetic_workflow_events() -> None:
    events = [
        _compact_event(WF_TRIGGER_REF, result=None),
        _compact_event("fetch_events", result=[1]),
        _compact_event("classify", result="ok"),
        _compact_event(WF_COMPLETED_REF, result=None),
    ]

    kept = select_execution_events(events, action_refs=["classify"])
    assert [event.action_ref for event in kept] == [
        WF_TRIGGER_REF,
        "classify",
        WF_COMPLETED_REF,
    ]
    assert select_execution_events(events, action_refs=None) == events


@pytest.mark.anyio
async def test_get_workflow_execution_filters_refs_and_strips_results(
    monkeypatch,
):
    events = [
        _compact_event(WF_TRIGGER_REF, result=None, source_event_id=1),
        _compact_event("fetch_events", result={"count": 2}, source_event_id=2),
        _compact_event("classify", result={"label": "benign"}, source_event_id=3),
    ]
    execution_id = _patch_execution_read(monkeypatch, events)

    full = _payload(
        await _tool(mcp_server.get_workflow_execution)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_refs=["classify"],
        )
    )
    assert [event["action_ref"] for event in full["events"]] == [
        WF_TRIGGER_REF,
        "classify",
    ]
    assert full["events"][1]["result"] == {"label": "benign"}

    lean = _payload(
        await _tool(mcp_server.get_workflow_execution)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            include_results=False,
        )
    )
    assert len(lean["events"]) == 3
    assert all(event["result"] is None for event in lean["events"])
    assert all(event["result_truncated"] is None for event in lean["events"])
    assert lean["events"][2]["status"] == "COMPLETED"


def _patch_execution_cancel(
    monkeypatch,
    *,
    scopes: frozenset[str],
    workflow_in_workspace: bool = True,
    cancel_error: Exception | None = None,
) -> tuple[str, dict[str, Any]]:
    """Fake the role, workflow lookup, and cancel call; return an exec id."""
    wf_id = mcp_server.WorkflowUUID.new(uuid.uuid4())
    execution_id = f"{wf_id.short()}:exec-{uuid.uuid4().hex[:8]}"
    calls: dict[str, Any] = {"get_workflow": [], "cancel": []}

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(scopes=scopes)

    class _WorkflowService:
        async def get_workflow(self, requested_wf_id):
            calls["get_workflow"].append(requested_wf_id)
            return SimpleNamespace(id=wf_id) if workflow_in_workspace else None

    class _ExecService:
        async def cancel_workflow_execution(self, requested_execution_id):
            calls["cancel"].append(requested_execution_id)
            if cancel_error is not None:
                raise cancel_error

    async def _connect(*, role):
        return _ExecService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(mcp_server.WorkflowExecutionsService, "connect", _connect)
    return execution_id, calls


@pytest.mark.anyio
async def test_cancel_workflow_execution_requests_cancellation(monkeypatch):
    execution_id, calls = _patch_execution_cancel(
        monkeypatch, scopes=frozenset({"workflow:terminate"})
    )

    payload = _payload(
        await _tool(mcp_server.cancel_workflow_execution)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
        )
    )

    assert payload["execution_id"] == execution_id
    assert "Cancellation requested" in payload["message"]
    assert calls["cancel"] == [execution_id]
    assert len(calls["get_workflow"]) == 1


@pytest.mark.anyio
async def test_cancel_workflow_execution_requires_terminate_scope(monkeypatch):
    execution_id, calls = _patch_execution_cancel(
        monkeypatch,
        scopes=frozenset({"workflow:read", "workflow:execute"}),
    )

    with pytest.raises(ToolError, match="Missing required scope: workflow:terminate"):
        await _tool(mcp_server.cancel_workflow_execution)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
        )

    assert calls["get_workflow"] == []
    assert calls["cancel"] == []


@pytest.mark.anyio
async def test_cancel_workflow_execution_rejects_other_workspace(monkeypatch):
    execution_id, calls = _patch_execution_cancel(
        monkeypatch,
        scopes=frozenset({"workflow:terminate"}),
        workflow_in_workspace=False,
    )

    with pytest.raises(ToolError, match="not found in workspace"):
        await _tool(mcp_server.cancel_workflow_execution)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
        )

    assert calls["cancel"] == []


@pytest.mark.anyio
async def test_cancel_workflow_execution_surfaces_missing_execution(monkeypatch):
    execution_id, _ = _patch_execution_cancel(
        monkeypatch,
        scopes=frozenset({"workflow:terminate"}),
        cancel_error=WorkflowExecutionNotFoundError("Workflow execution not found"),
    )

    with pytest.raises(ToolError, match="Workflow execution not found"):
        await _tool(mcp_server.cancel_workflow_execution)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
        )


@pytest.mark.anyio
async def test_get_execution_action_result_windows_full_inline_result(
    monkeypatch,
):
    findings = [{"id": index, "note": "x" * 100} for index in range(50)]
    execution_id = _patch_execution_read(
        monkeypatch, [_compact_event("build_alert", result=findings)]
    )
    expected = json.dumps(findings, default=str)
    assert len(expected) > MAX_EVENT_RESULT_CHARS

    chunks: list[str] = []
    offset = 0
    while True:
        payload = _payload(
            await _tool(mcp_server.get_execution_action_result)(
                workspace_id=str(uuid.uuid4()),
                execution_id=execution_id,
                action_ref="build_alert",
                max_bytes=2048,
                offset=offset,
            )
        )
        assert payload["total_bytes"] == len(expected.encode())
        assert payload["offset"] == offset
        assert payload["stream_id"] == str(ROOT_STREAM)
        assert payload["status"] == "COMPLETED"
        chunks.append(payload["result"])
        if not payload["truncated"]:
            assert payload["next_offset"] is None
            break
        assert payload["next_offset"] == offset + 2048
        offset = payload["next_offset"]

    assert len(chunks) > 1
    assert json.loads("".join(chunks)) == findings


@pytest.mark.anyio
async def test_get_execution_action_result_clamps_max_bytes(monkeypatch):
    execution_id = _patch_execution_read(
        monkeypatch, [_compact_event("build_alert", result="y" * 10)]
    )

    payload = _payload(
        await _tool(mcp_server.get_execution_action_result)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_ref="build_alert",
            max_bytes=0,
        )
    )
    # Clamped up to the configured minimum limit rather than returning nothing.
    assert len(payload["result"]) == mcp_server.config.TRACECAT__LIMIT_MIN
    assert payload["truncated"] is True

    payload = _payload(
        await _tool(mcp_server.get_execution_action_result)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_ref="build_alert",
            max_bytes=10**9,
        )
    )
    assert payload["truncated"] is False


@pytest.mark.anyio
async def test_get_execution_action_result_unknown_ref_lists_available(
    monkeypatch,
):
    execution_id = _patch_execution_read(
        monkeypatch,
        [
            _compact_event("fetch_events", result=1, source_event_id=1),
            _compact_event("classify", result=2, source_event_id=2),
        ],
    )

    with pytest.raises(ToolError, match="No event for action ref 'ghost'") as exc:
        await _tool(mcp_server.get_execution_action_result)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_ref="ghost",
        )
    assert "'classify'" in str(exc.value)
    assert "'fetch_events'" in str(exc.value)


@pytest.mark.anyio
async def test_get_execution_action_result_requires_stream_id_for_scatter(
    monkeypatch,
):
    stream_a = StreamID.new("scatter_items", 0)
    stream_b = StreamID.new("scatter_items", 1)
    execution_id = _patch_execution_read(
        monkeypatch,
        [
            _compact_event(
                "classify", result="a", source_event_id=1, stream_id=stream_a
            ),
            _compact_event(
                "classify", result="b", source_event_id=2, stream_id=stream_b
            ),
        ],
    )

    with pytest.raises(ToolError, match="pass stream_id") as exc:
        await _tool(mcp_server.get_execution_action_result)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_ref="classify",
        )
    assert str(stream_a) in str(exc.value)
    assert str(stream_b) in str(exc.value)

    payload = _payload(
        await _tool(mcp_server.get_execution_action_result)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_ref="classify",
            stream_id=str(stream_b),
        )
    )
    assert payload["result"] == '"b"'
    assert payload["stream_id"] == str(stream_b)

    with pytest.raises(ToolError, match="in stream 'nope'"):
        await _tool(mcp_server.get_execution_action_result)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_ref="classify",
            stream_id="nope",
        )


@pytest.mark.anyio
async def test_get_execution_action_result_dereferences_external_result(
    monkeypatch,
):
    external = ExternalObject(
        ref=ObjectRef(
            bucket="placeholder-bucket",
            key="placeholder/key.json",
            size_bytes=1,
            sha256="0" * 64,
        )
    )
    stored_payload = {"summary": "s" * 5000, "items": list(range(20))}
    loaded: list[Any] = []

    async def _fake_retrieve(stored):
        loaded.append(stored)
        return stored_payload

    monkeypatch.setattr(mcp_server, "retrieve_stored_object", _fake_retrieve)
    execution_id = _patch_execution_read(
        monkeypatch, [_compact_event("classify", result=external)]
    )

    payload = _payload(
        await _tool(mcp_server.get_execution_action_result)(
            workspace_id=str(uuid.uuid4()),
            execution_id=execution_id,
            action_ref="classify",
            max_bytes=1024 * 1024,
        )
    )

    assert loaded == [external]
    assert payload["truncated"] is False
    assert json.loads(payload["result"]) == stored_payload


# ---------------------------------------------------------------------------
# Form tools
# ---------------------------------------------------------------------------

_FORM_BUILDER_SCOPES = frozenset(
    {"form:read", "form:create", "form:update", "form:submission:read"}
)
_FORM_TOOL_ARGUMENTS: dict[str, dict[str, Any]] = {
    "list_forms": {},
    "get_form": {"form_id": str(uuid.uuid4())},
    "update_form": {"workflow_id": str(uuid.uuid4())},
}


def _patch_forms(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scopes: frozenset[str] = _FORM_BUILDER_SCOPES,
    flag: bool = True,
    entitled: bool = True,
    **methods: Any,
) -> SimpleNamespace:
    """Stand in for the forms service behind the MCP form tools."""
    role = Role(
        type="user",
        service_id="tracecat-mcp",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=scopes,
    )

    async def _resolve(workspace_id):
        return workspace_id, role

    async def _has_entitlement(entitlement):
        return entitled and entitlement is mcp_server.Entitlement.FORMS

    service = SimpleNamespace(
        role=role, has_entitlement=_has_entitlement, opened=False, **methods
    )

    def _with_session(role):
        service.opened = True
        return _AsyncContext(service)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server.FormsService, "with_session", _with_session)
    monkeypatch.setattr(
        mcp_server.config,
        "TRACECAT__FEATURE_FLAGS",
        {mcp_server.FeatureFlag.FORMS} if flag else set(),
    )
    return service


async def _call_form_tool(name: str, **arguments: Any) -> Any:
    return await _tool(getattr(mcp_server, name))(
        workspace_id=str(uuid.uuid4()), **(_FORM_TOOL_ARGUMENTS[name] | arguments)
    )


@pytest.mark.anyio
async def test_form_tools_are_registered_without_submission_tools() -> None:
    tools = {tool.name for tool in await mcp_server.mcp.list_tools()}

    assert set(_FORM_TOOL_ARGUMENTS) <= tools
    # Submissions stay off the external MCP server.
    assert not [name for name in tools if "submission" in name]


@pytest.mark.anyio
@pytest.mark.parametrize("tool_name", list(_FORM_TOOL_ARGUMENTS))
@pytest.mark.parametrize(
    ("gate", "error"),
    [
        ({"flag": False}, "not enabled"),
        ({"entitled": False}, "Feature 'forms' requires"),
        # Scope order in the message is not stable.
        (
            {"scopes": frozenset({"workflow:read"})},
            "Missing required scope: .*form:read",
        ),
    ],
    ids=["flag-off", "no-entitlement", "no-scope"],
)
async def test_form_tools_are_gated(
    monkeypatch: pytest.MonkeyPatch, tool_name: str, gate: dict[str, Any], error: str
) -> None:
    # The stand-in service has no methods: a call that got past the gate
    # would fail with a different message.
    service = _patch_forms(monkeypatch, **gate)

    with pytest.raises(ToolError, match=error):
        await _call_form_tool(tool_name)

    assert service.opened is ("flag" not in gate)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scopes", "found"),
    [(_FORM_BUILDER_SCOPES, True), (frozenset({"form:read", "form:submit"}), False)],
    ids=["builder", "intake-only"],
)
async def test_get_form_returns_the_config_only_to_a_builder(
    monkeypatch: pytest.MonkeyPatch, scopes: frozenset[str], found: bool
) -> None:
    form, read = object(), object()
    get_form = AsyncMock(return_value=form)
    read_form = AsyncMock(return_value=read)
    _patch_forms(monkeypatch, scopes=scopes, get_form=get_form, read_form=read_form)

    if found:
        assert await _call_form_tool("get_form") is read
        read_form.assert_awaited_once_with(form)
    else:
        # Same answer as for no form: the caller learns nothing.
        with pytest.raises(ToolError, match="not found"):
            await _call_form_tool("get_form")
        get_form.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exists", "scopes"),
    [
        (True, {"form:read", "form:update"}),
        (False, {"form:read", "form:update"}),
        (False, {"form:read", "form:update", "form:create"}),
    ],
    ids=["update", "create-without-form-create", "create"],
)
async def test_update_form_upserts_and_needs_form_create_only_to_create(
    monkeypatch: pytest.MonkeyPatch, exists: bool, scopes: set[str]
) -> None:
    workflow_id, form, read = uuid.uuid4(), object(), object()
    upsert_form = AsyncMock(return_value=form)
    _patch_forms(
        monkeypatch,
        scopes=frozenset(scopes),
        get_form_by_workflow_id=AsyncMock(return_value=form if exists else None),
        upsert_form=upsert_form,
        read_form=AsyncMock(return_value=read),
    )
    config = mcp_server.FormConfig(title="Laptop request")
    call = _call_form_tool(
        "update_form", workflow_id=str(workflow_id), status="online", config=config
    )

    if not exists and "form:create" not in scopes:
        with pytest.raises(ToolError, match="Missing required scope: form:create"):
            await call
        upsert_form.assert_not_awaited()
        return
    assert await call is read
    upsert_form.assert_awaited_once_with(
        workflow_id,
        mcp_server.FormUpdate(status=mcp_server.FormStatus.ONLINE, config=config),
    )


@pytest.mark.anyio
async def test_form_tool_reports_config_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.forms.enums import FormErrorCode, FormIssueCode, FormIssueSeverity
    from tracecat.forms.schemas import FormIssue

    issue = FormIssue(
        severity=FormIssueSeverity.ERROR,
        code=FormIssueCode.UNKNOWN_FIELD,
        message="The workflow has no input named 'laptop'",
        loc=["layout", 0, "field"],
    )
    error = mcp_server.FormError(
        FormErrorCode.FORM_CONFIG_INVALID, "The form config has errors", issues=[issue]
    )
    _patch_forms(
        monkeypatch,
        get_form_by_workflow_id=AsyncMock(return_value=object()),
        upsert_form=AsyncMock(side_effect=error),
    )

    with pytest.raises(ToolError) as exc_info:
        await _call_form_tool("update_form")

    assert str(exc_info.value) == (
        "The form config has errors: "
        "layout.0.field: The workflow has no input named 'laptop'"
    )


@pytest.mark.anyio
async def test_list_workflow_action_runs_marks_lockable_and_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    role = SimpleNamespace(workspace_id=workspace_id)
    workflow_id = mcp_server.WorkflowUUID.new_uuid4()
    events = {
        "wf_example/exec_ok": [SimpleNamespace(action_ref="a", status="COMPLETED")],
        "wf_example/exec_failed": [SimpleNamespace(action_ref="a", status="FAILED")],
        "wf_example/exec_absent": [SimpleNamespace(action_ref="b", status="COMPLETED")],
    }

    async def _resolve(_workspace_id: Any) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, role

    async def _actions(_ws: Any, _wf: Any) -> dict[str, Any]:
        return {"a": SimpleNamespace(locked_execution_id="wf_example/exec_ok")}

    class _ExecutionService:
        async def list_executions_paginated(self, **_: Any) -> SimpleNamespace:
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        id=exec_id,
                        run_id="run",
                        status=WorkflowExecutionStatus.COMPLETED,
                        start_time=datetime.now(UTC),
                        close_time=None,
                        typed_search_attributes=None,
                    )
                    for exec_id in events
                ]
            )

        async def list_workflow_execution_events_compact(self, exec_id: str) -> Any:
            return events[exec_id]

        async def get_execution(self, exec_id: str) -> Any:
            return object() if exec_id in events else None

    async def _connect(*, role: Any) -> _ExecutionService:
        return _ExecutionService()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_draft_actions_by_ref", _actions)
    monkeypatch.setattr(mcp_server.WorkflowExecutionsService, "connect", _connect)

    payload = _payload(
        await _tool(mcp_server.list_workflow_action_runs)(
            workspace_id=workspace_id, workflow_id=str(workflow_id), ref="a"
        )
    )
    assert payload["locked_execution_id"] == "wf_example/exec_ok"
    assert payload["locked_run_available"] is True
    runs = {run["execution_id"]: run for run in payload["runs"]}
    assert (
        runs["wf_example/exec_ok"]["lockable"] and runs["wf_example/exec_ok"]["locked"]
    )
    assert runs["wf_example/exec_failed"]["action_status"] == "FAILED"
    assert not runs["wf_example/exec_failed"]["lockable"]
    assert runs["wf_example/exec_absent"]["action_status"] is None
    assert not runs["wf_example/exec_absent"]["lockable"]


def _patch_action_service(monkeypatch: pytest.MonkeyPatch, updates: list[Any]) -> None:
    from tracecat.workflow.actions.service import WorkflowActionService

    action = SimpleNamespace(ref="a", locked_execution_id="wf_example/exec_old")

    class _Service:
        async def list_actions(self, _wf: Any) -> list[Any]:
            return [action]

        async def update_action(self, target: Any, params: Any) -> Any:
            updates.append(params.model_dump(exclude_unset=True))
            target.locked_execution_id = params.locked_execution_id
            return target

    monkeypatch.setattr(
        WorkflowActionService, "with_session", lambda role: _AsyncContext(_Service())
    )


@pytest.mark.anyio
async def test_lock_workflow_action_locks_and_unlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    workflow_id = mcp_server.WorkflowUUID.new_uuid4()
    updates: list[Any] = []

    async def _resolve(_workspace_id: Any) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, SimpleNamespace(workspace_id=workspace_id)

    async def _load(_ws: Any, _exec_id: str) -> tuple[Any, list[Any]]:
        return None, [SimpleNamespace(action_ref="a", status="COMPLETED")]

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_execution_with_events", _load)
    _patch_action_service(monkeypatch, updates)

    locked = _payload(
        await _tool(mcp_server.lock_workflow_action)(
            workspace_id=workspace_id,
            workflow_id=str(workflow_id),
            ref="a",
            execution_id="wf_example/exec_new",
        )
    )
    unlocked = _payload(
        await _tool(mcp_server.lock_workflow_action)(
            workspace_id=workspace_id,
            workflow_id=str(workflow_id),
            ref="a",
            execution_id=None,
        )
    )
    assert locked["locked_execution_id"] == "wf_example/exec_new"
    assert unlocked["locked_execution_id"] is None
    assert updates == [
        {"locked_execution_id": "wf_example/exec_new"},
        {"locked_execution_id": None},
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["FAILED", None])
async def test_lock_workflow_action_rejects_unusable_run(
    monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    workspace_id = uuid.uuid4()
    updates: list[Any] = []

    async def _resolve(_workspace_id: Any) -> tuple[uuid.UUID, SimpleNamespace]:
        return workspace_id, SimpleNamespace(workspace_id=workspace_id)

    async def _load(_ws: Any, _exec_id: str) -> tuple[Any, list[Any]]:
        if status is None:
            return None, []
        return None, [SimpleNamespace(action_ref="a", status=status)]

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(mcp_server, "_load_execution_with_events", _load)
    _patch_action_service(monkeypatch, updates)

    with pytest.raises(ToolError, match="did not complete"):
        await _tool(mcp_server.lock_workflow_action)(
            workspace_id=workspace_id,
            workflow_id=str(mcp_server.WorkflowUUID.new_uuid4()),
            ref="a",
            execution_id="wf_example/exec_bad",
        )
    assert updates == []


def test_auto_generate_layout_leaves_room_below_trigger():
    layout = layout_module.auto_generate_layout(
        [
            {"ref": "first", "depends_on": []},
            {"ref": "second", "depends_on": ["first"]},
        ]
    )
    first, second = layout["actions"]
    trigger_gap = first["y"] - (
        layout["trigger"]["y"] + layout_module.TRIGGER_NODE_HEIGHT
    )
    row_gap = second["y"] - (first["y"] + layout_module.ACTION_NODE_HEIGHT)

    assert trigger_gap == row_gap > 0
