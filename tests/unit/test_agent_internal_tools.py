from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from tracecat_ee.agent import activities as agent_activities
from tracecat_registry import RegistryOAuthSecret

from tracecat.agent.backends import registry
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.mcp import internal_tools
from tracecat.agent.preset.schemas import AgentPresetRead
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.tokens import InternalToolContext, MCPTokenClaims
from tracecat.agent.tools import BuildToolsResult
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.authz.controls import scope_matches
from tracecat.authz.scopes import EDITOR_SCOPES
from tracecat.db.models import AgentSession
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.workflow.management.management import WorkflowsManagementService


class _AsyncContext:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _build_claims(preset_id: uuid.UUID) -> MCPTokenClaims:
    return MCPTokenClaims(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        allowed_actions=[],
        allowed_internal_tools=["internal.builder.update_preset"],
        internal_tool_context=InternalToolContext(
            preset_id=preset_id,
            entity_type="agent_preset_builder",
        ),
    )


def _build_preset_read(preset: dict[str, object]) -> AgentPresetRead:
    data = dict(preset)
    data.setdefault("skills", [])
    return AgentPresetRead.model_validate(data)


def test_evaluate_configuration_prefers_workspace_secret_even_when_empty():
    requirements = [
        {
            "name": "slack",
            "required_keys": ["SLACK_BOT_TOKEN"],
            "optional": False,
        }
    ]
    workspace_inventory = {"slack": set()}
    org_inventory = {"slack": {"SLACK_BOT_TOKEN"}}

    configured, missing = internal_tools._evaluate_configuration(
        requirements,
        workspace_inventory,
        org_inventory,
        set(),
    )

    assert configured is False
    assert missing == ["missing key: slack.SLACK_BOT_TOKEN"]


def test_evaluate_configuration_accepts_configured_oauth_integration():
    requirements = [
        {
            "type": "oauth",
            "provider_id": "google_docs",
            "grant_type": "client_credentials",
            "optional": False,
        }
    ]

    configured, missing = internal_tools._evaluate_configuration(
        requirements,
        {},
        {},
        {
            ProviderKey(
                id="google_docs",
                grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
            )
        },
    )

    assert configured is True
    assert missing == []


@pytest.mark.anyio
@pytest.mark.parametrize("filter_creator", [False, True])
async def test_list_sessions_scopes_to_preset_and_optional_creator(
    monkeypatch, filter_creator: bool
):
    preset_id = uuid.uuid4()
    creator_id = uuid.uuid4()
    claims = _build_claims(preset_id)
    captured: dict[str, object] = {}

    async def _list_sessions(**kwargs):
        captured.update(kwargs)
        return []

    service = SimpleNamespace(list_sessions=_list_sessions)
    monkeypatch.setattr(
        "tracecat.agent.session.service.AgentSessionService.with_session",
        lambda role: _AsyncContext(service),
    )

    args = {"created_by": str(creator_id)} if filter_creator else {}
    result = await internal_tools.list_sessions(args, claims)

    assert result == {"sessions": []}
    assert captured["created_by"] == (creator_id if filter_creator else None)
    assert captured["entity_id"] == preset_id


