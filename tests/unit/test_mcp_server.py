from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Coroutine
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
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
    result = await _tool(mcp_server.validate_workflow_definition_yaml)(
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
    result = await _tool(mcp_server.validate_workflow_definition_yaml)(
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
    result_patch = await _tool(mcp_server.validate_workflow_definition_yaml)(
        workspace_id=str(uuid.uuid4()),
        definition_yaml="""
case_trigger:
  status: online
        """,
        update_mode="patch",
    )
    result_replace = await _tool(mcp_server.validate_workflow_definition_yaml)(
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
    monkeypatch.setattr(validation_service, "validate_dsl", _validate_dsl)

    result = await _tool(mcp_server.validate_workflow)(
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
async def test_update_workflow_layout_only_does_not_null_metadata(monkeypatch):
    """A layout-only update must not overwrite title/status with NULL."""

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

    layout_yaml = """\
layout:
  trigger:
    x: 10.0
    y: 20.0
  actions:
    - ref: step_a
      x: 100.0
      y: 200.0
"""

    result = await _tool(mcp_server.update_workflow)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(wf_id),
        definition_yaml=layout_yaml,
    )
    payload = json.loads(result)
    assert "updated successfully" in payload["message"]

    # The metadata fields must NOT have been set via setattr
    assert "title" not in setattr_calls
    assert "description" not in setattr_calls
    assert "status" not in setattr_calls


@pytest.mark.anyio
async def test_get_workflow_includes_layout_when_definition_build_fails(monkeypatch):
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
        trigger_position_x=12.0,
        trigger_position_y=24.0,
        viewport_x=3.0,
        viewport_y=6.0,
        viewport_zoom=0.5,
        actions=[SimpleNamespace(ref="step_a", position_x=100.0, position_y=200.0)],
        schedules=[],
    )

    class _WorkflowService:
        def __init__(self) -> None:
            self.session = object()

        async def get_workflow(self, _wf_id):
            return workflow

        async def build_dsl_from_workflow(self, _workflow):
            raise RuntimeError("dsl failed")

    class _CaseTriggerService:
        def __init__(self, _session, *, role):
            self.role = role

        async def get_case_trigger(self, _wf_id):
            raise mcp_server.TracecatNotFoundError("not found")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.WorkflowsManagementService,
        "with_session",
        lambda role: _AsyncContext(_WorkflowService()),
    )
    monkeypatch.setattr(mcp_server, "CaseTriggersService", _CaseTriggerService)

    result = await _tool(mcp_server.get_workflow)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(workflow_id),
    )

    payload = json.loads(result)
    assert payload["definition_yaml"] != ""
    exported = yaml.safe_load(payload["definition_yaml"])
    assert exported["definition_error"] == "dsl failed"
    assert exported["layout"]["trigger"] == {"x": 12.0, "y": 24.0}
    assert exported["layout"]["actions"] == [{"ref": "step_a", "x": 100.0, "y": 200.0}]


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

    configured, missing = mcp_server._evaluate_configuration(
        requirements,
        workspace_inventory,
        org_inventory,
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

    result = await _tool(mcp_server.get_action_context)(
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

    result = await _tool(mcp_server.list_secrets_metadata)(
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

    result = await _tool(mcp_server.create_case)(
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

    result = await _tool(mcp_server.update_schedule)(
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

    result = await _tool(mcp_server.delete_schedule)(
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

    result = await _tool(mcp_server.get_webhook)(
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
        await _tool(mcp_server.get_webhook)(
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

    result = await _tool(mcp_server.create_webhook)(
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

    result = await _tool(mcp_server.update_webhook)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        status="online",
    )
    payload = json.loads(result)
    assert "updated successfully" in payload["message"]
    assert fake_webhook.status == "online"


@pytest.mark.anyio
async def test_delete_webhook(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    deleted = []
    fake_webhook = SimpleNamespace(id=uuid.uuid4())

    async def _get_webhook(session, workspace_id, workflow_id):
        return fake_webhook

    class FakeSession:
        async def delete(self, obj):
            deleted.append(obj)

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

    result = await _tool(mcp_server.delete_webhook)(
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
    )
    payload = json.loads(result)
    assert "deleted successfully" in payload["message"]
    assert deleted == [fake_webhook]


@pytest.mark.anyio
async def test_delete_webhook_not_found(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace(workspace_id=uuid.uuid4())

    async def _get_webhook(session, workspace_id, workflow_id):
        return None

    class FakeSession:
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

    with pytest.raises(ToolError, match="Webhook not found"):
        await _tool(mcp_server.delete_webhook)(
            workspace_id=str(uuid.uuid4()),
            workflow_id=str(uuid.uuid4()),
        )


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
        await _tool(mcp_server.get_case_trigger)(
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

    result = await _tool(mcp_server.create_case_trigger)(
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

    result = await _tool(mcp_server.update_case_trigger)(
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

    result = await _tool(mcp_server.list_actions)(
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

    result = await _tool(mcp_server.list_actions)(
        workspace_id=str(uuid.uuid4()),
        namespace="tools.slack",
    )
    payload = json.loads(result)
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
    payload = json.loads(result)

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
    payload = json.loads(result)

    assert len(payload) == 2
    returned_ids = {w["id"] for w in payload}
    assert str(WS_A) in returned_ids
    assert str(WS_B) in returned_ids


@pytest.mark.anyio
async def test_create_case_routes_to_correct_workspace(monkeypatch):
    """create_case passes the resolved workspace role to CasesService."""
    role_a = SimpleNamespace(workspace_id=WS_A)
    role_b = SimpleNamespace(workspace_id=WS_B)
    captured_roles: list[SimpleNamespace] = []

    async def _resolve(workspace_id: str):
        ws = uuid.UUID(workspace_id)
        role = role_a if ws == WS_A else role_b
        return ws, role

    async def _create_case(params):
        return SimpleNamespace(
            id=uuid.uuid4(),
            short_id="CASE-0001",
            summary=params.summary,
            description=params.description,
            priority=params.priority,
            severity=params.severity,
            status=params.status,
            fields=None,
            payload=None,
            created_at="2025-01-01T00:00:00Z",
        )

    class _FakeCasesService:
        def __init__(self, role):
            captured_roles.append(role)

        async def create_case(self, params):
            return await _create_case(params)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.cases.service.CasesService.with_session",
        lambda role: _AsyncContext(_FakeCasesService(role)),
    )

    await _tool(mcp_server.create_case)(
        workspace_id=str(WS_A),
        summary="Alert in SOC",
        description="Suspicious login",
    )
    await _tool(mcp_server.create_case)(
        workspace_id=str(WS_B),
        summary="Build failure",
        description="CI pipeline broke",
    )

    assert len(captured_roles) == 2
    assert captured_roles[0].workspace_id == WS_A
    assert captured_roles[1].workspace_id == WS_B


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
async def test_list_cases_isolated_per_workspace(monkeypatch):
    """list_cases returns different results per workspace."""
    cases_by_ws: dict[uuid.UUID, list] = {
        WS_A: [
            SimpleNamespace(
                id=uuid.uuid4(),
                short_id="CASE-A1",
                summary="SOC alert",
                priority="high",
                severity="critical",
                status="new",
                created_at="2025-01-01T00:00:00Z",
                assignee=None,
                tags=[],
            )
        ],
        WS_B: [],
    }

    async def _resolve(workspace_id: str):
        ws = uuid.UUID(workspace_id)
        return ws, SimpleNamespace(workspace_id=ws)

    class _FakeCasesService:
        def __init__(self, role):
            self._ws = role.workspace_id

        async def search_cases(self, **_kwargs):
            return SimpleNamespace(items=cases_by_ws.get(self._ws, []))

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.cases.service.CasesService.with_session",
        lambda role: _AsyncContext(_FakeCasesService(role)),
    )

    result_a = json.loads(await _tool(mcp_server.list_cases)(workspace_id=str(WS_A)))
    result_b = json.loads(await _tool(mcp_server.list_cases)(workspace_id=str(WS_B)))

    assert len(result_a) == 1
    assert result_a[0]["short_id"] == "CASE-A1"
    assert len(result_b) == 0


@pytest.mark.anyio
async def test_concurrent_workspace_calls_do_not_cross(monkeypatch):
    """Concurrent tool calls for different workspaces resolve independently."""
    resolved: dict[str, uuid.UUID] = {}

    async def _resolve(workspace_id: str):
        ws = uuid.UUID(workspace_id)
        await asyncio.sleep(0)
        resolved[workspace_id] = ws
        return ws, SimpleNamespace(workspace_id=ws)

    async def _create_case(params):
        return SimpleNamespace(
            id=uuid.uuid4(),
            short_id="CASE-0001",
            summary=params.summary,
            description=params.description,
            priority=params.priority,
            severity=params.severity,
            status=params.status,
            fields=None,
            payload=None,
            created_at="2025-01-01T00:00:00Z",
        )

    class _FakeCasesService:
        def __init__(self, _role):
            pass

        async def create_case(self, params):
            return await _create_case(params)

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        "tracecat.cases.service.CasesService.with_session",
        lambda role: _AsyncContext(_FakeCasesService(role)),
    )

    results = await asyncio.gather(
        _tool(mcp_server.create_case)(
            workspace_id=str(WS_A),
            summary="Alert A",
            description="desc A",
        ),
        _tool(mcp_server.create_case)(
            workspace_id=str(WS_B),
            summary="Alert B",
            description="desc B",
        ),
    )

    assert len(results) == 2
    assert resolved[str(WS_A)] == WS_A
    assert resolved[str(WS_B)] == WS_B


@pytest.mark.anyio
async def test_import_csv(monkeypatch):
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
        mcp_server.TablesService,
        "with_session",
        lambda role: _AsyncContext(svc),
    )

    csv_text = "Name,Age\nAlice,30\nBob,25\nCharlie,35"
    result = await _tool(mcp_server.import_csv)(
        workspace_id=str(uuid.uuid4()),
        csv_content=csv_text,
        table_name="test_table",
    )
    payload = json.loads(result)
    assert payload["id"] == str(table_id)
    assert payload["name"] == "test_table"
    assert payload["rows_inserted"] == 3
    assert payload["column_mapping"] == {"Name": "name", "Age": "age"}
    assert svc._contents == csv_text.encode()
    assert svc._table_name == "test_table"


@pytest.mark.anyio
async def test_export_csv(monkeypatch):
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

    class _TablesService:
        async def get_table(self, _table_id):
            return fake_table

        async def search_rows(self, _table, *, limit=1000, cursor=None):
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
        mcp_server.TablesService,
        "with_session",
        lambda role: _AsyncContext(_TablesService()),
    )

    result = await _tool(mcp_server.export_csv)(
        workspace_id=str(uuid.uuid4()),
        table_id=str(table_id),
    )
    lines = result.strip().splitlines()
    assert lines[0] == "city,age"  # preserves table column order, system cols excluded
    assert lines[1] == "NYC,30"
    assert lines[2] == "LA,25"


@pytest.mark.anyio
async def test_import_csv_empty_raises(monkeypatch):
    async def _resolve(_workspace_id):
        return uuid.uuid4(), SimpleNamespace()

    class _TablesService:
        async def import_table_from_csv(self, *, contents, table_name, **kwargs):
            from tracecat.exceptions import TracecatImportError

            raise TracecatImportError("CSV file does not contain any columns")

    monkeypatch.setattr(mcp_server, "_resolve_workspace_role", _resolve)
    monkeypatch.setattr(
        mcp_server.TablesService,
        "with_session",
        lambda role: _AsyncContext(_TablesService()),
    )

    with pytest.raises(ToolError, match="CSV file does not contain any columns"):
        await _tool(mcp_server.import_csv)(
            workspace_id=str(uuid.uuid4()),
            csv_content="",
        )
