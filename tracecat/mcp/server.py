"""Standalone MCP server for Tracecat workflow management.

Exposes workflow operations to external MCP clients (Claude Desktop, Cursor, etc.).
Users authenticate via their existing Tracecat OIDC login.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

import yaml
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from google.protobuf.json_format import MessageToDict
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import delete, select
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat.agent.tools import create_tool_from_registry
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Action, WorkflowDefinition
from tracecat.dsl.common import (
    DSLInput,
    get_execution_type_from_search_attr,
    get_trigger_type_from_search_attr,
)
from tracecat.dsl.validation import (
    format_input_schema_validation_error,
    normalize_trigger_inputs,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.logger import logger
from tracecat.mcp.auth import (
    create_mcp_auth,
    get_email_from_token,
    list_user_organizations,
    list_user_workspaces,
    resolve_role,
)
from tracecat.mcp.config import (
    TRACECAT_MCP__RATE_LIMIT_BURST,
    TRACECAT_MCP__RATE_LIMIT_RPS,
)
from tracecat.mcp.middleware import (
    MCPInputSizeLimitMiddleware,
    MCPTimeoutMiddleware,
    get_mcp_client_id,
)
from tracecat.registry.lock.types import RegistryLock
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.validation.schemas import ValidationDetail
from tracecat.workflow.case_triggers.schemas import (
    CaseTriggerConfig,
    CaseTriggerCreate,
    CaseTriggerRead,
    CaseTriggerUpdate,
)
from tracecat.workflow.case_triggers.service import CaseTriggersService
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowUpdate
from tracecat.workflow.schedules.schemas import (
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from tracecat.workflow.schedules.service import WorkflowSchedulesService


def _parse_json_arg(raw_value: str | None, field_name: str) -> Any | None:
    """Parse a JSON-encoded argument."""
    if raw_value is None:
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Invalid JSON for {field_name}: {exc}") from exc


def _validation_result_payload(vr: Any) -> dict[str, Any]:
    """Serialize a validation result for user-facing error output."""
    payload = vr.root.model_dump(mode="json", exclude_none=True)
    if "msg" in payload and "message" not in payload:
        payload["message"] = payload["msg"]
    if payload.get("detail") is not None:
        payload["details"] = [
            (
                {
                    "type": detail.get("type", ""),
                    "msg": detail.get("msg", str(detail)),
                    "loc": list(detail.get("loc", ()) or ()),
                }
                if isinstance(detail, dict)
                else {
                    "type": getattr(detail, "type", ""),
                    "msg": getattr(detail, "msg", str(detail)),
                    "loc": list(getattr(detail, "loc", ()) or ()),
                }
            )
            for detail in payload["detail"]
        ]
    return payload


def _validation_detail_to_payload(
    details: list[ValidationDetail],
) -> list[dict[str, Any]]:
    """Serialize trigger validation details into JSON-serializable dictionaries."""
    return [
        {
            "type": detail.type,
            "msg": detail.msg,
            "loc": list(detail.loc) if detail.loc is not None else None,
        }
        for detail in details
    ]


def _validate_and_parse_trigger_inputs(
    dsl_input: DSLInput,
    inputs: str | None,
) -> Any | None:
    """Parse trigger inputs and validate using the DSL entrypoint schema."""
    parsed_inputs = _parse_json_arg(inputs, "inputs")

    expects = dsl_input.entrypoint.expects
    if not expects:
        return parsed_inputs

    try:
        normalize_trigger_inputs(
            expects,
            {} if parsed_inputs is None else parsed_inputs,
            model_name="TriggerInputsValidator",
        )
    except ValidationError as exc:
        details = ValidationDetail.list_from_pydantic(exc)
        raise ToolError(
            _json(
                {
                    "type": "validation_error",
                    "message": format_input_schema_validation_error(details),
                    "status": "error",
                    "details": _validation_detail_to_payload(details),
                    "input_schema": {
                        field_name: field.model_dump(mode="json")
                        for field_name, field in expects.items()
                    },
                }
            )
        ) from exc

    return parsed_inputs


async def _resolve_workspace_role(workspace_id: str) -> tuple[uuid.UUID, Any]:
    """Resolve workspace UUID + role from current token."""
    email = get_email_from_token()
    ws_id = uuid.UUID(workspace_id)
    role = await resolve_role(email, ws_id)
    return ws_id, role


def _build_example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a compact example payload from JSON schema properties."""
    example: dict[str, Any] = {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        prop = properties.get(key, {})
        prop_type = prop.get("type")
        if prop_type == "string":
            example[key] = "example"
        elif prop_type == "integer":
            example[key] = 1
        elif prop_type == "number":
            example[key] = 1.0
        elif prop_type == "boolean":
            example[key] = True
        elif prop_type == "array":
            example[key] = []
        elif prop_type == "object":
            example[key] = {}
        else:
            example[key] = "value"
    return example


def _secrets_to_requirements(secrets: list[Any]) -> list[dict[str, Any]]:
    """Convert registry secret objects to public requirement metadata."""
    requirements: list[dict[str, Any]] = []
    for secret in secrets:
        if isinstance(secret, RegistrySecret):
            requirements.append(
                {
                    "name": secret.name,
                    "required_keys": list(secret.keys or []),
                    "optional_keys": list(secret.optional_keys or []),
                    "optional": secret.optional,
                }
            )
        elif isinstance(secret, RegistryOAuthSecret):
            requirements.append(
                {
                    "name": secret.name,
                    "required_keys": [secret.token_name],
                    "optional_keys": [],
                    "optional": secret.optional,
                }
            )
    return requirements


async def _load_secret_inventory(
    role: Any,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Load workspace and organization secret key inventories for default environment."""
    from tracecat.secrets.service import SecretsService

    async with SecretsService.with_session(role=role) as svc:
        workspace_inventory: dict[str, set[str]] = {}
        org_inventory: dict[str, set[str]] = {}

        workspace_secrets = await svc.list_secrets()
        for secret in workspace_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            keys = {kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)}
            workspace_inventory[secret.name] = keys

        org_secrets = await svc.list_org_secrets()
        for secret in org_secrets:
            if secret.environment != DEFAULT_SECRETS_ENVIRONMENT:
                continue
            keys = {kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)}
            org_inventory[secret.name] = keys

        return workspace_inventory, org_inventory


def _evaluate_configuration(
    requirements: list[dict[str, Any]],
    workspace_inventory: dict[str, set[str]],
    org_inventory: dict[str, set[str]],
) -> tuple[bool, list[str]]:
    """Evaluate whether required secret names/keys are configured."""
    missing: list[str] = []
    for req in requirements:
        secret_name = req["name"]
        required_keys = set(req["required_keys"])
        if not required_keys and req.get("optional", False):
            continue
        available_keys = workspace_inventory.get(secret_name) or org_inventory.get(
            secret_name
        )
        if available_keys is None:
            missing.append(f"missing secret: {secret_name}")
            continue
        for key in sorted(required_keys):
            if key not in available_keys:
                missing.append(f"missing key: {secret_name}.{key}")
    return len(missing) == 0, missing


_WORKFLOW_YAML_TOP_LEVEL_KEYS = frozenset(
    {"definition", "layout", "schedules", "case_trigger"}
)


class MCPLayoutPosition(BaseModel):
    x: float | None = None
    y: float | None = None


class MCPLayoutViewport(BaseModel):
    x: float | None = None
    y: float | None = None
    zoom: float | None = None


class MCPLayoutActionPosition(BaseModel):
    ref: str
    x: float | None = None
    y: float | None = None


class MCPWorkflowLayout(BaseModel):
    trigger: MCPLayoutPosition | None = None
    viewport: MCPLayoutViewport | None = None
    actions: list[MCPLayoutActionPosition] = Field(default_factory=list)


class MCPWorkflowSchedule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["online", "offline"] = "online"
    inputs: dict[str, Any] | None = None
    cron: str | None = None
    every: timedelta | None = None
    offset: timedelta | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timeout: float = 0

    @field_validator("every", "offset", mode="before")
    @classmethod
    def parse_duration(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _parse_iso8601_duration(value)
        return value

    @model_validator(mode="after")
    def validate_schedule_spec(self) -> MCPWorkflowSchedule:
        if self.cron is None and self.every is None:
            raise ValueError("Either cron or every must be provided for a schedule")
        return self


class MCPWorkflowYamlPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    definition: DSLInput | None = None
    layout: MCPWorkflowLayout | None = None
    schedules: list[MCPWorkflowSchedule] | None = None
    case_trigger: dict[str, Any] | None = None


def _normalize_workflow_yaml_payload(raw_payload: Any) -> dict[str, Any]:
    """Normalize YAML payload shape for MCP workflow update APIs."""
    if raw_payload is None:
        raise ToolError("Workflow definition YAML is empty")
    if not isinstance(raw_payload, dict):
        raise ToolError("Workflow definition YAML must decode to an object")
    if "definition" in raw_payload:
        return raw_payload
    if _WORKFLOW_YAML_TOP_LEVEL_KEYS.intersection(raw_payload.keys()):
        return raw_payload
    return {"definition": raw_payload}


def _parse_workflow_yaml_payload(definition_yaml: str) -> MCPWorkflowYamlPayload:
    """Parse and validate workflow definition YAML payload."""
    try:
        raw = yaml.safe_load(definition_yaml)
    except yaml.YAMLError as exc:
        raise ToolError(f"Invalid YAML: {exc}") from exc
    normalized = _normalize_workflow_yaml_payload(raw)
    return MCPWorkflowYamlPayload.model_validate(normalized)


def _auto_generate_layout(
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate a top-down layout for workflow actions when none is provided.

    Walks the dependency graph to assign each action a depth (row), then
    spreads siblings horizontally. The trigger node sits at the top.
    """
    NODE_HEIGHT = 150  # vertical spacing between rows
    NODE_WIDTH = 300  # horizontal spacing between columns

    # Build dependency graph
    dependents: dict[str, list[str]] = {a["ref"]: [] for a in actions}
    deps: dict[str, list[str]] = {}
    for a in actions:
        deps[a["ref"]] = a.get("depends_on", []) or []
        for dep in deps[a["ref"]]:
            if dep in dependents:
                dependents[dep].append(a["ref"])

    # Assign depth via BFS from roots
    depth: dict[str, int] = {}
    roots = [ref for ref, d in deps.items() if not d]
    # If no roots found (cycle?), just use insertion order
    if not roots:
        for i, a in enumerate(actions):
            depth[a["ref"]] = i
    else:
        queue = list(roots)
        for r in roots:
            depth[r] = 0
        while queue:
            ref = queue.pop(0)
            for child in dependents.get(ref, []):
                new_depth = depth[ref] + 1
                if child not in depth or new_depth > depth[child]:
                    depth[child] = new_depth
                    queue.append(child)

    # Group actions by depth
    rows: dict[int, list[str]] = {}
    for ref, d in depth.items():
        rows.setdefault(d, []).append(ref)

    # Sort refs within each row for deterministic output
    for d in rows:
        rows[d].sort()

    # Position: trigger at top, then each row below
    layout: dict[str, Any] = {
        "trigger": {"x": 0, "y": 0},
        "actions": [],
    }
    for d in sorted(rows.keys()):
        refs_in_row = rows[d]
        total_width = (len(refs_in_row) - 1) * NODE_WIDTH
        start_x = -total_width / 2
        y = (d + 1) * NODE_HEIGHT  # +1 to leave room for trigger
        for i, ref in enumerate(refs_in_row):
            layout["actions"].append(
                {
                    "ref": ref,
                    "x": start_x + i * NODE_WIDTH,
                    "y": y,
                }
            )

    return layout


async def _replace_workflow_definition_from_dsl(
    service: WorkflowsManagementService,
    workflow_id: WorkflowUUID,
    dsl: DSLInput,
) -> None:
    """Replace draft workflow definition from DSL (actions + metadata)."""
    workflow = await service.get_workflow(workflow_id)
    if workflow is None:
        raise ToolError(f"Workflow {workflow_id} not found")

    workflow.title = dsl.title
    workflow.description = dsl.description
    workflow.entrypoint = dsl.entrypoint.ref
    entrypoint_data = dsl.entrypoint.model_dump()
    workflow.expects = entrypoint_data.get("expects") or {}
    workflow.returns = dsl.returns
    workflow.config = dsl.config.model_dump(mode="json")
    workflow.error_handler = dsl.error_handler
    service.session.add(workflow)

    await service.session.execute(
        delete(Action).where(
            Action.workspace_id == service.workspace_id,
            Action.workflow_id == workflow.id,
        )
    )
    await service.create_actions_from_dsl(dsl, workflow.id)
    await service.session.flush()
    await service.session.refresh(workflow, ["actions"])


def _apply_layout_to_workflow(
    *,
    workflow: Any,
    layout: MCPWorkflowLayout,
) -> None:
    """Apply optional trigger/action/viewport layout updates to a workflow."""
    if layout.trigger is not None:
        if layout.trigger.x is not None:
            workflow.trigger_position_x = layout.trigger.x
        if layout.trigger.y is not None:
            workflow.trigger_position_y = layout.trigger.y

    if layout.viewport is not None:
        if layout.viewport.x is not None:
            workflow.viewport_x = layout.viewport.x
        if layout.viewport.y is not None:
            workflow.viewport_y = layout.viewport.y
        if layout.viewport.zoom is not None:
            workflow.viewport_zoom = layout.viewport.zoom

    action_by_ref = {action.ref: action for action in workflow.actions}
    for action_position in layout.actions:
        action = action_by_ref.get(action_position.ref)
        if action is None:
            raise ToolError(
                f"Unknown action ref {action_position.ref!r} in layout.actions"
            )
        if action_position.x is not None:
            action.position_x = action_position.x
        if action_position.y is not None:
            action.position_y = action_position.y


async def _replace_workflow_schedules(
    *,
    service: WorkflowSchedulesService,
    workflow_id: WorkflowUUID,
    schedules: list[MCPWorkflowSchedule],
) -> list[uuid.UUID]:
    """Replace all schedules for a workflow from YAML payload."""
    existing = await service.list_schedules(workflow_id=workflow_id)
    for schedule in existing:
        await service.delete_schedule(schedule.id, commit=False)

    offline_schedule_ids: list[uuid.UUID] = []
    for schedule in schedules:
        created = await service.create_schedule(
            ScheduleCreate(
                workflow_id=workflow_id,
                inputs=schedule.inputs,
                cron=schedule.cron,
                every=schedule.every,
                offset=schedule.offset,
                start_at=schedule.start_at,
                end_at=schedule.end_at,
                status=schedule.status,
                timeout=schedule.timeout,
            ),
            commit=False,
        )
        if schedule.status == "offline":
            offline_schedule_ids.append(created.id)
    return offline_schedule_ids


async def _apply_case_trigger_payload(
    *,
    service: CaseTriggersService,
    workflow_id: WorkflowUUID,
    case_trigger_payload: dict[str, Any],
    update_mode: Literal["replace", "patch"],
) -> None:
    """Apply case-trigger configuration based on update mode."""
    if update_mode == "replace":
        config = CaseTriggerConfig.model_validate(case_trigger_payload)
        await service.upsert_case_trigger(
            workflow_id,
            config,
            create_missing_tags=True,
            commit=False,
        )
        return

    patch = CaseTriggerUpdate.model_validate(case_trigger_payload)
    if not patch.model_dump(exclude_unset=True):
        return
    try:
        await service.update_case_trigger(
            workflow_id,
            patch,
            create_missing_tags=True,
            commit=False,
        )
    except TracecatNotFoundError:
        config = CaseTriggerConfig(
            status=patch.status or "offline",
            event_types=patch.event_types or [],
            tag_filters=patch.tag_filters or [],
        )
        await service.upsert_case_trigger(
            workflow_id,
            config,
            create_missing_tags=True,
            commit=False,
        )


auth = create_mcp_auth()

# ---------------------------------------------------------------------------
# Server instructions — sent to every MCP client on connection
# ---------------------------------------------------------------------------

_MCP_INSTRUCTIONS = """\
Tracecat workflow management server. Use `list_workspaces` to discover available \
workspaces, then pass `workspace_id` to all other tools.

## Action namespaces
- `core.*` — built-in platform actions (core.http_request, core.transform.reshape, \
core.script.run_python, core.table.*, core.open_case, core.send_email, etc.)
- `core.transform.*` — data transforms (reshape, scatter, gather, filter, map)
- `tools.*` — third-party integrations (tools.slack.post_message, etc.)
- `ai.*` — AI/LLM actions:
  - `ai.action` — simple LLM call (no tools), supports `output_type` for structured output
  - `ai.agent` — full AI agent with tool calling via `actions` list
  - `ai.preset_agent` — run a saved agent preset by slug

Use `list_actions` to discover available actions. Use `get_action_context` or \
`get_workflow_authoring_context` to get parameter schemas for any action \
(including platform/interface actions like ai.agent, scatter, gather, etc.).

## Expression syntax (used in action `args:` values)
- `${{ TRIGGER.<field> }}` — workflow trigger input
- `${{ ACTIONS.<ref>.result }}` — output from a completed action
- `${{ SECRETS.<name>.<KEY> }}` — secret value
- `${{ VARS.<name>.<key> }}` — workspace variable
- `${{ FN.<func>(<args>) }}` — built-in function call (e.g. FN.length, FN.join, FN.now)
- Operators: `||`, `&&`, `==`, `!=`, `<`, `>`, `<=`, `>=`, `+`, `-`, `*`, `/`
- Ternary: `${{ condition -> true_value : false_value }}`
- Literals: `None` (NOT `null`), `true`, `false`, strings, numbers
- **Important**: There is NO inline `for` comprehension syntax in expressions. \
Use `core.script.run_python` for list transformations, or `for_each` on actions.
- **Important**: Use `None` (Python-style) NOT `null` (JSON-style) in expressions.

## Scatter/Gather pattern (parallel fan-out)
Within a scatter stream, each child action accesses its item via \
`ACTIONS.<scatter_ref>.result`:
```yaml
- ref: my_scatter
  action: core.transform.scatter
  args:
    collection: ${{ ACTIONS.previous_step.result }}
- ref: process_item
  action: core.http_request
  depends_on: [my_scatter]
  args:
    url: "https://api.example.com/${{ ACTIONS.my_scatter.result.id }}"
    method: GET
- ref: my_gather
  action: core.transform.gather
  depends_on: [process_item]
  args:
    items: ${{ ACTIONS.process_item.result }}
```

## Key DSL fields (inside each action under `actions:`)
- `ref` — unique slug identifier for the action
- `action` — action type (e.g. `core.http_request`)
- `args` — action arguments as key-value pairs
- `depends_on` — list of action refs this action waits for
- `run_if` — conditional expression to skip execution
- `for_each` — iterate over a list \
(syntax: `${{ for var.x in ACTIONS.step.result }}`, access item as `${{ var.x }}`)
- `retry_policy` — {max_attempts, timeout}
- `join_strategy` — `all` (default) or `any`

## Recommended authoring sequence
1. `get_workflow_authoring_context` — get action schemas, secrets, and variables
2. `create_workflow` or `update_workflow` with `definition_yaml`
3. `validate_workflow` — check for structural and expression errors
4. `publish_workflow` — freeze a versioned snapshot
5. `run_published_workflow` or `run_draft_workflow` — execute it
6. `list_workflow_executions` — see run history, find execution IDs
7. `get_workflow_execution` — inspect execution status, per-action results/errors

## Debugging workflow runs
After running a workflow, use `list_workflow_executions` to see recent runs and their \
statuses (COMPLETED, FAILED, RUNNING, etc.). Then use `get_workflow_execution` with the \
execution ID to get a detailed event timeline showing each action's status, timing, \
inputs, results, and errors. This is essential for diagnosing failed runs.

## Important: workflow actions vs MCP tools
Action names like `core.open_case` are for use *inside* workflow YAML definitions \
(in the `action:` field of a workflow action step). They are NOT MCP tool names. \
To manage cases directly, use the MCP tools `create_case`, `list_cases`, `get_case`, \
`update_case`, and `delete_case`. Similarly, use `create_workflow` (not \
`core.workflow.execute`) to create workflows via MCP.

Read the `tracecat://platform/dsl-reference` resource for the full DSL specification.
"""

mcp = FastMCP(
    "tracecat-workflows",
    auth=auth,
    instructions=_MCP_INSTRUCTIONS,
)

# ---------------------------------------------------------------------------
# Middleware pipeline (registration order = execution order)
# ---------------------------------------------------------------------------

mcp.add_middleware(
    RateLimitingMiddleware(
        max_requests_per_second=TRACECAT_MCP__RATE_LIMIT_RPS,
        burst_capacity=TRACECAT_MCP__RATE_LIMIT_BURST,
        get_client_id=get_mcp_client_id,
    )
)
mcp.add_middleware(MCPInputSizeLimitMiddleware())
mcp.add_middleware(MCPTimeoutMiddleware())
mcp.add_middleware(
    ErrorHandlingMiddleware(include_traceback=False, transform_errors=True)
)
mcp.add_middleware(
    LoggingMiddleware(methods=["tools/call"], include_payload_length=True)
)


# ---------------------------------------------------------------------------
# MCP Resources — structured reference material for LLM clients
# ---------------------------------------------------------------------------

_DSL_REFERENCE_TEXT = """\
# Tracecat Workflow DSL Reference

## Workflow YAML Structure

A workflow definition YAML has four optional top-level sections:

```yaml
definition:
  title: "My Workflow"
  description: "What this workflow does"
  entrypoint:
    expects:           # Trigger input schema (optional)
      alert_id:
        type: str
        description: "The alert ID to investigate"
      severity:
        type: str
        default: "medium"
    ref: first_action   # Which action to start with (optional, defaults to first action)
  actions:
    - ref: first_action
      action: core.http_request
      args:
        url: "https://api.example.com/alerts/${{ TRIGGER.alert_id }}"
        method: GET
    - ref: notify
      action: tools.slack.post_message
      depends_on:
        - first_action
      args:
        channel: "#alerts"
        text: "Alert ${{ TRIGGER.alert_id }}: ${{ ACTIONS.first_action.result }}"

layout:                # Optional UI positioning
  trigger:
    x: 0
    y: 0
  actions:
    - ref: first_action
      x: 0
      y: 150
    - ref: notify
      x: 0
      y: 300

schedules:             # Optional recurring schedules
  - cron: "0 9 * * 1-5"
    status: online
  - every: PT1H
    status: offline

case_trigger:          # Optional case-event trigger
  status: offline
  event_types: ["case_created", "case_updated"]
  tag_filters: ["malware", "phishing"]
```

## Action Statement Fields

Each action in the `actions` list supports:

| Field | Type | Description |
|-------|------|-------------|
| `ref` | str (required) | Unique slug identifier (lowercase, hyphens, underscores) |
| `action` | str (required) | Fully qualified action type (e.g. `core.http_request`) |
| `args` | mapping | Action arguments — use expressions for dynamic values |
| `depends_on` | list[str] | Refs of actions that must complete before this one runs |
| `run_if` | expression | Conditional — skip this action if the expression is falsy |
| `for_each` | expression or list | Iterate: run this action once per item in the list |
| `retry_policy` | object | `{max_attempts: int, timeout: float}` |
| `join_strategy` | str | `"all"` (default) or `"any"` — how to join parallel branches |
| `start_delay` | float | Seconds to wait before starting |
| `environment` | str | Override secrets environment for this action |

## Expression Syntax

Expressions are wrapped in `${{ }}` and can reference:

### Context Variables
- `TRIGGER.<field>` — workflow trigger inputs (defined in `entrypoint.expects`)
- `ACTIONS.<ref>.result` — output from a completed action
- `SECRETS.<secret_name>.<KEY>` — secret value from the workspace/org
- `VARS.<variable_name>.<key>` — workspace variable value
- `ENV.<name>` — environment variable
- `LOCAL.<key>` — current for_each iteration item (only inside `for_each` actions)

### Operators
- Logical: `||` (or), `&&` (and)
- Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`
- Arithmetic: `+`, `-`, `*`, `/`
- Ternary: `${{ condition -> true_value : false_value }}`
- Member access: `obj.field` or `obj["field"]`
- Indexing: `list[0]`, `list[-1]`

### Built-in Functions (FN.)

**String**: capitalize, concat, endswith, format, join, lowercase, prefix, replace, \
slice, slugify, split, startswith, strip, suffix, titleize, uppercase, url_encode, \
url_decode

**Comparison**: greater_than, greater_than_or_equal, is_equal, is_null, \
less_than, less_than_or_equal, is_not_equal, is_not_null, not_equal, not_null

**Regex**: regex_extract, regex_match, regex_not_match

**Array/Collection**: compact, contains, does_not_contain, contains_any_of, \
contains_none_of, difference, flatten, intersection, is_empty, is_in, length, \
not_empty, is_not_empty, not_in, is_not_in, symmetric_difference, union, unique, \
zip_map, at

**Math**: add, sub, mul, div, mod, pow, sum, min, max

**Iteration**: zip, iter_product, range

**JSON/Dict**: lookup, map_keys, merge, to_keys, to_values, tabulate, \
is_json, deserialize_json, deserialize_ndjson, deserialize_yaml, prettify_json, \
serialize_json, serialize_yaml

**Formatting**: to_markdown_list, to_markdown_table, to_markdown_tasks

**Logical**: and, or, not

**Time/Date**: datetime, days_between, days, format_datetime, from_timestamp, \
get_day_of_week, get_day, get_hour, get_minute, get_month, get_second, get_year, \
hours_between, hours, is_working_hours, minutes_between, minutes, now, \
parse_datetime, parse_time, seconds_between, seconds, set_timezone, to_datetime, \
to_isoformat, to_time, to_timestamp, today, unset_timezone, utcnow, wall_clock, \
weeks_between, weeks, windows_filetime

**Encoding**: to_base64, from_base64, to_base64url, from_base64url

**Hash**: hash_md5, hash_sha1, hash_sha256, hash_sha512

**IP Address**: ipv4_in_subnet, ipv6_in_subnet, ipv4_is_public, ipv6_is_public, \
check_ip_version

**IOC Extraction**: extract_asns, extract_cves, extract_domains, extract_emails, \
extract_md5, extract_sha1, extract_sha256, extract_sha512, extract_ip, \
extract_ipv4, extract_ipv6, extract_mac, extract_urls, normalize_email

**Generators**: uuid4

**IO**: parse_csv

## Core Built-in Actions

| Action | Description |
|--------|-------------|
| `core.http_request` | Make an HTTP request (GET, POST, PUT, DELETE, PATCH) |
| `core.transform.reshape` | Reshape data using expressions |
| `core.transform.scatter` | Fan-out: scatter a collection into parallel streams |
| `core.transform.gather` | Fan-in: gather results from parallel streams into a list |
| `core.transform.filter` | Filter a collection using a Python lambda |
| `core.transform.map` | Map over items |
| `core.script.run_python` | Run inline Python script in a sandbox |
| `core.open_case` | Open a new case |
| `core.send_email` | Send an email via SMTP |
| `core.table.insert_row` | Insert a row into a table |
| `core.table.lookup` | Lookup a value in a table |
| `core.workflow.execute` | Execute a child workflow |
| `ai.action` | Call an LLM (no tools), supports structured output via `output_type` |
| `ai.agent` | AI agent with tool calling (can invoke Tracecat actions) |
| `ai.preset_agent` | Run a saved agent preset by slug |

## Common Workflow Patterns

### HTTP Request → Notify
```yaml
actions:
  - ref: fetch_data
    action: core.http_request
    args:
      url: "https://api.example.com/data"
      method: GET
      headers:
        Authorization: "Bearer ${{ SECRETS.api_creds.API_TOKEN }}"
  - ref: notify_slack
    action: tools.slack.post_message
    depends_on: [fetch_data]
    args:
      channel: "#alerts"
      text: "Got data: ${{ ACTIONS.fetch_data.result }}"
```

### Scatter/Gather (Parallel Fan-out/Fan-in)
```yaml
actions:
  - ref: get_items
    action: core.transform.reshape
    args:
      value: ${{ TRIGGER.items }}
  - ref: scatter_items
    action: core.transform.scatter
    depends_on: [get_items]
    args:
      collection: ${{ ACTIONS.get_items.result }}
  - ref: process_item
    action: core.http_request
    depends_on: [scatter_items]
    args:
      url: "https://api.example.com/process/${{ ACTIONS.scatter_items.result.id }}"
      method: POST
  - ref: gather_results
    action: core.transform.gather
    depends_on: [process_item]
    args:
      items: ${{ ACTIONS.process_item.result }}
```
**Key**: Inside a scatter stream, `ACTIONS.<scatter_ref>.result` gives each item. \
Multiple actions can chain within the stream before the gather collects results.

### AI Action (Simple LLM Call)
```yaml
actions:
  - ref: analyze
    action: ai.action
    args:
      user_prompt: "Analyze this data: ${{ ACTIONS.fetch.result }}"
      model_name: claude-sonnet-4-20250514
      model_provider: anthropic
      instructions: "You are a security analyst. Be concise."
      output_type: 'list[{"finding": "str", "severity": "str"}]'
```

### AI Agent (With Tool Calling)
```yaml
actions:
  - ref: investigate
    action: ai.agent
    args:
      user_prompt: "Investigate this alert and create a case if needed."
      model_name: claude-sonnet-4-20250514
      model_provider: anthropic
      actions:
        - core.open_case
        - tools.slack.post_message
      instructions: "You are a SOC analyst. Be thorough."
      max_tool_calls: 10
```

### For-each Loop
```yaml
actions:
  - ref: process_items
    action: core.http_request
    for_each: "${{ for var.x in TRIGGER.items }}"
    args:
      url: "https://api.example.com/process/${{ var.x }}"
      method: POST
```

### Conditional Execution
```yaml
actions:
  - ref: escalate
    action: tools.slack.post_message
    run_if: "${{ TRIGGER.severity == 'critical' }}"
    args:
      channel: "#critical-alerts"
      text: "CRITICAL: ${{ TRIGGER.alert_title }}"
```

### Python Script for Complex Logic
```yaml
actions:
  - ref: transform
    action: core.script.run_python
    args:
      inputs:
        raw_data: "${{ ACTIONS.fetch.result }}"
      script: |
        def main(raw_data):
            # Process and return transformed data
            return [item for item in raw_data if item["status"] == "active"]
```

### HTTP Request with JSON Payload
```yaml
actions:
  - ref: create_ticket
    action: core.http_request
    args:
      url: "https://api.example.com/tickets"
      method: POST
      headers:
        Authorization: "Bearer ${{ SECRETS.api.TOKEN }}"
        Content-Type: "application/json"
      payload:
        title: "${{ TRIGGER.title }}"
        priority: "${{ TRIGGER.severity }}"
```
"""


@mcp.resource(
    "tracecat://platform/dsl-reference",
    name="DSL Reference",
    description="Complete Tracecat workflow DSL specification including YAML structure, expression syntax, built-in functions, and common patterns.",
    mime_type="text/plain",
)
def get_dsl_reference() -> str:
    """Return the full Tracecat DSL reference documentation."""
    return _DSL_REFERENCE_TEXT


async def _build_action_catalog(workspace_id: str) -> str:
    """Build the action catalog JSON for a workspace."""
    from tracecat.registry.actions.service import RegistryActionsService

    _, role = await _resolve_workspace_role(workspace_id)
    workspace_inventory, org_inventory = await _load_secret_inventory(role)

    async with RegistryActionsService.with_session(role=role) as svc:
        entries = await svc.list_actions_from_index()

        # Group by top-level namespace (e.g. "core", "tools.slack", "ai")
        namespaces: dict[str, dict[str, Any]] = {}
        for entry, _ in entries:
            action_name = f"{entry.namespace}.{entry.name}"
            # Use second-level namespace for tools.* (e.g. "tools.slack"),
            # first-level for everything else (e.g. "core")
            parts = entry.namespace.split(".")
            if parts[0] == "tools" and len(parts) >= 2:
                ns_key = f"{parts[0]}.{parts[1]}"
            else:
                ns_key = parts[0]

            if ns_key not in namespaces:
                namespaces[ns_key] = {"actions": [], "action_count": 0}

            namespaces[ns_key]["actions"].append(
                {"name": action_name, "description": entry.description or ""}
            )
            namespaces[ns_key]["action_count"] += 1

        # Evaluate secret configuration per namespace
        for _ns_key, ns_data in namespaces.items():
            ns_missing: list[str] = []
            ns_configured = True
            for action_info in ns_data["actions"]:
                indexed = await svc.get_action_from_index(action_info["name"])
                if indexed is None:
                    continue
                secrets = svc.aggregate_secrets_from_manifest(
                    indexed.manifest, action_info["name"]
                )
                if secrets:
                    requirements = _secrets_to_requirements(secrets)
                    configured, missing = _evaluate_configuration(
                        requirements, workspace_inventory, org_inventory
                    )
                    if not configured:
                        ns_configured = False
                        ns_missing.extend(missing)
            ns_data["configured"] = ns_configured
            if ns_missing:
                ns_data["missing_secrets"] = sorted(set(ns_missing))

    total_actions = sum(ns["action_count"] for ns in namespaces.values())
    return _json(
        {
            "workspace_id": workspace_id,
            "total_actions": total_actions,
            "namespaces": namespaces,
        }
    )


@mcp.resource(
    "tracecat://workspaces/{workspace_id}/action-catalog",
    name="Action Catalog",
    description="Complete browsable inventory of all available actions in a workspace, grouped by namespace with descriptions and secret configuration status.",
    mime_type="application/json",
)
async def get_action_catalog(workspace_id: str) -> str:
    """Return all available actions grouped by namespace with configuration status."""
    return await _build_action_catalog(workspace_id)


def _json(obj: Any) -> str:
    """Serialize to JSON string."""
    return json.dumps(obj, default=str)


def _format_temporal_status(status: Any) -> str | None:
    """Return a stable workflow status string for MCP responses."""
    if status is None:
        return None
    if isinstance(status, WorkflowExecutionStatus):
        return status.name
    if isinstance(status, int):
        try:
            return WorkflowExecutionStatus(status).name
        except ValueError:
            return str(status)
    if hasattr(status, "name"):
        return str(status.name)
    return str(status)


def _normalize_exception_value(value: Any) -> Any:
    """Normalize non-JSON-safe values from temporal exceptions."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple, set)):
        return [_normalize_exception_value(v) for v in value]
    if isinstance(value, dict):
        return {
            str(k): _normalize_exception_value(v)
            for k, v in value.items()
            if k is not None
        }
    if isinstance(value, BaseException):
        return {
            "type": value.__class__.__name__,
            "message": str(value),
        }
    return (
        str(value)
        if not isinstance(value, (str, int, float, bool, type(None)))
        else value
    )


def _serialize_temporal_exception(error: BaseException) -> dict[str, Any]:
    """Serialize a temporal exception recursively for MCP output."""
    payload: dict[str, Any] = {
        "type": error.__class__.__name__,
        "message": str(error),
    }

    error_type = getattr(error, "type", None)
    error_details = getattr(error, "details", None)
    error_stack_trace = getattr(error, "stack_trace", None)
    error_failure = getattr(error, "failure", None)

    if error_type is not None:
        payload["failure_type"] = str(error_type)
    if error_details is not None:
        payload["details"] = _normalize_exception_value(error_details)
    if error_stack_trace is not None:
        payload["stack_trace"] = _normalize_exception_value(error_stack_trace)
    if error_failure is not None:
        try:
            payload["failure"] = MessageToDict(error_failure)
        except Exception:
            payload["failure"] = _normalize_exception_value(error_failure)

    extra_attrs = {
        "non_retryable",
        "next_retry_delay",
        "category",
        "scheduled_event_id",
        "started_event_id",
        "identity",
        "activity_type",
        "activity_id",
        "retry_state",
        "workflow_id",
        "workflow_run_id",
        "first_execution_run_id",
    }
    for attr in extra_attrs:
        value = getattr(error, attr, None)
        if value is not None:
            payload[attr] = _normalize_exception_value(value)

    # Include nested error causality for richer diagnostics.
    nested_cause = getattr(error, "__cause__", None)
    if nested_cause is not None and isinstance(nested_cause, BaseException):
        payload["cause"] = _serialize_temporal_exception(nested_cause)

    return payload


def _serialize_workflow_failure(
    error: WorkflowFailureError | Exception,
) -> dict[str, Any]:
    """Serialize workflow failure details for user-facing MCP output."""
    cause = getattr(error, "cause", None)
    if cause is None:
        return _serialize_temporal_exception(error)
    payload = {
        "type": error.__class__.__name__,
        "message": str(error),
        "cause": _serialize_temporal_exception(cause),
    }
    return payload


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workspaces() -> str:
    """List all workspaces accessible to the authenticated user.

    Returns a JSON array of workspace objects with id, name, and role.
    """
    try:
        email = get_email_from_token()
        workspaces = await list_user_workspaces(email)
        return _json(workspaces)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workspaces", error=str(e))
        raise ToolError(f"Failed to list workspaces: {e}") from None


@mcp.tool()
async def list_organizations() -> str:
    """List all organizations the authenticated user belongs to.

    Returns a JSON array of organization objects with id, name, and role.
    """
    try:
        email = get_email_from_token()
        orgs = await list_user_organizations(email)
        return _json(orgs)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list organizations", error=str(e))
        raise ToolError(f"Failed to list organizations: {e}") from None


# ---------------------------------------------------------------------------
# Workflow CRUD tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_workflow(
    workspace_id: str,
    title: str,
    description: str = "",
    definition_yaml: str | None = None,
) -> str:
    """Create a new workflow in a workspace.

    If definition_yaml is provided, creates a fully-defined workflow from YAML.
    Otherwise creates a blank workflow with just a title and description.

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        title: Workflow title (3-100 characters).
        description: Optional workflow description (up to 1000 characters).
        definition_yaml: Optional YAML string defining the full workflow (actions,
            triggers, entrypoint). When provided, title/description in the YAML
            take precedence. The YAML must follow the ExternalWorkflowDefinition
            format with a top-level 'definition' key containing title, description,
            entrypoint, actions, and optionally triggers.

    Returns JSON with the new workflow's id, title, description, and status.
    """
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.schemas import WorkflowCreate

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)

        if definition_yaml:
            # Parse YAML and create workflow from external definition
            try:
                external_defn_data = yaml.safe_load(definition_yaml)
            except yaml.YAMLError as e:
                raise ToolError(f"Invalid YAML: {e}") from e

            # If YAML has no top-level 'definition' key, wrap it
            if "definition" not in external_defn_data:
                external_defn_data = {"definition": external_defn_data}

            # Apply title/description overrides if not in the YAML
            defn = external_defn_data.get("definition", {})
            if "title" not in defn:
                defn["title"] = title
            if "description" not in defn and description:
                defn["description"] = description

            # Auto-generate layout if not provided
            if "layout" not in external_defn_data:
                actions = defn.get("actions", [])
                if actions:
                    external_defn_data["layout"] = _auto_generate_layout(actions)

            async with WorkflowsManagementService.with_session(role=role) as svc:
                workflow = await svc.create_workflow_from_external_definition(
                    external_defn_data
                )
                return _json(
                    {
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "description": workflow.description,
                        "status": workflow.status,
                    }
                )
        else:
            async with WorkflowsManagementService.with_session(role=role) as svc:
                workflow = await svc.create_workflow(
                    WorkflowCreate(title=title, description=description or None)
                )
                return _json(
                    {
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "description": workflow.description,
                        "status": workflow.status,
                    }
                )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create workflow", error=str(e))
        raise ToolError(f"Failed to create workflow: {e}") from None


@mcp.tool()
async def get_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Get details of a specific workflow including its full YAML definition.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID (short or full format).

    Returns JSON with workflow metadata (id, title, description, status, version)
    and a 'definition_yaml' field containing the full workflow definition in YAML
    format (definition, layout, schedules, and case_trigger). The YAML can be
    modified and used with update_workflow's definition_yaml parameter.
    """
    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Build the DSL from current workflow state
            definition_yaml = ""
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
                payload: dict[str, Any] = {
                    "definition": dsl.model_dump(mode="json", exclude_none=True),
                    "layout": {
                        "trigger": {
                            "x": workflow.trigger_position_x,
                            "y": workflow.trigger_position_y,
                        },
                        "viewport": {
                            "x": workflow.viewport_x,
                            "y": workflow.viewport_y,
                            "zoom": workflow.viewport_zoom,
                        },
                        "actions": [
                            {
                                "ref": action.ref,
                                "x": action.position_x,
                                "y": action.position_y,
                            }
                            for action in sorted(
                                workflow.actions, key=lambda action: action.ref
                            )
                        ],
                    },
                    "schedules": [
                        schedule.model_dump(
                            mode="json",
                            exclude={
                                "id",
                                "workspace_id",
                                "workflow_id",
                                "created_at",
                                "updated_at",
                            },
                        )
                        for schedule in ScheduleRead.list_adapter().validate_python(
                            workflow.schedules
                        )
                    ],
                }
                try:
                    case_trigger = await CaseTriggersService(
                        svc.session, role=role
                    ).get_case_trigger(wf_id)
                    payload["case_trigger"] = {
                        "status": case_trigger.status,
                        "event_types": case_trigger.event_types,
                        "tag_filters": case_trigger.tag_filters,
                    }
                except TracecatNotFoundError:
                    payload["case_trigger"] = None
                definition_yaml = yaml.dump(
                    payload,
                    indent=2,
                    sort_keys=False,
                )
            except (ValidationError, Exception) as e:
                logger.warning(
                    "Could not build DSL for workflow",
                    workflow_id=workflow_id,
                    error=str(e),
                )

            return _json(
                {
                    "id": str(workflow.id),
                    "title": workflow.title,
                    "description": workflow.description,
                    "status": workflow.status,
                    "version": workflow.version,
                    "alias": workflow.alias,
                    "entrypoint": workflow.entrypoint,
                    "definition_yaml": definition_yaml,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get workflow", error=str(e))
        raise ToolError(f"Failed to get workflow: {e}") from None


@mcp.tool()
async def update_workflow(
    workspace_id: str,
    workflow_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    alias: str | None = None,
    error_handler: str | None = None,
    definition_yaml: str | None = None,
    update_mode: Literal["replace", "patch"] = "patch",
) -> str:
    """Update a workflow's properties.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        title: New title (3-100 characters, optional).
        description: New description (optional).
        status: New status - "online" or "offline" (optional).
        alias: New alias for the workflow (optional).
        error_handler: Error handler workflow alias (optional).
        definition_yaml: Optional workflow YAML payload. Supports:
            - definition (DSL)
            - layout (trigger/action/viewport positions)
            - schedules
            - case_trigger
        update_mode: "patch" to apply provided sections only, or "replace" to
            replace provided state sections with YAML values.

    Returns a confirmation message.
    """
    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        update_params = WorkflowUpdate(
            title=title,
            description=description,
            status=status,  # pyright: ignore[reportArgumentType]
            alias=alias,
            error_handler=error_handler,
        )

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if workflow is None:
                raise ToolError(f"Workflow {workflow_id} not found")

            yaml_payload = (
                _parse_workflow_yaml_payload(definition_yaml)
                if definition_yaml is not None
                else None
            )

            # Auto-generate layout when definition is provided but layout is not
            if (
                yaml_payload is not None
                and yaml_payload.definition is not None
                and yaml_payload.layout is None
            ):
                raw = yaml.safe_load(definition_yaml) if definition_yaml else {}
                defn_raw = raw.get("definition", raw) if isinstance(raw, dict) else {}
                actions_raw = defn_raw.get("actions", [])
                if actions_raw:
                    auto_layout = _auto_generate_layout(actions_raw)
                    yaml_payload.layout = MCPWorkflowLayout.model_validate(auto_layout)

            if yaml_payload is not None and yaml_payload.definition is not None:
                await _replace_workflow_definition_from_dsl(
                    service=svc,
                    workflow_id=wf_id,
                    dsl=yaml_payload.definition,
                )
                await svc.session.refresh(workflow, ["actions"])

            if yaml_payload is not None and yaml_payload.layout is not None:
                await svc.session.refresh(workflow, ["actions"])
                _apply_layout_to_workflow(workflow=workflow, layout=yaml_payload.layout)

            offline_schedule_ids: list[uuid.UUID] = []
            if yaml_payload is not None and yaml_payload.schedules is not None:
                schedule_service = WorkflowSchedulesService(svc.session, role=role)
                offline_schedule_ids = await _replace_workflow_schedules(
                    service=schedule_service,
                    workflow_id=wf_id,
                    schedules=yaml_payload.schedules,
                )

            if yaml_payload is not None and yaml_payload.case_trigger is not None:
                case_trigger_service = CaseTriggersService(svc.session, role=role)
                await _apply_case_trigger_payload(
                    service=case_trigger_service,
                    workflow_id=wf_id,
                    case_trigger_payload=yaml_payload.case_trigger,
                    update_mode=update_mode,
                )

            for key, value in update_params.model_dump(exclude_unset=True).items():
                setattr(workflow, key, value)
            svc.session.add(workflow)
            await svc.session.commit()
            await svc.session.refresh(workflow)

            if offline_schedule_ids:
                schedule_service = WorkflowSchedulesService(svc.session, role=role)
                for schedule_id in offline_schedule_ids:
                    await schedule_service.update_schedule(
                        schedule_id,
                        ScheduleUpdate(status="offline"),
                    )

            return _json(
                {
                    "message": f"Workflow {workflow_id} updated successfully",
                    "mode": update_mode,
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update workflow", error=str(e))
        raise ToolError(f"Failed to update workflow: {e}") from None


@mcp.tool()
async def list_workflows(
    workspace_id: str,
    status: str | None = None,
    limit: int = 50,
    search: str | None = None,
) -> str:
    """List workflows in a workspace."""
    from tracecat.workflow.management.management import WorkflowsManagementService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = max(1, min(limit, 200))
        async with WorkflowsManagementService.with_session(role=role) as svc:
            results = await svc.list_workflows()
            workflows: list[dict[str, Any]] = []
            for workflow, latest_defn in results:
                if status and workflow.status != status:
                    continue
                if search:
                    needle = search.lower()
                    if (
                        needle not in workflow.title.lower()
                        and needle not in (workflow.description or "").lower()
                    ):
                        continue
                workflows.append(
                    {
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "description": workflow.description,
                        "status": workflow.status,
                        "version": workflow.version,
                        "alias": workflow.alias,
                        "latest_definition_version": (
                            latest_defn.version if latest_defn else None
                        ),
                    }
                )
                if len(workflows) >= limit:
                    break
            return _json(workflows)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workflows", error=str(e))
        raise ToolError(f"Failed to list workflows: {e}") from None


@mcp.tool()
async def delete_workflow(workspace_id: str, workflow_id: str) -> str:
    """Delete a workflow in a workspace."""
    from tracecat.workflow.management.management import WorkflowsManagementService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        async with WorkflowsManagementService.with_session(role=role) as svc:
            await svc.delete_workflow(wf_id)
        return _json({"message": f"Workflow {workflow_id} deleted successfully"})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete workflow", error=str(e))
        raise ToolError(f"Failed to delete workflow: {e}") from None


@mcp.tool()
async def validate_workflow_definition_yaml(
    workspace_id: str,
    definition_yaml: str,
    update_mode: Literal["replace", "patch"] = "patch",
) -> str:
    """Validate workflow update YAML without persisting it."""

    try:
        _, _ = await _resolve_workspace_role(workspace_id)
        try:
            payload = _parse_workflow_yaml_payload(definition_yaml)
        except ToolError as e:
            return _json(
                {"valid": False, "errors": [{"type": "yaml", "message": str(e)}]}
            )
        except ValidationError as e:
            return _json(
                {
                    "valid": False,
                    "errors": [
                        {
                            "type": "schema",
                            "section": str(err["loc"][0])
                            if err["loc"]
                            else "definition",
                            "message": err["msg"],
                        }
                        for err in e.errors()
                    ],
                }
            )

        errors: list[dict[str, Any]] = []
        if payload.case_trigger is not None:
            try:
                if update_mode == "replace":
                    CaseTriggerConfig.model_validate(payload.case_trigger)
                else:
                    CaseTriggerUpdate.model_validate(payload.case_trigger)
            except ValidationError as e:
                errors.extend(
                    {
                        "type": "schema",
                        "section": "case_trigger",
                        "message": err["msg"],
                    }
                    for err in e.errors()
                )

        if errors:
            return _json({"valid": False, "errors": errors})
        return _json({"valid": True, "errors": []})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to validate workflow definition", error=str(e))
        raise ToolError(f"Failed to validate workflow definition: {e}") from None


@mcp.tool()
async def list_actions(
    workspace_id: str,
    query: str | None = None,
    namespace: str | None = None,
    limit: int = 50,
) -> str:
    """Search or browse available actions and return compact context metadata.

    Supports three usage modes:
    - **Search**: provide `query` to search by name/description across all namespaces.
      Example: list_actions(workspace_id, query="send message")
    - **Browse namespace**: provide `namespace` without `query` to list all actions
      in a namespace. Example: list_actions(workspace_id, namespace="tools.slack")
    - **Browse all**: omit both to list all available actions.

    Common namespaces: `core`, `tools.slack`, `tools.crowdstrike`, `tools.okta`, `ai`.

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        query: Optional search string to match against action names and descriptions.
        namespace: Optional namespace prefix filter (e.g. "tools.slack").
        limit: Maximum number of results (1-200, default 50).

    Returns JSON array of objects with fields:
    - action_name: Fully qualified name (e.g. "tools.slack.post_message")
    - description: One-line description of the action
    - configured: Whether required secrets are present in the workspace
    - missing_requirements: List of missing secret names/keys (if any)
    """
    from tracecat.registry.actions.service import RegistryActionsService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = max(1, min(limit, 200))
        workspace_inventory, org_inventory = await _load_secret_inventory(role)
        async with RegistryActionsService.with_session(role=role) as svc:
            if query:
                entries = await svc.search_actions_from_index(query, limit=limit)
            else:
                entries = await svc.list_actions_from_index(namespace=namespace)
            items: list[dict[str, Any]] = []
            for entry, _ in entries:
                action_name = f"{entry.namespace}.{entry.name}"
                if query and namespace and not entry.namespace.startswith(namespace):
                    continue
                indexed = await svc.get_action_from_index(action_name)
                if indexed is None:
                    continue
                secrets = svc.aggregate_secrets_from_manifest(
                    indexed.manifest, action_name
                )
                requirements = _secrets_to_requirements(secrets)
                configured, missing = _evaluate_configuration(
                    requirements, workspace_inventory, org_inventory
                )
                items.append(
                    {
                        "action_name": action_name,
                        "description": entry.description,
                        "configured": configured,
                        "missing_requirements": missing,
                    }
                )
            return _json(items[:limit])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list actions", error=str(e))
        raise ToolError(f"Failed to list actions: {e}") from None


@mcp.tool()
async def get_action_context(workspace_id: str, action_name: str) -> str:
    """Get full schema and configuration context for a single action.

    Use this after discovering an action via `list_actions` to get the complete
    parameter schema needed to write the `args:` block in a workflow definition.

    Example action names: "core.http_request", "tools.slack.post_message",
    "core.script.run_python", "core.transform.reshape".

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        action_name: Fully qualified action name (e.g. "tools.slack.post_message").

    Returns JSON with fields:
    - action_name: The action name
    - description: What the action does
    - parameters_json_schema: JSON Schema for the action's args (map these to the
      YAML `args:` block in a workflow action definition)
    - required_secrets: List of secrets the action needs ({name, required_keys, optional_keys})
    - configured: Whether all required secrets are present
    - missing_requirements: List of missing secret names/keys
    - examples: Example args payload based on the schema
    """
    from tracecat.registry.actions.service import RegistryActionsService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        workspace_inventory, org_inventory = await _load_secret_inventory(role)
        async with RegistryActionsService.with_session(role=role) as svc:
            indexed = await svc.get_action_from_index(action_name)
            if indexed is None:
                raise ToolError(f"Action {action_name} not found")
            tool = await create_tool_from_registry(action_name, indexed)
            secrets = svc.aggregate_secrets_from_manifest(indexed.manifest, action_name)
            requirements = _secrets_to_requirements(secrets)
            configured, missing = _evaluate_configuration(
                requirements, workspace_inventory, org_inventory
            )
            schema = tool.parameters_json_schema
            return _json(
                {
                    "action_name": action_name,
                    "description": tool.description,
                    "parameters_json_schema": schema,
                    "required_secrets": requirements,
                    "configured": configured,
                    "missing_requirements": missing,
                    "examples": [_build_example_from_schema(schema)],
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get action context", error=str(e))
        raise ToolError(f"Failed to get action context: {e}") from None


@mcp.tool()
async def get_workflow_authoring_context(
    workspace_id: str,
    action_names_json: str | None = None,
    query: str | None = None,
) -> str:
    """Get compact workflow authoring context for selected actions.

    Returns everything needed to write a workflow definition: action schemas,
    available secrets, and workspace variables. Use this before calling
    `create_workflow` or `update_workflow`.

    Two input modes (provide one or neither):
    - **By name**: pass `action_names_json` as a JSON array of action names,
      e.g. '["core.http_request", "tools.slack.post_message"]'
    - **By search**: pass `query` to search for actions by name/description

    Args:
        workspace_id: The workspace ID (from list_workspaces).
        action_names_json: JSON array of fully qualified action names.
        query: Search string to find actions by name or description.

    Returns JSON with sections:
    - actions: Array of action contexts (schema, secrets, examples for each action)
    - variable_hints: Available workspace variables ({name, keys, environment})
    - secret_hints: Available secrets ({name, keys, environment, scope})
    - notes: Additional context about the response
    """
    from tracecat.registry.actions.service import RegistryActionsService
    from tracecat.variables.service import VariablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        action_names_raw = _parse_json_arg(action_names_json, "action_names_json")
        action_names: list[str] = []
        if action_names_raw is not None:
            if not isinstance(action_names_raw, list) or not all(
                isinstance(item, str) for item in action_names_raw
            ):
                raise ToolError("action_names_json must be a JSON array of strings")
            action_names = action_names_raw

        workspace_inventory, org_inventory = await _load_secret_inventory(role)
        action_contexts: list[dict[str, Any]] = []
        async with RegistryActionsService.with_session(role=role) as registry_svc:
            if not action_names and query:
                entries = await registry_svc.search_actions_from_index(query, limit=20)
                action_names = [
                    f"{entry.namespace}.{entry.name}" for entry, _ in entries
                ]
            for action_name in action_names:
                indexed = await registry_svc.get_action_from_index(action_name)
                if indexed is None:
                    continue
                tool = await create_tool_from_registry(action_name, indexed)
                requirements = _secrets_to_requirements(
                    registry_svc.aggregate_secrets_from_manifest(
                        indexed.manifest, action_name
                    )
                )
                configured, missing = _evaluate_configuration(
                    requirements, workspace_inventory, org_inventory
                )
                action_contexts.append(
                    {
                        "action_name": action_name,
                        "description": tool.description,
                        "parameters_json_schema": tool.parameters_json_schema,
                        "required_secrets": requirements,
                        "configured": configured,
                        "missing_requirements": missing,
                        "examples": [
                            _build_example_from_schema(tool.parameters_json_schema)
                        ],
                    }
                )

        async with VariablesService.with_session(role=role) as var_svc:
            variables = await var_svc.list_variables(
                environment=DEFAULT_SECRETS_ENVIRONMENT
            )
            variable_hints = [
                {
                    "name": var.name,
                    "keys": sorted(var.values.keys()),
                    "environment": var.environment,
                }
                for var in variables
            ]

        secret_hints: list[dict[str, Any]] = []
        for secret_name, keys in workspace_inventory.items():
            secret_hints.append(
                {
                    "name": secret_name,
                    "keys": sorted(keys),
                    "environment": DEFAULT_SECRETS_ENVIRONMENT,
                    "scope": "workspace",
                }
            )
        for secret_name, keys in org_inventory.items():
            secret_hints.append(
                {
                    "name": secret_name,
                    "keys": sorted(keys),
                    "environment": DEFAULT_SECRETS_ENVIRONMENT,
                    "scope": "organization",
                }
            )

        return _json(
            {
                "actions": action_contexts,
                "variable_hints": variable_hints,
                "secret_hints": secret_hints,
                "notes": [
                    "configured means required secret names and required key names exist in the default environment",
                ],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to build workflow authoring context", error=str(e))
        raise ToolError(f"Failed to build workflow authoring context: {e}") from None


# ---------------------------------------------------------------------------
# Validation and publishing tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def validate_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Validate a workflow's draft state.

    Checks that the workflow DSL is structurally sound and that arguments are valid.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with valid (bool) and any errors.
    """
    from tracecat.exceptions import TracecatValidationError
    from tracecat.validation.service import validate_dsl
    from tracecat.workflow.management.management import WorkflowsManagementService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Tier 1: Build DSL
            errors: list[dict[str, Any]] = []
            dsl: DSLInput | None = None
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
            except TracecatValidationError as e:
                errors.append({"type": "dsl", "message": str(e)})
            except ValidationError as e:
                errors.append({"type": "dsl", "message": str(e)})

            if errors or dsl is None:
                return _json({"valid": False, "errors": errors})

            # Tier 2: Semantic validation
            val_results = await validate_dsl(session=svc.session, dsl=dsl, role=role)
            if val_results:
                for vr in val_results:
                    errors.append(_validation_result_payload(vr))
                return _json({"valid": False, "errors": errors})

            return _json({"valid": True, "errors": []})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to validate workflow", error=msg)
        return _json(
            {
                "valid": False,
                "errors": [
                    {
                        "type": "internal",
                        "message": msg,
                        "exc_type": type(e).__name__,
                    }
                ],
            }
        )


@mcp.tool()
async def publish_workflow(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Publish (commit) a workflow, creating a new versioned definition.

    This validates the workflow, freezes registry dependencies, and creates a
    new workflow definition version.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with workflow_id, status, message, version, and any errors.
    """
    from tracecat.exceptions import TracecatValidationError
    from tracecat.registry.lock.service import RegistryLockService
    from tracecat.validation.service import validate_dsl
    from tracecat.workflow.management.definitions import WorkflowDefinitionsService
    from tracecat.workflow.management.management import WorkflowsManagementService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowsManagementService.with_session(role=role) as svc:
            session = svc.session
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")

            # Tier 1: Build DSL
            construction_errors: list[dict[str, Any]] = []
            dsl: DSLInput | None = None
            try:
                dsl = await svc.build_dsl_from_workflow(workflow)
            except TracecatValidationError as e:
                construction_errors.append(
                    {
                        "type": "dsl",
                        "status": "error",
                        "message": str(e),
                    }
                )
            except ValidationError as e:
                construction_errors.append(
                    {
                        "type": "dsl",
                        "status": "error",
                        "message": str(e),
                    }
                )

            if construction_errors:
                return _json(
                    {
                        "workflow_id": workflow_id,
                        "status": "failure",
                        "message": f"DSL construction failed with {len(construction_errors)} errors",
                        "errors": construction_errors,
                    }
                )

            if dsl is None:
                raise ToolError("DSL should be defined if no construction errors")

            # Tier 2: Semantic validation
            val_errors = await validate_dsl(session=session, dsl=dsl, role=role)
            if val_errors:
                return _json(
                    {
                        "workflow_id": workflow_id,
                        "status": "failure",
                        "message": f"{len(val_errors)} validation error(s)",
                        "errors": [_validation_result_payload(vr) for vr in val_errors],
                    }
                )

            # Phase 1: Resolve registry lock
            lock_service = RegistryLockService(session, role)
            action_names = {action.action for action in dsl.actions}
            registry_lock = await lock_service.resolve_lock_with_bindings(action_names)
            workflow.registry_lock = registry_lock.model_dump()

            # Phase 2: Create workflow definition
            defn_service = WorkflowDefinitionsService(session, role=role)
            defn = await defn_service.create_workflow_definition(
                wf_id,
                dsl,
                alias=workflow.alias,
                registry_lock=registry_lock,
                commit=False,
            )

            # Phase 3: Update workflow version
            workflow.version = defn.version
            session.add(workflow)
            session.add(defn)
            await session.commit()
            await session.refresh(workflow)
            await session.refresh(defn)

            return _json(
                {
                    "workflow_id": workflow_id,
                    "status": "success",
                    "message": "Workflow published successfully",
                    "version": defn.version,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to publish workflow", error=msg)
        raise ToolError(f"Failed to publish workflow: {msg}") from None


# ---------------------------------------------------------------------------
# Execution tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def run_draft_workflow(
    workspace_id: str,
    workflow_id: str,
    inputs: str | None = None,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Run a workflow from its current draft state (without publishing).

    Optionally update the workflow's title/description before running.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        inputs: Optional JSON string of trigger inputs.
        title: Optional new title to set before running.
        description: Optional new description to set before running.

    Returns JSON with workflow_id, execution_id, and a message.
    """
    from tracecat.exceptions import TracecatValidationError
    from tracecat.workflow.executions.service import WorkflowExecutionsService
    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.management.schemas import WorkflowUpdate

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Optionally update workflow first
        if title or description:
            async with WorkflowsManagementService.with_session(role=role) as svc:
                await svc.update_workflow(
                    wf_id,
                    WorkflowUpdate(title=title, description=description),
                )

        # Build DSL from draft
        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")
            try:
                dsl_input = await svc.build_dsl_from_workflow(workflow)
            except (TracecatValidationError, ValidationError) as e:
                raise ToolError(f"Draft workflow has validation errors: {e}") from e

        # Validate and parse trigger inputs before dispatch
        payload = _validate_and_parse_trigger_inputs(dsl_input, inputs)
        exec_service = await WorkflowExecutionsService.connect(role=role)
        response = await exec_service.create_draft_workflow_execution_wait_for_start(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=payload,
        )
        return _json(
            {
                "workflow_id": str(response["wf_id"]),
                "execution_id": str(response["wf_exec_id"]),
                "message": response["message"],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to run draft workflow", error=msg)
        raise ToolError(f"Failed to run draft workflow: {msg}") from None


@mcp.tool()
async def run_published_workflow(
    workspace_id: str,
    workflow_id: str,
    inputs: str | None = None,
) -> str:
    """Run the latest published version of a workflow.

    The workflow must have been published (committed) at least once.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        inputs: Optional JSON string of trigger inputs.

    Returns JSON with workflow_id, execution_id, and a message.
    """
    from sqlalchemy import select

    from tracecat.db.engine import get_async_session_context_manager
    from tracecat.db.models import WorkflowDefinition
    from tracecat.registry.lock.types import RegistryLock
    from tracecat.workflow.executions.service import WorkflowExecutionsService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Fetch latest workflow definition scoped to the caller's workspace
        async with get_async_session_context_manager() as session:
            result = await session.execute(
                select(WorkflowDefinition)
                .where(
                    WorkflowDefinition.workflow_id == wf_id,
                    WorkflowDefinition.workspace_id == ws_id,
                )
                .order_by(WorkflowDefinition.version.desc())
            )
            defn = result.scalars().first()
            if not defn:
                raise ToolError(
                    f"No published definition found for workflow {workflow_id}. "
                    "Publish the workflow first using publish_workflow."
                )

            dsl_input = DSLInput(**defn.content)
            registry_lock = (
                RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None
            )

        # Validate and parse trigger inputs before dispatch
        payload = _validate_and_parse_trigger_inputs(dsl_input, inputs)
        exec_service = await WorkflowExecutionsService.connect(role=role)
        response = await exec_service.create_workflow_execution_wait_for_start(
            dsl=dsl_input,
            wf_id=wf_id,
            payload=payload,
            registry_lock=registry_lock,
        )
        return _json(
            {
                "workflow_id": str(response["wf_id"]),
                "execution_id": str(response["wf_exec_id"]),
                "message": response["message"],
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except BaseException as e:
        msg = str(e)
        if isinstance(e, BaseExceptionGroup):
            msgs = [str(exc) for exc in e.exceptions]
            msg = "; ".join(msgs)
        logger.error("Failed to run published workflow", error=msg)
        raise ToolError(f"Failed to run published workflow: {msg}") from None


@mcp.tool()
async def run_workflow_and_wait(
    workspace_id: str,
    workflow_id: str,
    inputs: str | None = None,
    use_published: bool = True,
    timeout_seconds: int = 120,
) -> str:
    """Run a workflow and wait for completion up to timeout_seconds.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        inputs: Optional JSON string of workflow trigger inputs.
        use_published: Run latest published workflow definition if True, else run draft.
        timeout_seconds: Maximum wait time for completion.

    Returns JSON with workflow_id, execution_id, status, and result/error details.
    """
    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)
        wait_timeout = max(1, timeout_seconds)
        exec_service = await WorkflowExecutionsService.connect(role=role)
        wf_exec_id = generate_exec_id(wf_id)
        dsl_input: DSLInput | None = None
        payload: Any | None

        if use_published:
            async with get_async_session_context_manager() as session:
                result = await session.execute(
                    select(WorkflowDefinition)
                    .where(
                        WorkflowDefinition.workflow_id == wf_id,
                        WorkflowDefinition.workspace_id == ws_id,
                    )
                    .order_by(WorkflowDefinition.version.desc())
                )
                defn = result.scalars().first()
                if not defn:
                    raise ToolError(
                        f"No published definition found for workflow {workflow_id}. "
                        "Publish the workflow first using publish_workflow."
                    )
                dsl_input = DSLInput(**defn.content)
                registry_lock = (
                    RegistryLock.model_validate(defn.registry_lock)
                    if defn.registry_lock
                    else None
                )

            payload = _validate_and_parse_trigger_inputs(dsl_input, inputs)
            await exec_service.create_workflow_execution_wait_for_start(
                dsl=dsl_input,
                wf_id=wf_id,
                payload=payload,
                wf_exec_id=wf_exec_id,
                registry_lock=registry_lock,
            )
        else:
            async with WorkflowsManagementService.with_session(role=role) as svc:
                workflow = await svc.get_workflow(wf_id)
                if workflow is None:
                    raise ToolError(f"Workflow {workflow_id} not found")
                dsl_input = await svc.build_dsl_from_workflow(workflow)

            payload = _validate_and_parse_trigger_inputs(dsl_input, inputs)
            await exec_service.create_draft_workflow_execution_wait_for_start(
                dsl=dsl_input,
                wf_id=wf_id,
                payload=payload,
                wf_exec_id=wf_exec_id,
            )

        try:
            result = await asyncio.wait_for(
                exec_service.handle(wf_exec_id).result(),
                timeout=wait_timeout,
            )
            return _json(
                {
                    "workflow_id": str(wf_id),
                    "execution_id": wf_exec_id,
                    "status": "completed",
                    "execution_status": "COMPLETED",
                    "result": result,
                    "input": payload,
                }
            )
        except TimeoutError:
            execution = await exec_service.get_execution(wf_exec_id)
            return _json(
                {
                    "workflow_id": str(wf_id),
                    "execution_id": wf_exec_id,
                    "status": "running",
                    "execution_status": _format_temporal_status(
                        execution.status if execution else None
                    ),
                    "message": (
                        f"Workflow is still running after {wait_timeout} seconds. "
                        "Use execution_id to fetch status later."
                    ),
                    "input": payload,
                }
            )
        except WorkflowFailureError as e:
            execution = await exec_service.get_execution(wf_exec_id)
            return _json(
                {
                    "workflow_id": str(wf_id),
                    "execution_id": wf_exec_id,
                    "status": "failed",
                    "execution_status": _format_temporal_status(
                        execution.status if execution else None
                    ),
                    "error": _serialize_workflow_failure(e),
                    "result": None,
                    "input": payload,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to run workflow and wait", error=str(e))
        raise ToolError(f"Failed to run workflow and wait: {e}") from None


@mcp.tool()
async def list_workflow_executions(
    workspace_id: str,
    workflow_id: str,
    limit: int = 20,
) -> str:
    """List recent executions for a workflow.

    Use this to see run history, check which runs succeeded or failed, and
    find execution IDs for deeper inspection with get_workflow_execution.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        limit: Maximum number of executions to return (1-100, default 20).

    Returns JSON array of execution objects with id, run_id, status,
    start_time, close_time, trigger_type, and execution_type.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)
        limit = max(1, min(limit, 100))

        exec_service = await WorkflowExecutionsService.connect(role=role)
        executions = await exec_service.list_executions(workflow_id=wf_id, limit=limit)
        items: list[dict[str, Any]] = []
        for execution in executions:
            trigger_type = None
            execution_type = None
            try:
                trigger_type = get_trigger_type_from_search_attr(
                    execution.typed_search_attributes, execution.id
                )
                execution_type = get_execution_type_from_search_attr(
                    execution.typed_search_attributes
                )
            except Exception:
                pass
            items.append(
                {
                    "id": execution.id,
                    "run_id": execution.run_id,
                    "status": _format_temporal_status(execution.status),
                    "start_time": str(execution.start_time),
                    "close_time": (
                        str(execution.close_time) if execution.close_time else None
                    ),
                    "trigger_type": str(trigger_type) if trigger_type else None,
                    "execution_type": str(execution_type) if execution_type else None,
                }
            )
        return _json(items)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list workflow executions", error=str(e))
        raise ToolError(f"Failed to list workflow executions: {e}") from None


@mcp.tool()
async def get_workflow_execution(
    workspace_id: str,
    execution_id: str,
) -> str:
    """Get status and details of a specific workflow execution.

    Returns execution metadata (status, timing) and a compact event timeline
    showing each action's status, timing, and any errors. Use this to debug
    failed runs or check the progress of running workflows.

    Args:
        workspace_id: The workspace ID.
        execution_id: The workflow execution ID (returned by run_* tools or
            list_workflow_executions).

    Returns JSON with execution metadata (id, run_id, status, start_time,
    close_time) and an events array with per-action status, timing, inputs,
    results, and errors.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        exec_service = await WorkflowExecutionsService.connect(role=role)

        execution = await exec_service.get_execution(execution_id)
        if execution is None:
            raise ToolError(f"Execution {execution_id} not found")

        trigger_type = None
        execution_type = None
        try:
            trigger_type = get_trigger_type_from_search_attr(
                execution.typed_search_attributes, execution.id
            )
            execution_type = get_execution_type_from_search_attr(
                execution.typed_search_attributes
            )
        except Exception:
            pass

        # Get compact event history for action-level details
        compact_events = await exec_service.list_workflow_execution_events_compact(
            execution_id
        )

        events_payload: list[dict[str, Any]] = []
        for event in compact_events:
            event_data: dict[str, Any] = {
                "action_ref": event.action_ref,
                "action_name": event.action_name,
                "status": str(event.status),
                "schedule_time": str(event.schedule_time),
                "start_time": str(event.start_time) if event.start_time else None,
                "close_time": str(event.close_time) if event.close_time else None,
            }
            if event.action_error is not None:
                error_info: dict[str, Any] = {
                    "message": event.action_error.message,
                }
                if event.action_error.cause:
                    error_info["cause"] = event.action_error.cause
                event_data["error"] = error_info
            if event.action_result is not None:
                try:
                    result_str = json.dumps(event.action_result, default=str)
                    if len(result_str) > 2000:
                        event_data["result_truncated"] = result_str[:2000] + "..."
                    else:
                        event_data["result"] = event.action_result
                except (TypeError, ValueError):
                    event_data["result"] = str(event.action_result)[:2000]
            events_payload.append(event_data)

        return _json(
            {
                "id": execution.id,
                "run_id": execution.run_id,
                "status": _format_temporal_status(execution.status),
                "start_time": str(execution.start_time),
                "close_time": (
                    str(execution.close_time) if execution.close_time else None
                ),
                "trigger_type": str(trigger_type) if trigger_type else None,
                "execution_type": str(execution_type) if execution_type else None,
                "history_length": execution.history_length,
                "events": events_payload,
            }
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get workflow execution", error=str(e))
        raise ToolError(f"Failed to get workflow execution: {e}") from None


# ---------------------------------------------------------------------------
# Schedule tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workflow_schedules(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """List all schedules for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns a JSON array of schedule objects.
    """
    from tracecat.workflow.schedules.schemas import ScheduleRead
    from tracecat.workflow.schedules.service import WorkflowSchedulesService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with WorkflowSchedulesService.with_session(role=role) as svc:
            schedules = await svc.list_schedules(workflow_id=wf_id)
            schedule_reads = ScheduleRead.list_adapter().validate_python(schedules)
            return _json([s.model_dump(mode="json") for s in schedule_reads])
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list schedules", error=str(e))
        raise ToolError(f"Failed to list schedules: {e}") from None


@mcp.tool()
async def create_schedule(
    workspace_id: str,
    workflow_id: str,
    cron: str | None = None,
    every: str | None = None,
    offset: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    inputs: str | None = None,
    schedule_status: str = "online",
) -> str:
    """Create a schedule for a workflow.

    The workflow must be published (committed) before creating a schedule.
    Provide either 'cron' or 'every' (ISO 8601 duration like "PT1H" for hourly).

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        cron: Cron expression (e.g. "0 9 * * 1-5" for weekdays at 9am).
        every: ISO 8601 duration (e.g. "PT1H" for every hour, "P1D" for daily).
        offset: ISO 8601 duration offset for the schedule.
        start_at: ISO 8601 datetime for when to start the schedule.
        end_at: ISO 8601 datetime for when to end the schedule.
        inputs: Optional JSON string of workflow trigger inputs.
        schedule_status: "online" or "offline" (default "online").

    Returns JSON with the created schedule details.
    """

    from tracecat.workflow.management.management import WorkflowsManagementService
    from tracecat.workflow.schedules.schemas import ScheduleCreate, ScheduleRead
    from tracecat.workflow.schedules.service import WorkflowSchedulesService

    try:
        email = get_email_from_token()
        ws_id = uuid.UUID(workspace_id)
        role = await resolve_role(email, ws_id)
        wf_id = WorkflowUUID.new(workflow_id)

        # Verify workflow exists and is published
        async with WorkflowsManagementService.with_session(role=role) as svc:
            workflow = await svc.get_workflow(wf_id)
            if not workflow:
                raise ToolError(f"Workflow {workflow_id} not found")
            if not workflow.version:
                raise ToolError(
                    "Workflow must be published before creating a schedule. "
                    "Use publish_workflow first."
                )

        # Parse inputs
        parsed_inputs = json.loads(inputs) if inputs else None
        parsed_every = _parse_iso8601_duration(every) if every else None
        parsed_offset = _parse_iso8601_duration(offset) if offset else None

        create_params = ScheduleCreate(
            workflow_id=workflow_id,
            cron=cron,
            every=parsed_every,
            offset=parsed_offset,
            start_at=start_at,  # pyright: ignore[reportArgumentType]
            end_at=end_at,  # pyright: ignore[reportArgumentType]
            inputs=parsed_inputs,
            status=schedule_status,  # pyright: ignore[reportArgumentType]
        )

        async with WorkflowSchedulesService.with_session(role=role) as svc:
            schedule = await svc.create_schedule(create_params)
            schedule_read = ScheduleRead.model_validate(schedule)
            return _json(schedule_read.model_dump(mode="json"))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create schedule", error=str(e))
        raise ToolError(f"Failed to create schedule: {e}") from None


@mcp.tool()
async def update_schedule(
    workspace_id: str,
    schedule_id: str,
    cron: str | None = None,
    every: str | None = None,
    offset: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    inputs: str | None = None,
    status: str | None = None,
) -> str:
    """Update an existing schedule.

    Args:
        workspace_id: The workspace ID.
        schedule_id: The schedule ID to update.
        cron: New cron expression (e.g. "0 9 * * 1-5").
        every: New ISO 8601 duration (e.g. "PT1H").
        offset: New ISO 8601 duration offset.
        start_at: New ISO 8601 datetime for schedule start.
        end_at: New ISO 8601 datetime for schedule end.
        inputs: Optional JSON string of workflow trigger inputs.
        status: "online" or "offline".

    Returns JSON with the updated schedule details.
    """
    from tracecat.workflow.schedules.schemas import ScheduleRead, ScheduleUpdate
    from tracecat.workflow.schedules.service import WorkflowSchedulesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        sched_id = uuid.UUID(schedule_id)

        parsed_inputs = json.loads(inputs) if inputs else None
        parsed_every = _parse_iso8601_duration(every) if every else None
        parsed_offset = _parse_iso8601_duration(offset) if offset else None

        update_params = ScheduleUpdate(
            cron=cron,
            every=parsed_every,
            offset=parsed_offset,
            start_at=start_at,  # pyright: ignore[reportArgumentType]
            end_at=end_at,  # pyright: ignore[reportArgumentType]
            inputs=parsed_inputs,
            status=status,  # pyright: ignore[reportArgumentType]
        )

        async with WorkflowSchedulesService.with_session(role=role) as svc:
            schedule = await svc.update_schedule(sched_id, update_params)
            schedule_read = ScheduleRead.model_validate(schedule)
            return _json(schedule_read.model_dump(mode="json"))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update schedule", error=str(e))
        raise ToolError(f"Failed to update schedule: {e}") from None


@mcp.tool()
async def delete_schedule(
    workspace_id: str,
    schedule_id: str,
) -> str:
    """Delete a schedule.

    Args:
        workspace_id: The workspace ID.
        schedule_id: The schedule ID to delete.

    Returns a confirmation message.
    """
    from tracecat.workflow.schedules.service import WorkflowSchedulesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        sched_id = uuid.UUID(schedule_id)

        async with WorkflowSchedulesService.with_session(role=role) as svc:
            await svc.delete_schedule(sched_id)
        return _json({"message": f"Schedule {schedule_id} deleted successfully"})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete schedule", error=str(e))
        raise ToolError(f"Failed to delete schedule: {e}") from None


# ---------------------------------------------------------------------------
# Webhook tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_webhook(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Get webhook configuration for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with the webhook configuration (id, secret, status, methods,
    url, entrypoint_ref, allowlisted_cidrs, filters, api_key).
    """
    from tracecat.webhooks import service as webhook_service
    from tracecat.webhooks.schemas import WebhookRead

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with get_async_session_context_manager() as session:
            webhook = await webhook_service.get_webhook(
                session=session,
                workspace_id=role.workspace_id,
                workflow_id=wf_id,
            )
            if webhook is None:
                raise ToolError(f"Webhook not found for workflow {workflow_id}")
            webhook_read = WebhookRead.model_validate(webhook, from_attributes=True)
            return _json(webhook_read.model_dump(mode="json"))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get webhook", error=str(e))
        raise ToolError(f"Failed to get webhook: {e}") from None


@mcp.tool()
async def create_webhook(
    workspace_id: str,
    workflow_id: str,
    status: str = "offline",
    methods: str | None = None,
    allowlisted_cidrs: str | None = None,
) -> str:
    """Create a webhook for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        status: "online" or "offline" (default "offline").
        methods: JSON array of HTTP methods, e.g. '["POST"]' (default ["POST"]).
        allowlisted_cidrs: Optional JSON array of CIDR strings.

    Returns JSON with the created webhook details.
    """
    from tracecat.db.models import Webhook
    from tracecat.webhooks.schemas import WebhookRead

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        parsed_methods = _parse_json_arg(methods, "methods") or ["POST"]
        parsed_cidrs = _parse_json_arg(allowlisted_cidrs, "allowlisted_cidrs") or []

        async with get_async_session_context_manager() as session:
            webhook = Webhook(
                workspace_id=role.workspace_id,
                workflow_id=wf_id,
                status=status,
                methods=parsed_methods,
                allowlisted_cidrs=parsed_cidrs,
            )
            session.add(webhook)
            await session.commit()
            await session.refresh(webhook)
            webhook_read = WebhookRead.model_validate(webhook, from_attributes=True)
            return _json(webhook_read.model_dump(mode="json"))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create webhook", error=str(e))
        raise ToolError(f"Failed to create webhook: {e}") from None


@mcp.tool()
async def update_webhook(
    workspace_id: str,
    workflow_id: str,
    status: str | None = None,
    methods: str | None = None,
    entrypoint_ref: str | None = None,
    allowlisted_cidrs: str | None = None,
) -> str:
    """Update webhook configuration for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        status: "online" or "offline".
        methods: JSON array of HTTP methods, e.g. '["GET", "POST"]'.
        entrypoint_ref: Entrypoint action ref.
        allowlisted_cidrs: JSON array of CIDR strings.

    Returns a confirmation message.
    """
    from tracecat.webhooks import service as webhook_service
    from tracecat.webhooks.schemas import WebhookUpdate

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        parsed_methods = _parse_json_arg(methods, "methods")
        parsed_cidrs = _parse_json_arg(allowlisted_cidrs, "allowlisted_cidrs")

        update_params = WebhookUpdate(
            status=status,  # pyright: ignore[reportArgumentType]
            methods=parsed_methods,
            entrypoint_ref=entrypoint_ref,
            allowlisted_cidrs=parsed_cidrs,
        )

        async with get_async_session_context_manager() as session:
            webhook = await webhook_service.get_webhook(
                session=session,
                workspace_id=role.workspace_id,
                workflow_id=wf_id,
            )
            if webhook is None:
                raise ToolError(f"Webhook not found for workflow {workflow_id}")

            for key, value in update_params.model_dump(exclude_unset=True).items():
                setattr(webhook, key, value)

            session.add(webhook)
            await session.commit()
        return _json(
            {"message": f"Webhook for workflow {workflow_id} updated successfully"}
        )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update webhook", error=str(e))
        raise ToolError(f"Failed to update webhook: {e}") from None


# ---------------------------------------------------------------------------
# Case trigger tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_case_trigger(
    workspace_id: str,
    workflow_id: str,
) -> str:
    """Get case trigger configuration for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.

    Returns JSON with the case trigger (id, workflow_id, status, event_types,
    tag_filters).
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        async with CaseTriggersService.with_session(role=role) as svc:
            case_trigger = await svc.get_case_trigger(wf_id)
            ct_read = CaseTriggerRead.model_validate(case_trigger, from_attributes=True)
            return _json(ct_read.model_dump(mode="json"))
    except TracecatNotFoundError as e:
        raise ToolError(str(e)) from e
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get case trigger", error=str(e))
        raise ToolError(f"Failed to get case trigger: {e}") from None


@mcp.tool()
async def create_case_trigger(
    workspace_id: str,
    workflow_id: str,
    status: str = "offline",
    event_types: str | None = None,
    tag_filters: str | None = None,
) -> str:
    """Create or upsert a case trigger for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        status: "online" or "offline" (default "offline").
        event_types: JSON array of case event type strings using underscores
            (e.g. '["case_created", "case_updated"]'). Valid values:
            case_created, case_updated, case_closed, case_reopened, case_viewed,
            priority_changed, severity_changed, status_changed, fields_changed,
            assignee_changed, attachment_created, attachment_deleted, tag_added,
            tag_removed, payload_changed, task_created, task_deleted,
            task_status_changed, task_priority_changed, task_workflow_changed,
            task_assignee_changed, dropdown_value_changed.
        tag_filters: JSON array of tag filter strings (e.g. '["malware", "phishing"]').

    Returns JSON with the case trigger details.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        parsed_event_types = _parse_json_arg(event_types, "event_types") or []
        parsed_tag_filters = _parse_json_arg(tag_filters, "tag_filters") or []

        config = CaseTriggerCreate(
            status=status,  # pyright: ignore[reportArgumentType]
            event_types=parsed_event_types,
            tag_filters=parsed_tag_filters,
        )

        async with CaseTriggersService.with_session(role=role) as svc:
            case_trigger = await svc.upsert_case_trigger(
                wf_id, config, create_missing_tags=True
            )
            ct_read = CaseTriggerRead.model_validate(case_trigger, from_attributes=True)
            return _json(ct_read.model_dump(mode="json"))
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create case trigger", error=str(e))
        raise ToolError(f"Failed to create case trigger: {e}") from None


@mcp.tool()
async def update_case_trigger(
    workspace_id: str,
    workflow_id: str,
    status: str | None = None,
    event_types: str | None = None,
    tag_filters: str | None = None,
) -> str:
    """Update an existing case trigger for a workflow.

    Args:
        workspace_id: The workspace ID.
        workflow_id: The workflow ID.
        status: "online" or "offline".
        event_types: JSON array of case event type strings using underscores
            (e.g. '["case_created", "case_updated"]'). See create_case_trigger
            for full list of valid values.
        tag_filters: JSON array of tag filter strings.

    Returns a confirmation message.
    """
    try:
        _, role = await _resolve_workspace_role(workspace_id)
        wf_id = WorkflowUUID.new(workflow_id)

        parsed_event_types = _parse_json_arg(event_types, "event_types")
        parsed_tag_filters = _parse_json_arg(tag_filters, "tag_filters")

        update_params = CaseTriggerUpdate(
            status=status,  # pyright: ignore[reportArgumentType]
            event_types=parsed_event_types,
            tag_filters=parsed_tag_filters,
        )

        async with CaseTriggersService.with_session(role=role) as svc:
            await svc.update_case_trigger(
                wf_id, update_params, create_missing_tags=True
            )
        return _json(
            {
                "message": (
                    f"Case trigger for workflow {workflow_id} updated successfully"
                )
            }
        )
    except TracecatNotFoundError as e:
        raise ToolError(str(e)) from e
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update case trigger", error=str(e))
        raise ToolError(f"Failed to update case trigger: {e}") from None


# ---------------------------------------------------------------------------
# Tables, cases, variables, and secrets metadata tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_tables(workspace_id: str) -> str:
    """List workspace tables."""
    from tracecat.tables.service import TablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            tables = await svc.list_tables()
            return _json(
                [{"id": str(table.id), "name": table.name} for table in tables]
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list tables", error=str(e))
        raise ToolError(f"Failed to list tables: {e}") from None


@mcp.tool()
async def create_table(
    workspace_id: str,
    name: str,
    columns_json: str | None = None,
) -> str:
    """Create a table with optional columns.

    Args:
        workspace_id: The workspace ID.
        name: Table name.
        columns_json: Optional JSON array of column definitions. Each column
            object has: name (str), type (str), nullable (bool, optional),
            default (str, optional), is_primary_key (bool, optional).
            Column type must be UPPERCASE — one of: TEXT, INTEGER, NUMERIC,
            DATE, BOOLEAN, TIMESTAMP, TIMESTAMPTZ, JSONB, UUID, SELECT,
            MULTI_SELECT.

    Returns JSON with the new table's id and name.
    """
    from tracecat.tables.schemas import TableCreate
    from tracecat.tables.service import TablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        columns = _parse_json_arg(columns_json, "columns_json") or []
        params = TableCreate(name=name, columns=columns)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.create_table(params)
            return _json({"id": str(table.id), "name": table.name})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create table", error=str(e))
        raise ToolError(f"Failed to create table: {e}") from None


@mcp.tool()
async def get_table(workspace_id: str, table_id: str) -> str:
    """Get table definition and index metadata."""
    from tracecat.tables.enums import SqlType
    from tracecat.tables.service import TablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            index_columns = await svc.get_index(table)
            return _json(
                {
                    "id": str(table.id),
                    "name": table.name,
                    "columns": [
                        {
                            "id": str(column.id),
                            "name": column.name,
                            "type": SqlType(column.type).value,
                            "nullable": column.nullable,
                            "default": column.default,
                            "is_index": column.name in index_columns,
                            "options": column.options,
                        }
                        for column in table.columns
                    ],
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get table", error=str(e))
        raise ToolError(f"Failed to get table: {e}") from None


@mcp.tool()
async def update_table(
    workspace_id: str,
    table_id: str,
    name: str | None = None,
) -> str:
    """Update table metadata."""
    from tracecat.tables.schemas import TableUpdate
    from tracecat.tables.service import TablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            updated = await svc.update_table(table, TableUpdate(name=name))
            return _json({"id": str(updated.id), "name": updated.name})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update table", error=str(e))
        raise ToolError(f"Failed to update table: {e}") from None


@mcp.tool()
async def delete_table(workspace_id: str, table_id: str) -> str:
    """Delete a table."""
    from tracecat.tables.service import TablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            await svc.delete_table(table)
            return _json({"message": f"Table {table_id} deleted successfully"})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete table", error=str(e))
        raise ToolError(f"Failed to delete table: {e}") from None


@mcp.tool()
async def insert_table_row(
    workspace_id: str,
    table_id: str,
    row_json: str,
    upsert: bool = False,
) -> str:
    """Insert a table row."""
    from tracecat.tables.schemas import TableRowInsert
    from tracecat.tables.service import TablesService

    try:
        row_data = _parse_json_arg(row_json, "row_json")
        if not isinstance(row_data, dict):
            raise ToolError("row_json must decode to a JSON object")
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            row = await svc.insert_row(
                table, TableRowInsert(data=row_data, upsert=upsert)
            )
            return _json(row)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to insert table row", error=str(e))
        raise ToolError(f"Failed to insert table row: {e}") from None


@mcp.tool()
async def update_table_row(
    workspace_id: str,
    table_id: str,
    row_id: str,
    row_json: str,
) -> str:
    """Update a table row."""
    from tracecat.tables.service import TablesService

    try:
        row_data = _parse_json_arg(row_json, "row_json")
        if not isinstance(row_data, dict):
            raise ToolError("row_json must decode to a JSON object")
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            row = await svc.update_row(table, uuid.UUID(row_id), row_data)
            return _json(row)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update table row", error=str(e))
        raise ToolError(f"Failed to update table row: {e}") from None


@mcp.tool()
async def delete_table_row(
    workspace_id: str,
    table_id: str,
    row_id: str,
) -> str:
    """Delete a table row."""
    from tracecat.tables.service import TablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            await svc.delete_row(table, uuid.UUID(row_id))
            return _json({"message": f"Row {row_id} deleted successfully"})
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete table row", error=str(e))
        raise ToolError(f"Failed to delete table row: {e}") from None


@mcp.tool()
async def search_table_rows(
    workspace_id: str,
    table_id: str,
    search_term: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Search rows in a table."""
    from tracecat.tables.service import TablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        async with TablesService.with_session(role=role) as svc:
            table = await svc.get_table(uuid.UUID(table_id))
            rows = await svc.search_rows(
                table,
                search_term=search_term,
                limit=limit,
                offset=offset,
            )
            return _json(rows)
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to search table rows", error=str(e))
        raise ToolError(f"Failed to search table rows: {e}") from None


@mcp.tool()
async def create_case(
    workspace_id: str,
    summary: str,
    description: str,
    priority: str = "unknown",
    severity: str = "unknown",
    status: str = "new",
    fields_json: str | None = None,
    payload_json: str | None = None,
) -> str:
    """Create a case."""
    from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
    from tracecat.cases.schemas import CaseCreate
    from tracecat.cases.service import CasesService

    try:
        fields = _parse_json_arg(fields_json, "fields_json")
        payload = _parse_json_arg(payload_json, "payload_json")
        _, role = await _resolve_workspace_role(workspace_id)
        async with CasesService.with_session(role=role) as svc:
            case = await svc.create_case(
                CaseCreate(
                    summary=summary,
                    description=description,
                    priority=CasePriority(priority),
                    severity=CaseSeverity(severity),
                    status=CaseStatus(status),
                    fields=fields,
                    payload=payload,
                )
            )
            return _json(
                {
                    "id": str(case.id),
                    "short_id": case.short_id,
                    "summary": case.summary,
                    "status": case.status,
                    "priority": case.priority,
                    "severity": case.severity,
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to create case", error=str(e))
        raise ToolError(f"Failed to create case: {e}") from None


@mcp.tool()
async def list_cases(
    workspace_id: str,
    limit: int = 50,
    status: str | None = None,
    priority: str | None = None,
    severity: str | None = None,
    search: str | None = None,
) -> str:
    """List cases."""
    from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
    from tracecat.cases.service import CasesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        limit = max(1, min(limit, 200))
        async with CasesService.with_session(role=role) as svc:
            cases = await svc.search_cases(
                search_term=search,
                status=CaseStatus(status) if status else None,
                priority=CasePriority(priority) if priority else None,
                severity=CaseSeverity(severity) if severity else None,
                limit=limit,
            )
            return _json(
                [
                    {
                        "id": str(case.id),
                        "short_id": case.short_id,
                        "summary": case.summary,
                        "status": case.status,
                        "priority": case.priority,
                        "severity": case.severity,
                    }
                    for case in cases
                ]
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list cases", error=str(e))
        raise ToolError(f"Failed to list cases: {e}") from None


@mcp.tool()
async def get_case(workspace_id: str, case_id: str) -> str:
    """Get case details."""
    from tracecat.cases.service import CasesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CasesService.with_session(role=role) as svc:
            case = await svc.get_case(uuid.UUID(case_id), track_view=False)
            if case is None:
                raise ToolError(f"Case {case_id} not found")
            return _json(
                {
                    "id": str(case.id),
                    "short_id": case.short_id,
                    "summary": case.summary,
                    "description": case.description,
                    "status": case.status,
                    "priority": case.priority,
                    "severity": case.severity,
                    "payload": case.payload,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get case", error=str(e))
        raise ToolError(f"Failed to get case: {e}") from None


@mcp.tool()
async def update_case(
    workspace_id: str,
    case_id: str,
    summary: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    fields_json: str | None = None,
    payload_json: str | None = None,
) -> str:
    """Update a case."""
    from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
    from tracecat.cases.schemas import CaseUpdate
    from tracecat.cases.service import CasesService

    try:
        fields = _parse_json_arg(fields_json, "fields_json")
        payload = _parse_json_arg(payload_json, "payload_json")
        _, role = await _resolve_workspace_role(workspace_id)
        async with CasesService.with_session(role=role) as svc:
            case = await svc.get_case(uuid.UUID(case_id), track_view=False)
            if case is None:
                raise ToolError(f"Case {case_id} not found")
            updated = await svc.update_case(
                case,
                CaseUpdate(
                    summary=summary,
                    description=description,
                    priority=CasePriority(priority) if priority else None,
                    severity=CaseSeverity(severity) if severity else None,
                    status=CaseStatus(status) if status else None,
                    fields=fields,
                    payload=payload,
                ),
            )
            return _json(
                {
                    "id": str(updated.id),
                    "short_id": updated.short_id,
                    "summary": updated.summary,
                    "status": updated.status,
                    "priority": updated.priority,
                    "severity": updated.severity,
                }
            )
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to update case", error=str(e))
        raise ToolError(f"Failed to update case: {e}") from None


@mcp.tool()
async def delete_case(workspace_id: str, case_id: str) -> str:
    """Delete a case."""
    from tracecat.cases.service import CasesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with CasesService.with_session(role=role) as svc:
            case = await svc.get_case(uuid.UUID(case_id), track_view=False)
            if case is None:
                raise ToolError(f"Case {case_id} not found")
            await svc.delete_case(case)
            return _json({"message": f"Case {case_id} deleted successfully"})
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to delete case", error=str(e))
        raise ToolError(f"Failed to delete case: {e}") from None


@mcp.tool()
async def list_variables(
    workspace_id: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
) -> str:
    """List workspace variables."""
    from tracecat.variables.service import VariablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with VariablesService.with_session(role=role) as svc:
            variables = await svc.list_variables(environment=environment)
            return _json(
                [
                    {
                        "id": str(variable.id),
                        "name": variable.name,
                        "description": variable.description,
                        "environment": variable.environment,
                        "keys": sorted(variable.values.keys()),
                    }
                    for variable in variables
                ]
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list variables", error=str(e))
        raise ToolError(f"Failed to list variables: {e}") from None


@mcp.tool()
async def get_variable(
    workspace_id: str,
    variable_name: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
) -> str:
    """Get a workspace variable."""
    from tracecat.variables.service import VariablesService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        async with VariablesService.with_session(role=role) as svc:
            variable = await svc.get_variable_by_name(
                variable_name, environment=environment
            )
            return _json(
                {
                    "id": str(variable.id),
                    "name": variable.name,
                    "description": variable.description,
                    "environment": variable.environment,
                    "values": variable.values,
                }
            )
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get variable", error=str(e))
        raise ToolError(f"Failed to get variable: {e}") from None


@mcp.tool()
async def list_secrets_metadata(
    workspace_id: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
    scope: str = "both",
) -> str:
    """List secret metadata without secret values."""
    from tracecat.secrets.service import SecretsService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        if scope not in {"workspace", "organization", "both"}:
            raise ToolError("scope must be one of: workspace, organization, both")
        result: list[dict[str, Any]] = []
        async with SecretsService.with_session(role=role) as svc:
            if scope in {"workspace", "both"}:
                workspace_secrets = await svc.list_secrets()
                for secret in workspace_secrets:
                    if secret.environment != environment:
                        continue
                    keys = [kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)]
                    result.append(
                        {
                            "id": str(secret.id),
                            "name": secret.name,
                            "type": secret.type,
                            "environment": secret.environment,
                            "scope": "workspace",
                            "keys": keys,
                            "tags": secret.tags,
                        }
                    )
            if scope in {"organization", "both"}:
                org_secrets = await svc.list_org_secrets()
                for secret in org_secrets:
                    if secret.environment != environment:
                        continue
                    keys = [kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)]
                    result.append(
                        {
                            "id": str(secret.id),
                            "name": secret.name,
                            "type": secret.type,
                            "environment": secret.environment,
                            "scope": "organization",
                            "keys": keys,
                            "tags": secret.tags,
                        }
                    )
            return _json(result)
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to list secrets metadata", error=str(e))
        raise ToolError(f"Failed to list secrets metadata: {e}") from None


@mcp.tool()
async def get_secret_metadata(
    workspace_id: str,
    secret_name: str,
    environment: str = DEFAULT_SECRETS_ENVIRONMENT,
    scope: str = "both",
) -> str:
    """Get secret metadata by name without secret values."""
    from tracecat.exceptions import TracecatNotFoundError
    from tracecat.secrets.service import SecretsService

    try:
        _, role = await _resolve_workspace_role(workspace_id)
        if scope not in {"workspace", "organization", "both"}:
            raise ToolError("scope must be one of: workspace, organization, both")
        async with SecretsService.with_session(role=role) as svc:
            if scope in {"workspace", "both"}:
                try:
                    secret = await svc.get_secret_by_name(
                        secret_name, environment=environment
                    )
                    return _json(
                        {
                            "id": str(secret.id),
                            "name": secret.name,
                            "type": secret.type,
                            "environment": secret.environment,
                            "scope": "workspace",
                            "keys": [
                                kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)
                            ],
                            "tags": secret.tags,
                        }
                    )
                except TracecatNotFoundError:
                    pass
            if scope in {"organization", "both"}:
                try:
                    secret = await svc.get_org_secret_by_name(
                        secret_name, environment=environment
                    )
                    return _json(
                        {
                            "id": str(secret.id),
                            "name": secret.name,
                            "type": secret.type,
                            "environment": secret.environment,
                            "scope": "organization",
                            "keys": [
                                kv.key for kv in svc.decrypt_keys(secret.encrypted_keys)
                            ],
                            "tags": secret.tags,
                        }
                    )
                except TracecatNotFoundError:
                    pass
        raise ToolError(f"Secret {secret_name!r} not found in scope={scope}")
    except ToolError:
        raise
    except ValueError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error("Failed to get secret metadata", error=str(e))
        raise ToolError(f"Failed to get secret metadata: {e}") from None


def _parse_iso8601_duration(duration_str: str) -> timedelta:
    """Parse a simple ISO 8601 duration string into a timedelta.

    Supports formats like PT1H, PT30M, P1D, PT1H30M, P1DT12H, etc.
    """
    pattern = r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
    match = re.fullmatch(pattern, duration_str)
    if not match:
        raise ValueError(f"Invalid ISO 8601 duration: {duration_str}")

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