@pytest.mark.anyio
async def test_get_session_rejects_non_preset_entity(monkeypatch):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)
    service = SimpleNamespace(
        get_session=lambda _session_id: None,
    )

    async def _get_session(_session_id):
        return SimpleNamespace(
            entity_type="agent_preset_builder",
            entity_id=preset_id,
        )

    service.get_session = _get_session
    monkeypatch.setattr(
        "tracecat.agent.session.service.AgentSessionService.with_session",
        lambda role: _AsyncContext(service),
    )

    with pytest.raises(internal_tools.InternalToolError, match="not found"):
        await internal_tools.get_session(
            {"session_id": str(uuid.uuid4())},
            claims,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "backend_state", ["enabled", "disabled", "missing", "unsupported"]
)
async def test_internal_session_read_derives_readonly_from_backend_state(
    backend_state: str,
):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)
    now = datetime.now(UTC)
    session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=claims.workspace_id,
        created_by=claims.user_id,
        title="Example chat",
        entity_type="agent_preset",
        entity_id=preset_id,
        backend_id="external",
        harness_type="unsupported" if backend_state == "unsupported" else "claude_code",
        created_at=now,
        updated_at=now,
    )
    provider = DefaultBackend()
    service = SimpleNamespace(
        get_session=AsyncMock(return_value=session),
        list_messages=AsyncMock(return_value=[]),
    )
    with (
        patch(
            "tracecat.agent.session.service.AgentSessionService.with_session",
            return_value=_AsyncContext(service),
        ),
        patch.object(
            registry,
            "get_agent_backends",
            return_value={} if backend_state == "missing" else {"external": provider},
        ),
        patch.object(provider, "is_enabled", return_value=backend_state != "disabled"),
    ):
        response = await internal_tools.get_session(
            {"session_id": str(session.id)}, claims
        )
    assert response["backend_id"] == "external"
    assert response["is_readonly"] is (backend_state != "enabled")


@pytest.mark.anyio
async def test_list_available_tools_includes_configuration_fields(monkeypatch):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)

    preset_service = SimpleNamespace(
        get_preset=lambda _preset_id: None,
    )

    async def _get_preset(_preset_id):
        return SimpleNamespace(actions=["tools.alpha"])

    preset_service.get_preset = _get_preset

    async def _search_actions(_query):
        return [
            (
                SimpleNamespace(
                    namespace="tools",
                    name="alpha",
                    description="Alpha tool",
                ),
                "origin",
            ),
            (
                SimpleNamespace(
                    namespace="tools",
                    name="beta",
                    description="Beta tool",
                ),
                "origin",
            ),
        ]

    registry_service = SimpleNamespace(search_actions_from_index=_search_actions)

    async def _config(*_args, **_kwargs):
        action_name = _args[1]
        if action_name == "tools.alpha":
            return True, []
        return False, ["missing secret: beta"]

    async def _secret_inventory(_role):
        return {}, {}

    async def _oauth_inventory(_role):
        return set()

    monkeypatch.setattr(
        "tracecat.agent.preset.service.AgentPresetService.with_session",
        lambda role: _AsyncContext(preset_service),
    )
    monkeypatch.setattr(
        "tracecat.agent.mcp.internal_tools.RegistryActionsService.with_session",
        lambda role: _AsyncContext(registry_service),
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_oauth_inventory",
        _oauth_inventory,
    )
    monkeypatch.setattr(internal_tools, "_get_action_configuration", _config)

    result = await internal_tools.list_available_tools({"query": "tool"}, claims)
    assert len(result["tools"]) == 2
    alpha = next(t for t in result["tools"] if t["action_id"] == "tools.alpha")
    beta = next(t for t in result["tools"] if t["action_id"] == "tools.beta")
    assert alpha["configured"] is True
    assert alpha["already_in_preset"] is True
    assert beta["configured"] is False
    assert beta["missing_requirements"] == ["missing secret: beta"]


@pytest.mark.anyio
async def test_update_preset_blocks_unconfigured_tool_add(monkeypatch):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)

    async def _get_preset(_preset_id):
        return SimpleNamespace(actions=["tools.alpha"])

    async def _update_preset(_preset, _params):
        return _preset

    preset_service = SimpleNamespace(
        get_preset=_get_preset,
        update_preset=_update_preset,
    )
    registry_service = SimpleNamespace()

    async def _config(*_args, **_kwargs):
        return False, ["missing key: slack.SLACK_BOT_TOKEN"]

    async def _secret_inventory(_role):
        return {}, {}

    async def _oauth_inventory(_role):
        return set()

    monkeypatch.setattr(
        "tracecat.agent.preset.service.AgentPresetService.with_session",
        lambda role: _AsyncContext(preset_service),
    )
    monkeypatch.setattr(
        "tracecat.agent.mcp.internal_tools.RegistryActionsService.with_session",
        lambda role: _AsyncContext(registry_service),
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_oauth_inventory",
        _oauth_inventory,
    )
    monkeypatch.setattr(internal_tools, "_get_action_configuration", _config)

    with pytest.raises(
        internal_tools.InternalToolError, match="Cannot add unconfigured"
    ):
        await internal_tools.update_preset(
            {"actions": ["tools.alpha", "tools.slack.post_message"]},
            claims,
        )


