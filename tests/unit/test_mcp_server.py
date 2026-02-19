from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import cast

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams
from temporalio.client import WorkflowFailureError
from tracecat_registry import RegistrySecret

import tracecat.mcp.auth as mcp_auth
import tracecat.validation.service as validation_service
from tracecat.expressions.common import ExprType
from tracecat.validation.schemas import (
    ValidationDetail,
    ValidationResult,
    ValidationResultType,
)

mcp_auth.create_mcp_auth = lambda: None
from tracecat.mcp import server as mcp_server  # noqa: E402


class _AsyncContext:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.anyio
async def test_validate_workflow_definition_yaml_valid(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    result = await mcp_server.validate_workflow_definition_yaml.fn(
        workspace_id=str(uuid.uuid4()),
        definition_yaml="""
definition:
  title: Test workflow
  description: Test description
  entrypoint:
    expects: {}
  actions:
    - ref: do_it
      action: core.table.list_tables
      args: {}
        """,
    )
    payload = json.loads(result)
    assert payload["valid"] is True
    assert payload["errors"] == []


@pytest.mark.anyio
async def test_validate_workflow_definition_yaml_extended_payload(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    result = await mcp_server.validate_workflow_definition_yaml.fn(
        workspace_id=str(uuid.uuid4()),
        definition_yaml="""
definition:
  title: Test workflow
  description: Test description
  entrypoint:
    expects: {}
  actions:
    - ref: do_it
      action: core.table.list_tables
      args: {}
layout:
  trigger:
    x: 20
    y: 30
  actions:
    - ref: do_it
      x: 100
      y: 200
schedules:
  - cron: "0 * * * *"
    status: offline
case_trigger:
  status: offline
  event_types: []
  tag_filters: []
        """,
    )
    payload = json.loads(result)
    assert payload["valid"] is True
    assert payload["errors"] == []


@pytest.mark.anyio
async def test_validate_workflow_definition_yaml_case_trigger_mode(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    result_patch = await mcp_server.validate_workflow_definition_yaml.fn(
        workspace_id=str(uuid.uuid4()),
        definition_yaml="""
case_trigger:
  status: online
        """,
        update_mode="patch",
    )
    result_replace = await mcp_server.validate_workflow_definition_yaml.fn(
        workspace_id=str(uuid.uuid4()),
        definition_yaml="""
case_trigger:
  status: online
        """,
        update_mode="replace",
    )
    patch_payload = json.loads(result_patch)
    replace_payload = json.loads(result_replace)

    assert patch_payload["valid"] is True
    assert replace_payload["valid"] is False
    assert replace_payload["errors"][0]["section"] == "case_trigger"


@pytest.mark.anyio
async def test_validate_workflow_returns_expression_details(monkeypatch):
    async def _resolve_role(_email, _ws_id):
        return SimpleNamespace()

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

    monkeypatch.setattr(mcp_server, "get_email_from_token", lambda: "alice@example.com")
    monkeypatch.setattr(mcp_server, "resolve_role", _resolve_role)
    monkeypatch.setattr(
        "tracecat.workflow.management.management.WorkflowsManagementService.with_session",
        _with_session,
    )
    monkeypatch.setattr(validation_service, "validate_dsl", _validate_dsl)

    result = await mcp_server.validate_workflow.fn(
        workspace_id=str(uuid.uuid4()), workflow_id=str(uuid.uuid4())
    )
    payload = json.loads(result)
    assert payload["valid"] is False
    assert payload["errors"][0]["type"] == "expression"
    assert payload["errors"][0]["details"][0]["type"] == "action.input"
    assert payload["errors"][0]["details"][0]["msg"] == "bad expr"
    assert payload["errors"][0]["details"][0]["loc"] == ["step_a", "inputs", "field"]


def test_serialize_workflow_failure_flattens_cause():
    class _Cause(Exception):
        def __init__(self) -> None:
            self.type = "ValidationError"
            self.details = ("bad value", {"field": "hash"})
            self.stack_trace = "Traceback: ... "
            self._details = ()
            super().__init__("workflow failed due to bad input")

    serialized = mcp_server._serialize_workflow_failure(
        WorkflowFailureError(cause=_Cause())
    )
    assert serialized["type"] == "WorkflowFailureError"
    assert serialized["message"] == "Workflow execution failed"
    cause = serialized["cause"]
    assert cause["type"] == "_Cause"
    assert cause["message"] == "workflow failed due to bad input"
    assert cause["failure_type"] == "ValidationError"
    assert cause["details"] == ["bad value", {"field": "hash"}]
    assert cause["stack_trace"] == "Traceback: ... "


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

    result = await mcp_server.create_table.fn(
        workspace_id=str(uuid.uuid4()),
        name="ioc_table",
        columns_json='[{"name":"ioc","type":"TEXT","nullable":false}]',
    )
    payload = json.loads(result)
    assert payload["name"] == "ioc_table"
    assert created["params"].name == "ioc_table"
    assert created["params"].columns[0].name == "ioc"


@pytest.mark.anyio
async def test_get_action_context_includes_configuration(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    indexed_action = SimpleNamespace(manifest=SimpleNamespace())
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
        return {"slack": {"SLACK_BOT_TOKEN"}}, {}

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

    result = await mcp_server.get_action_context.fn(
        workspace_id=str(uuid.uuid4()),
        action_name="tools.slack.post_message",
    )
    payload = json.loads(result)
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
        environment="default",
        tags={"team": "soc"},
        encrypted_keys=b"encrypted",
    )
    org_secret = SimpleNamespace(
        id=uuid.uuid4(),
        name="pagerduty",
        type="custom",
        environment="default",
        tags=None,
        encrypted_keys=b"encrypted2",
    )

    async def _list_secrets():
        return [workspace_secret]

    async def _list_org_secrets():
        return [org_secret]

    secret_service = SimpleNamespace(
        list_secrets=_list_secrets,
        list_org_secrets=_list_org_secrets,
        decrypt_keys=lambda _encrypted: [
            SimpleNamespace(key="API_KEY", value="super-secret-value")
        ],
    )

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.secrets.service.SecretsService.with_session",
        lambda role: _AsyncContext(secret_service),
    )

    result = await mcp_server.list_secrets_metadata.fn(
        workspace_id=str(uuid.uuid4()),
        scope="both",
    )
    payload = json.loads(result)
    assert len(payload) == 2
    assert payload[0]["keys"] == ["API_KEY"]
    assert "super-secret-value" not in result


@pytest.mark.anyio
async def test_create_case_parses_fields_and_payload_json(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _create_case(params):
        return SimpleNamespace(
            id=uuid.uuid4(),
            short_id="CASE-0001",
            summary=params.summary,
            status=params.status,
            priority=params.priority,
            severity=params.severity,
        )

    case_service = SimpleNamespace(create_case=_create_case)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.cases.service.CasesService.with_session",
        lambda role: _AsyncContext(case_service),
    )

    result = await mcp_server.create_case.fn(
        workspace_id=str(uuid.uuid4()),
        summary="Suspicious login",
        description="Investigate user login anomaly",
        fields_json='{"asset":"host-1"}',
        payload_json='{"source":"okta"}',
    )
    payload = json.loads(result)
    assert payload["summary"] == "Suspicious login"
    assert payload["short_id"] == "CASE-0001"


# ---------------------------------------------------------------------------
# Schedule update/delete tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_schedule(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    schedule_id = uuid.uuid4()
    updated_schedule = SimpleNamespace(
        id=schedule_id,
        workspace_id=uuid.uuid4(),
        created_at="2025-01-01T00:00:00",
        updated_at="2025-01-02T00:00:00",
        workflow_id=uuid.uuid4(),
        inputs=None,
        cron="0 12 * * *",
        every=None,
        offset=None,
        start_at=None,
        end_at=None,
        timeout=None,
        status="online",
    )

    async def _update_schedule(_sched_id, _params):
        return updated_schedule

    schedule_service = SimpleNamespace(update_schedule=_update_schedule)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.schedules.service.WorkflowSchedulesService.with_session",
        lambda role: _AsyncContext(schedule_service),
    )

    result = await mcp_server.update_schedule.fn(
        workspace_id=str(uuid.uuid4()),
        schedule_id=str(schedule_id),
        cron="0 12 * * *",
        status="online",
    )
    payload = json.loads(result)
    assert payload["cron"] == "0 12 * * *"
    assert payload["status"] == "online"


@pytest.mark.anyio
async def test_delete_schedule(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    deleted_id = uuid.uuid4()
    deleted = {}

    async def _delete_schedule(sched_id):
        deleted["id"] = sched_id

    schedule_service = SimpleNamespace(delete_schedule=_delete_schedule)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.schedules.service.WorkflowSchedulesService.with_session",
        lambda role: _AsyncContext(schedule_service),
    )

    result = await mcp_server.delete_schedule.fn(
        workspace_id=str(uuid.uuid4()),
        schedule_id=str(deleted_id),
    )
    payload = json.loads(result)
    assert "deleted successfully" in payload["message"]
    assert deleted["id"] == deleted_id


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

    result = await mcp_server.get_webhook.fn(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
    )
    payload = json.loads(result)
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
        await mcp_server.get_webhook.fn(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
        )


@pytest.mark.anyio
async def test_create_webhook(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    wf_id = uuid.uuid4()
    created = {}

    class FakeWebhook:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                object.__setattr__(self, k, v)
            object.__setattr__(self, "id", uuid.uuid4())
            object.__setattr__(self, "secret", "whsec_new")
            object.__setattr__(self, "url", "https://example.com/webhook")
            object.__setattr__(self, "api_key", None)
            object.__setattr__(self, "filters", {})
            object.__setattr__(self, "entrypoint_ref", None)
            created["webhook"] = self

    class FakeSession:
        def add(self, obj):
            pass

        async def commit(self):
            pass

        async def refresh(self, obj):
            pass

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server,
        "get_async_session_context_manager",
        lambda: _AsyncContext(FakeSession()),
    )
    monkeypatch.setattr("tracecat.db.models.Webhook", FakeWebhook)

    result = await mcp_server.create_webhook.fn(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(wf_id),
        status="offline",
        methods='["POST"]',
    )
    payload = json.loads(result)
    assert payload["status"] == "offline"
    assert payload["methods"] == ["POST"]


@pytest.mark.anyio
async def test_update_webhook(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    fake_webhook = SimpleNamespace(
        id=uuid.uuid4(),
        status="offline",
        methods=["POST"],
        entrypoint_ref=None,
        allowlisted_cidrs=[],
    )

    async def _get_webhook(session, workspace_id, workflow_id):
        return fake_webhook

    class FakeSession:
        def add(self, obj):
            pass

        async def commit(self):
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

    result = await mcp_server.update_webhook.fn(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        status="online",
    )
    payload = json.loads(result)
    assert "updated successfully" in payload["message"]
    assert fake_webhook.status == "online"


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

    result = await mcp_server.get_case_trigger.fn(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
    )
    payload = json.loads(result)
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
        await mcp_server.get_case_trigger.fn(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
        )


@pytest.mark.anyio
async def test_create_case_trigger(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    ct_id = uuid.uuid4()

    async def _upsert_case_trigger(_workflow_id, _config, create_missing_tags=False):
        return SimpleNamespace(
            id=ct_id,
            workflow_id=uuid.uuid4(),
            status=_config.status,
            event_types=[
                evt.value if hasattr(evt, "value") else evt
                for evt in _config.event_types
            ],
            tag_filters=list(_config.tag_filters),
        )

    ct_service = SimpleNamespace(upsert_case_trigger=_upsert_case_trigger)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.case_triggers.service.CaseTriggersService.with_session",
        lambda role: _AsyncContext(ct_service),
    )

    result = await mcp_server.create_case_trigger.fn(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        status="offline",
        event_types='["case_created"]',
        tag_filters='["phishing"]',
    )
    payload = json.loads(result)
    assert payload["status"] == "offline"
    assert payload["event_types"] == ["case_created"]
    assert payload["tag_filters"] == ["phishing"]


@pytest.mark.anyio
async def test_update_case_trigger(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _update_case_trigger(_workflow_id, _params, create_missing_tags=False):
        return SimpleNamespace()

    ct_service = SimpleNamespace(update_case_trigger=_update_case_trigger)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.workflow.case_triggers.service.CaseTriggersService.with_session",
        lambda role: _AsyncContext(ct_service),
    )

    result = await mcp_server.update_case_trigger.fn(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        status="online",
        event_types='["case_created", "case_updated"]',
    )
    payload = json.loads(result)
    assert "updated successfully" in payload["message"]


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
async def test_get_mcp_client_id_returns_anonymous_without_context():
    from tracecat.mcp.middleware import get_mcp_client_id

    ctx = MiddlewareContext(
        message=CallToolRequestParams(name="t", arguments=None),
        fastmcp_context=None,
        method="tools/call",
    )
    assert get_mcp_client_id(ctx) == "anonymous"


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


@pytest.mark.anyio
async def test_action_catalog_resource(monkeypatch):
    """The action catalog resource returns actions grouped by namespace."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _secret_inventory(_role):
        return {"slack": {"SLACK_BOT_TOKEN"}}, {}

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

    indexed_action = SimpleNamespace(manifest=SimpleNamespace())

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
    payload = json.loads(result)
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
        return {}, {}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description

    entries = [
        (_IndexEntry("core", "http_request", "Make an HTTP request"), "platform"),
        (_IndexEntry("tools.slack", "post_message", "Post a message"), "platform"),
    ]
    indexed_action = SimpleNamespace(manifest=SimpleNamespace())

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

    result = await mcp_server.list_actions.fn(
        workspace_id=str(uuid.uuid4()),
    )
    payload = json.loads(result)
    assert len(payload) == 2
    assert payload[0]["action_name"] == "core.http_request"
    assert payload[1]["action_name"] == "tools.slack.post_message"


@pytest.mark.anyio
async def test_list_actions_browse_with_namespace(monkeypatch):
    """list_actions with namespace but no query should filter by namespace."""

    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    async def _secret_inventory(_role):
        return {}, {}

    class _IndexEntry:
        def __init__(self, namespace, name, description):
            self.namespace = namespace
            self.name = name
            self.description = description

    entries = [
        (_IndexEntry("tools.slack", "post_message", "Post a message"), "platform"),
    ]
    indexed_action = SimpleNamespace(manifest=SimpleNamespace())

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

    result = await mcp_server.list_actions.fn(
        workspace_id=str(uuid.uuid4()),
        namespace="tools.slack",
    )
    payload = json.loads(result)
    assert len(payload) == 1
    assert captured_kwargs.get("namespace") == "tools.slack"


@pytest.mark.anyio
async def test_list_workspaces_applies_org_scope(monkeypatch):
    scoped_org = uuid.uuid4()
    captured: dict[str, object] = {}

    monkeypatch.setattr(mcp_server, "get_email_from_token", lambda: "alice@example.com")
    monkeypatch.setattr(
        mcp_server,
        "get_scoped_organization_id_for_request",
        lambda *, email: scoped_org,
    )

    async def _list_user_workspaces(
        email: str,
        organization_id: uuid.UUID | None = None,
    ) -> list[dict[str, str]]:
        captured["email"] = email
        captured["organization_id"] = organization_id
        return [{"id": str(uuid.uuid4()), "name": "SOC", "role": "member"}]

    monkeypatch.setattr(mcp_server, "list_user_workspaces", _list_user_workspaces)
    result = await mcp_server.list_workspaces.fn()
    payload = json.loads(result)

    assert len(payload) == 1
    assert captured["email"] == "alice@example.com"
    assert captured["organization_id"] == scoped_org


@pytest.mark.anyio
async def test_resolve_workspace_role_rejects_scope_mismatch(monkeypatch):
    workspace_org = uuid.uuid4()
    scoped_org = uuid.uuid4()

    monkeypatch.setattr(mcp_server, "get_email_from_token", lambda: "alice@example.com")
    monkeypatch.setattr(
        mcp_server,
        "get_scoped_organization_id_for_request",
        lambda *, email: scoped_org,
    )

    async def _resolve_role(_email: str, _workspace_id: uuid.UUID):
        return SimpleNamespace(organization_id=workspace_org)

    monkeypatch.setattr(mcp_server, "resolve_role", _resolve_role)

    with pytest.raises(
        ValueError,
        match="outside the organization scope",
    ):
        await mcp_server._resolve_workspace_role(str(uuid.uuid4()))