@pytest.mark.anyio
async def test_update_preset_allows_configured_oauth_tool_add(monkeypatch):
    preset_id = uuid.uuid4()
    claims = _build_claims(preset_id)

    existing_preset = SimpleNamespace(actions=["tools.alpha"])
    updated_preset = {
        "id": preset_id,
        "workspace_id": claims.workspace_id,
        "name": "Builder Preset",
        "slug": "builder-preset",
        "description": None,
        "current_version_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "instructions": None,
        "model_name": "gpt-5.4",
        "model_provider": "openai",
        "base_url": None,
        "output_type": None,
        "actions": ["tools.alpha", "tools.google_docs.create_document"],
        "namespaces": None,
        "tool_approvals": None,
        "mcp_integrations": None,
        "retries": 3,
        "enable_internet_access": False,
    }

    async def _get_preset(_preset_id):
        return existing_preset

    async def _update_preset(_preset, _params):
        return updated_preset

    async def _build_preset(_preset):
        return _build_preset_read(updated_preset)

    async def _get_action_from_index(_action_name):
        return SimpleNamespace(manifest=object())

    registry_service = SimpleNamespace(
        get_action_from_index=_get_action_from_index,
        aggregate_secrets_from_manifest=lambda _manifest, _action_name: [
            RegistryOAuthSecret(
                provider_id="google_docs",
                grant_type="client_credentials",
            )
        ],
    )
    preset_service = SimpleNamespace(
        get_preset=_get_preset,
        update_preset=_update_preset,
        build_preset_read=_build_preset,
    )

    async def _secret_inventory(_role):
        return {}, {}

    async def _oauth_inventory(_role):
        return {
            ProviderKey(
                id="google_docs",
                grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
            )
        }

    monkeypatch.setattr(
        "tracecat.agent.preset.service.AgentPresetService.with_session",
        lambda role: _AsyncContext(preset_service),
    )
    monkeypatch.setattr(
        "tracecat.agent.mcp.internal_tools.RegistryActionsService.with_session",
        lambda role: _AsyncContext(registry_service),
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_secret_inventory",
        _secret_inventory,
    )
    monkeypatch.setattr(
        internal_tools,
        "_load_oauth_inventory",
        _oauth_inventory,
    )

    result = await internal_tools.update_preset(
        {"actions": ["tools.alpha", "tools.google_docs.create_document"]},
        claims,
    )

    assert result["actions"] == ["tools.alpha", "tools.google_docs.create_document"]


# -----------------------------------------------------------------------------
# Workflow tools
# -----------------------------------------------------------------------------

_WF_ID = "wf_4itKqkgCZrLhgYiq5L211X"
_EXEC_ID = f"{_WF_ID}/exec_6Fu6rb1QpVvcn2kQ2y1ecZ"


def _workflow_claims() -> MCPTokenClaims:
    return MCPTokenClaims(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        allowed_actions=[],
        allowed_internal_tools=list(internal_tools.WORKFLOW_INTERNAL_TOOL_NAMES),
    )


def _scopes_without(scope: str) -> frozenset[str]:
    """Editor scopes minus `scope` and every scope that implies it."""
    return frozenset(
        granted for granted in EDITOR_SCOPES if not scope_matches(granted, scope)
    )


def _patch_caller_scopes(monkeypatch, scopes: frozenset[str]) -> None:
    """Stub the DB-backed scope lookup the workflow tools use for the caller."""

    async def _compute(_role):
        return scopes

    monkeypatch.setattr("tracecat.auth.credentials.compute_effective_scopes", _compute)


@pytest.fixture(autouse=True)
def caller_has_editor_scopes(monkeypatch):
    _patch_caller_scopes(monkeypatch, EDITOR_SCOPES)


class _FakeExecutions:
    def __init__(self, execution: object | None, outcome: object = None):
        self.execution = execution
        self.outcome = outcome
        self.cancelled: list[str] = []

    async def get_execution(self, _execution_id):
        return self.execution

    def handle(self, _execution_id):
        outcome = self.outcome

        async def _result():
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return SimpleNamespace(result=_result)

    async def cancel_workflow_execution(self, execution_id):
        self.cancelled.append(execution_id)


def _patch_workspace_workflow(monkeypatch, *, found: bool = True) -> None:
    async def _get_workflow(_wf_id):
        return object() if found else None

    monkeypatch.setattr(
        "tracecat.workflow.management.management.WorkflowsManagementService.with_session",
        lambda role: _AsyncContext(SimpleNamespace(get_workflow=_get_workflow)),
    )


def _patch_executions(monkeypatch, executions: _FakeExecutions) -> None:
    async def _connect(role):
        return executions

    monkeypatch.setattr(
        "tracecat.workflow.executions.service.WorkflowExecutionsService.connect",
        _connect,
    )


def test_workflow_tools_have_handlers_and_definitions():
    definitions = internal_tools.get_internal_tool_definitions()
    for name in internal_tools.WORKFLOW_INTERNAL_TOOL_NAMES:
        assert name in internal_tools.INTERNAL_TOOL_HANDLERS
        assert definitions[name].name == name


@pytest.mark.anyio
async def test_start_workflow_resolves_alias_and_runs_published(monkeypatch):
    claims = _workflow_claims()
    wf_uuid = WorkflowUUID.new(_WF_ID)
    calls: dict[str, object] = {}

    async def _resolve_alias(alias, *, use_committed):
        calls["alias"] = (alias, use_committed)
        return wf_uuid

    async def _run_workflow(workflow_id, **kwargs):
        calls["run"] = (workflow_id, kwargs)
        return {"wf_id": wf_uuid, "wf_exec_id": _EXEC_ID, "message": "started"}

    service = SimpleNamespace(
        resolve_workflow_alias=_resolve_alias, run_workflow=_run_workflow
    )
    monkeypatch.setattr(
        "tracecat.workflow.management.management.WorkflowsManagementService.with_session",
        lambda role: _AsyncContext(service),
    )

    result = await internal_tools.start_workflow(
        {"workflow_alias": "triage", "inputs": {"x": 1}}, claims
    )

    assert result == {
        "workflow_id": _WF_ID,
        "workflow_execution_id": _EXEC_ID,
        "message": "started",
    }
    assert calls["alias"] == ("triage", True)
    assert calls["run"] == (
        wf_uuid,
        {"inputs": {"x": 1}, "use_draft": False, "version": None},
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "args",
    [{}, {"workflow_id": _WF_ID, "workflow_alias": "triage"}],
)
async def test_start_workflow_requires_exactly_one_target(args):
    with pytest.raises(internal_tools.InternalToolError, match="exactly one"):
        await internal_tools.start_workflow(args, _workflow_claims())


@pytest.mark.anyio
async def test_start_workflow_requires_execute_scope(monkeypatch):
    """The real service method enforces `workflow:execute`, as for MCP."""
    claims = _workflow_claims()
    _patch_caller_scopes(monkeypatch, _scopes_without("workflow:execute"))
    monkeypatch.setattr(
        "tracecat.workflow.management.management.WorkflowsManagementService.with_session",
        lambda role: _AsyncContext(
            WorkflowsManagementService(session=AsyncMock(), role=role)
        ),
    )

    with pytest.raises(internal_tools.InternalToolError, match="workflow:execute"):
        await internal_tools.start_workflow({"workflow_id": _WF_ID}, claims)


@pytest.mark.anyio
async def test_get_workflow_run_returns_status_while_running(monkeypatch):
    _patch_workspace_workflow(monkeypatch)
    started = datetime(2026, 11, 20, tzinfo=UTC)
    executions = _FakeExecutions(
        SimpleNamespace(
            status=WorkflowExecutionStatus.RUNNING,
            start_time=started,
            close_time=None,
        ),
        outcome=AssertionError("result must not be read while running"),
    )
    _patch_executions(monkeypatch, executions)

    result = await internal_tools.get_workflow_run(
        {"workflow_execution_id": _EXEC_ID}, _workflow_claims()
    )

    assert result == {
        "workflow_execution_id": _EXEC_ID,
        "status": "RUNNING",
        "start_time": started,
        "close_time": None,
    }


@pytest.mark.anyio
async def test_get_workflow_run_returns_result_when_completed(monkeypatch):
    _patch_workspace_workflow(monkeypatch)
    executions = _FakeExecutions(
        SimpleNamespace(
            status=WorkflowExecutionStatus.COMPLETED,
            start_time=None,
            close_time=None,
        ),
        outcome=InlineObject(data={"verdict": "benign"}),
    )
    _patch_executions(monkeypatch, executions)

    result = await internal_tools.get_workflow_run(
        {"workflow_execution_id": _EXEC_ID}, _workflow_claims()
    )

    assert result["status"] == "COMPLETED"
    assert result["result"] == {"verdict": "benign"}


@pytest.mark.anyio
async def test_get_workflow_run_returns_error_when_failed(monkeypatch):
    _patch_workspace_workflow(monkeypatch)
    failure = WorkflowFailureError(cause=ApplicationError("boom"))
    executions = _FakeExecutions(
        SimpleNamespace(
            status=WorkflowExecutionStatus.FAILED,
            start_time=None,
            close_time=None,
        ),
        outcome=failure,
    )
    _patch_executions(monkeypatch, executions)

    result = await internal_tools.get_workflow_run(
        {"workflow_execution_id": _EXEC_ID}, _workflow_claims()
    )

    assert result["status"] == "FAILED"
    assert "boom" in result["error"]
    assert "result" not in result


@pytest.mark.anyio
@pytest.mark.parametrize(
    "handler",
    [internal_tools.get_workflow_run, internal_tools.cancel_workflow_run],
)
async def test_workflow_run_tools_hide_other_workspaces(monkeypatch, handler):
    _patch_workspace_workflow(monkeypatch, found=False)
    executions = _FakeExecutions(None)
    _patch_executions(monkeypatch, executions)

    with pytest.raises(internal_tools.InternalToolError, match="not found"):
        await handler({"workflow_execution_id": _EXEC_ID}, _workflow_claims())
    assert executions.cancelled == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "scope"),
    [
        (internal_tools.get_workflow_run, "workflow:read"),
        (internal_tools.cancel_workflow_run, "workflow:terminate"),
    ],
)
async def test_workflow_run_tools_enforce_scopes(monkeypatch, handler, scope):
    claims = _workflow_claims()
    _patch_caller_scopes(monkeypatch, _scopes_without(scope))
    _patch_workspace_workflow(monkeypatch)
    executions = _FakeExecutions(None)
    _patch_executions(monkeypatch, executions)

    with pytest.raises(internal_tools.InternalToolError, match=scope):
        await handler({"workflow_execution_id": _EXEC_ID}, claims)
    assert executions.cancelled == []


@pytest.mark.anyio
async def test_cancel_workflow_run_requests_cancellation(monkeypatch):
    _patch_workspace_workflow(monkeypatch)
    executions = _FakeExecutions(None)
    _patch_executions(monkeypatch, executions)

    result = await internal_tools.cancel_workflow_run(
        {"workflow_execution_id": _EXEC_ID}, _workflow_claims()
    )

    assert executions.cancelled == [_EXEC_ID]
    assert result["workflow_execution_id"] == _EXEC_ID


@pytest.mark.anyio
async def test_workflow_run_tools_reject_malformed_execution_id():
    with pytest.raises(internal_tools.InternalToolError, match="Invalid arguments"):
        await internal_tools.get_workflow_run(
            {"workflow_execution_id": "not-an-id"}, _workflow_claims()
        )


@pytest.mark.anyio
async def test_build_grants_only_requested_workflow_tools(monkeypatch):
    """Workflow tools are opt-in through actions and skip the registry build."""
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    build_tools = AsyncMock(
        return_value=BuildToolsResult(tools=[], collected_secrets=set())
    )
    monkeypatch.setattr(agent_activities, "build_agent_tools", build_tools)
    lock_service = MagicMock(spec=RegistryLockService)
    lock_service.resolve_lock_with_bindings = AsyncMock(
        return_value=RegistryLock(origins={}, actions={})
    )
    monkeypatch.setattr(
        RegistryLockService, "with_session", lambda: _AsyncContext(lock_service)
    )

    result = await agent_activities.AgentActivities().build_tool_definitions(
        agent_activities.BuildToolDefsArgs(
            role=role,
            tool_filters=ToolFilters(
                actions=["core.cases.get_case", "internal.workflows.start"]
            ),
        )
    )

    assert build_tools.await_args is not None
    assert build_tools.await_args.kwargs["actions"] == ["core.cases.get_case"]
    assert result.allowed_internal_tools == ["internal.workflows.start"]
    assert set(result.tool_definitions) == {"internal.workflows.start"}
    lock_service.resolve_lock_with_bindings.assert_awaited_once_with(set())


@pytest.mark.anyio
async def test_build_respects_namespaces_for_workflow_tools(monkeypatch):
    """A namespace filter narrows workflow tools like any other tool."""
    role = Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        agent_activities,
        "build_agent_tools",
        AsyncMock(return_value=BuildToolsResult(tools=[], collected_secrets=set())),
    )
    lock_service = MagicMock(spec=RegistryLockService)
    lock_service.resolve_lock_with_bindings = AsyncMock(
        return_value=RegistryLock(origins={}, actions={})
    )
    monkeypatch.setattr(
        RegistryLockService, "with_session", lambda: _AsyncContext(lock_service)
    )

    result = await agent_activities.AgentActivities().build_tool_definitions(
        agent_activities.BuildToolDefsArgs(
            role=role,
            tool_filters=ToolFilters(
                namespaces=["core"], actions=["internal.workflows.start"]
            ),
        )
    )

    assert result.allowed_internal_tools is None
    assert "internal.workflows.start" not in result.tool_definitions


@pytest.mark.anyio
async def test_session_config_drops_workflow_tools_without_caller_scope():
    """Case chat and preset sessions gate workflow tools on the caller's scopes."""
    caller = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "action:*:execute", "workflow:read"}),
    )
    service = AgentSessionService(session=AsyncMock(), role=caller)
    unscoped = AgentConfig(
        model_name="gpt-5-mini",
        model_provider="openai",
        actions=[
            "core.cases.get_case",
            "internal.workflows.get",
            "internal.workflows.cancel",
        ],
    )

    @asynccontextmanager
    async def _unscoped_config(_session):
        yield unscoped

    with patch.object(service, "_build_session_agent_config", _unscoped_config):
        async with service._build_agent_config(MagicMock()) as config:
            assert config.actions == ["core.cases.get_case", "internal.workflows.get"]


@pytest.mark.anyio
async def test_start_workflow_runs_as_the_calling_user(monkeypatch):
    """The run gets the caller's role, so the executor checks their action scopes."""
    claims = _workflow_claims()
    wf_uuid = WorkflowUUID.new(_WF_ID)
    roles: list[Role] = []

    async def _run_workflow(workflow_id, **kwargs):
        return {"wf_id": wf_uuid, "wf_exec_id": _EXEC_ID, "message": "started"}

    def _with_session(role):
        roles.append(role)
        return _AsyncContext(SimpleNamespace(run_workflow=_run_workflow))

    monkeypatch.setattr(
        "tracecat.workflow.management.management.WorkflowsManagementService.with_session",
        _with_session,
    )

    await internal_tools.start_workflow({"workflow_id": _WF_ID}, claims)

    assert roles[0].type == "user"
    assert roles[0].user_id == claims.user_id
    assert roles[0].scopes == EDITOR_SCOPES


@pytest.mark.anyio
async def test_workflow_tools_without_user_use_service_role(monkeypatch):
    claims = _workflow_claims().model_copy(update={"user_id": None})
    role = await internal_tools._caller_role(claims)
    assert role.type == "service"
    assert role.service_id == "tracecat-mcp"
