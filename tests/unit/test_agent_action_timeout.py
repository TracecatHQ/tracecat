"""Generic action-timeout wiring for agent-backed actions.

Agent timeouts clamp to [default, deployment ceiling] at parse time.
No boundary rejects: out-of-bounds values normalize wherever they are seen.
"""

import uuid

import pytest

from tracecat.agent import types as agent_types
from tracecat.agent.constants import AGENT_TIMEOUT_SECONDS_DEFAULT
from tracecat.config import TRACECAT__AGENT_SANDBOX_TIMEOUT
from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement
from tracecat.workflow.actions.schemas import ActionControlFlow, ActionRead
from tracecat.workflow.management.schemas import ExternalWorkflowDefinition


def _task(
    action: str, retry_policy: ActionRetryPolicy | None = None
) -> ActionStatement:
    if retry_policy is None:
        return ActionStatement(ref="task", action=action, args={})
    return ActionStatement(
        ref="task", action=action, args={}, retry_policy=retry_policy
    )


def _agent_action(timeout: int) -> dict[str, object]:
    return {
        "ref": "agent",
        "action": "ai.agent",
        "args": {},
        "retry_policy": {"timeout": timeout},
    }


def _dsl_payload(action: dict[str, object]) -> dict[str, object]:
    return {
        "title": "wf",
        "description": "d",
        "entrypoint": {"ref": action["ref"], "expects": {}},
        "actions": [action],
    }


def test_regular_action_keeps_default_timeout() -> None:
    assert _task("core.http_request").retry_policy.timeout == DEFAULT_ACTION_TIMEOUT


def test_agent_actions_default_to_thirty_minutes() -> None:
    for action in ("ai.agent", "ai.action", "ai.preset_agent"):
        assert _task(action).retry_policy.timeout == AGENT_TIMEOUT_SECONDS_DEFAULT


def test_agent_action_explicit_in_range_timeout_survives() -> None:
    task = _task("ai.agent", ActionRetryPolicy(timeout=2000))
    assert task.retry_policy.timeout == 2000


def test_agent_action_parsed_without_timeout_gets_default() -> None:
    task = ActionStatement.model_validate(
        {
            "ref": "agent",
            "action": "ai.agent",
            "args": {},
            "retry_policy": {"max_attempts": 2},
        }
    )
    assert task.retry_policy.timeout == AGENT_TIMEOUT_SECONDS_DEFAULT
    assert task.retry_policy.max_attempts == 2


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [
        (1, AGENT_TIMEOUT_SECONDS_DEFAULT),
        (900, AGENT_TIMEOUT_SECONDS_DEFAULT),
        (100_000, TRACECAT__AGENT_SANDBOX_TIMEOUT),
    ],
)
def test_agent_timeout_clamps_to_bounds(timeout: int, expected: int) -> None:
    """Out-of-bounds values normalize at parse time so the statement value is
    exactly what executes. There is no minimum below the default."""
    stmt = ActionStatement.model_validate(_agent_action(timeout))
    assert stmt.retry_policy.timeout == expected


def test_agent_timeout_lowered_ceiling_clamps_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 900)

    low = ActionStatement.model_validate(_agent_action(60))
    above = ActionStatement.model_validate(_agent_action(2000))
    assert low.retry_policy.timeout == 900
    assert above.retry_policy.timeout == 900


def test_non_agent_actions_are_never_clamped() -> None:
    stmt = ActionStatement.model_validate(
        {
            "ref": "http",
            "action": "core.http_request",
            "args": {},
            "retry_policy": {"timeout": 100_000},
        }
    )
    assert stmt.retry_policy.timeout == 100_000


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [(1, AGENT_TIMEOUT_SECONDS_DEFAULT), (100_000, TRACECAT__AGENT_SANDBOX_TIMEOUT)],
)
def test_external_workflow_definition_upload_normalizes(
    timeout: int, expected: int
) -> None:
    """YAML/JSON upload, MCP create, and internal create all funnel here."""
    payload = {"definition": _dsl_payload(_agent_action(timeout))}
    defn = ExternalWorkflowDefinition.model_validate(payload)
    assert defn.definition.actions[0].retry_policy.timeout == expected


def test_mcp_workflow_yaml_payload_normalizes() -> None:
    """MCP update_workflow inline definition_yaml boundary."""
    from tracecat.mcp.schemas import WorkflowYamlPayload

    payload = {"definition": _dsl_payload(_agent_action(100_000))}
    parsed = WorkflowYamlPayload.model_validate(payload)
    assert parsed.definition is not None
    assert parsed.definition.actions[0].retry_policy.timeout == (
        TRACECAT__AGENT_SANDBOX_TIMEOUT
    )


def test_workflow_edit_document_patch_normalizes() -> None:
    """edit_workflow JSON-Patch boundary (validate_workflow_patch_payload)."""
    from tracecat.mcp.schemas import WorkflowEditDocument

    payload = {
        "metadata": {"title": "workflow", "description": "d", "status": "offline"},
        "definition": {
            "entrypoint": {"ref": "agent"},
            "actions": [_agent_action(100_000)],
            "config": {},
        },
    }
    assert (
        WorkflowEditDocument.model_validate(payload)
        .definition.actions[0]
        .retry_policy.timeout
        == TRACECAT__AGENT_SANDBOX_TIMEOUT
    )


def test_git_sync_parse_normalizes_out_of_bounds() -> None:
    """Git-sync import clamps rather than reporting a diagnostic."""
    import yaml as yaml_lib

    from tracecat.workspace_sync.workflow import parse_workflow_spec

    content = yaml_lib.safe_dump(
        {
            "type": "workflow",
            "version": 1,
            "id": "my_workflow",
            "definition": _dsl_payload(_agent_action(100_000)),
        }
    )
    spec, diagnostic = parse_workflow_spec("workflows/my_workflow.yml", content)
    assert diagnostic is None
    assert spec is not None
    assert spec.definition.actions[0].retry_policy.timeout == (
        TRACECAT__AGENT_SANDBOX_TIMEOUT
    )


def test_legacy_db_row_with_baked_generic_default_clamps_up() -> None:
    """Rows persisted before clamping stored the full ActionControlFlow dump,
    baking the generic 300s default in as an explicit author value. Rebuilding
    statements from such rows must clamp the timeout up to the agent default."""
    from tracecat.db.models import Action
    from tracecat.dsl.common import build_action_statements_from_actions

    workflow_id = uuid.uuid4()
    legacy_row = Action(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        workflow_id=workflow_id,
        type="ai.agent",
        title="Agent",
        description="",
        inputs="",
        # Exactly what pre-clamp code persisted: model_dump() with defaults.
        control_flow=ActionControlFlow().model_dump(),
        upstream_edges=[
            {"source_id": f"trigger-{workflow_id}", "source_type": "trigger"}
        ],
    )
    stmts = build_action_statements_from_actions([legacy_row])
    assert len(stmts) == 1
    assert stmts[0].retry_policy.timeout == AGENT_TIMEOUT_SECONDS_DEFAULT


def test_action_write_api_clamps_out_of_bounds_agent_timeout() -> None:
    """Expand phase: the authoring surface clamps and persists instead of
    rejecting; a follow-up flips this to a 422 once telemetry is quiet."""
    from tracecat.workflow.actions.router import _clamp_agent_timeout

    def clamp(action_type: str, payload: dict | None) -> ActionControlFlow | None:
        cf = ActionControlFlow.model_validate(payload) if payload is not None else None
        _clamp_agent_timeout(action_type, cf)
        return cf

    # Out-of-bounds values normalize in place.
    low = clamp("ai.agent", {"retry_policy": {"timeout": 60}})
    assert low is not None and low.retry_policy.timeout == AGENT_TIMEOUT_SECONDS_DEFAULT
    high = clamp("ai.agent", {"retry_policy": {"timeout": 100_000}})
    assert high is not None
    assert high.retry_policy.timeout == TRACECAT__AGENT_SANDBOX_TIMEOUT

    # In-range, non-agent, unset, and absent control flow are untouched.
    in_range = clamp("ai.agent", {"retry_policy": {"timeout": 2000}})
    assert in_range is not None and in_range.retry_policy.timeout == 2000
    http = clamp("core.http_request", {"retry_policy": {"timeout": 100_000}})
    assert http is not None and http.retry_policy.timeout == 100_000
    # Omitted timeout (cleared field) resets to the default, persisted
    # explicitly so readback cannot re-inflate the generic 300s.
    unset = clamp("ai.agent", {"retry_policy": {"max_attempts": 3}})
    assert unset is not None
    assert unset.retry_policy.timeout == AGENT_TIMEOUT_SECONDS_DEFAULT
    assert "timeout" in unset.retry_policy.model_fields_set
    assert unset.retry_policy.max_attempts == 3
    assert clamp("ai.agent", None) is None


@pytest.mark.parametrize(
    "control_flow",
    [
        {},
        {"retry_policy": {}},
        {"retry_policy": {"timeout": 300}},
        {"retry_policy": {"timeout": AGENT_TIMEOUT_SECONDS_DEFAULT}},
        {"retry_policy": {"timeout": 100_000}},
    ],
)
def test_action_read_uses_lowered_deployment_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    control_flow: dict[str, object],
) -> None:
    """Legacy rows display the timeout that actually executes."""
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 900)

    action = ActionRead(
        id=uuid.uuid4(),
        type="ai.agent",
        title="Agent",
        description="",
        status="offline",
        inputs="",
        control_flow=ActionControlFlow.model_validate(control_flow),
        is_interactive=False,
    )

    assert action.control_flow.retry_policy.timeout == 900


def test_action_read_does_not_clamp_non_agent_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 900)

    action = ActionRead(
        id=uuid.uuid4(),
        type="core.http_request",
        title="HTTP request",
        description="",
        status="offline",
        inputs="",
        control_flow=ActionControlFlow.model_validate(
            {"retry_policy": {"timeout": 1800}}
        ),
        is_interactive=False,
    )

    assert action.control_flow.retry_policy.timeout == 1800


@pytest.mark.anyio
async def test_action_create_persists_agent_default_timeout() -> None:
    """New agent actions store the deployment default explicitly; explicit
    values and non-agent actions are untouched."""
    from unittest.mock import AsyncMock, MagicMock

    from tracecat.auth.types import Role
    from tracecat.identifiers import WorkflowUUID
    from tracecat.workflow.actions.schemas import ActionCreate
    from tracecat.workflow.actions.service import WorkflowActionService

    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    service = WorkflowActionService(
        session,
        role=Role(
            type="user",
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            scopes=frozenset({"workflow:create"}),
        ),
    )

    def create(action_type: str, control_flow: ActionControlFlow | None):
        return service.create_action(
            ActionCreate(
                workflow_id=WorkflowUUID.new(uuid.uuid4()),
                type=action_type,
                title="t",
                control_flow=control_flow,
            )
        )

    # Unset -> deployment default persisted explicitly (also when
    # control_flow is omitted entirely).
    agent = await create("ai.agent", None)
    assert agent.control_flow["retry_policy"]["timeout"] == (
        AGENT_TIMEOUT_SECONDS_DEFAULT
    )

    # Explicit in-range value survives.
    explicit = await create(
        "ai.agent",
        ActionControlFlow.model_validate({"retry_policy": {"timeout": 2000}}),
    )
    assert explicit.control_flow["retry_policy"]["timeout"] == 2000

    # Non-agent actions keep the generic default.
    http = await create("core.http_request", None)
    assert http.control_flow["retry_policy"]["timeout"] == DEFAULT_ACTION_TIMEOUT


@pytest.mark.anyio
async def test_graph_add_node_persists_agent_default_timeout() -> None:
    """The builder creates nodes via graph operations, not the actions API —
    the default injection must live on that path too."""
    from unittest.mock import AsyncMock, MagicMock

    from tracecat.auth.types import Role
    from tracecat.workflow.graph.service import WorkflowGraphService
    from tracecat.workflow.management.schemas import AddNodePayload

    session = MagicMock()
    session.flush = AsyncMock()
    added: list = []
    session.add = added.append
    service = WorkflowGraphService(
        session,
        role=Role(
            type="user",
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            scopes=frozenset({"workflow:update"}),
        ),
    )
    workflow = MagicMock()
    workflow.id = uuid.uuid4()

    async def add(action_type: str, control_flow: dict | None):
        added.clear()
        await service._add_node(
            workflow,
            AddNodePayload(
                type=action_type,
                title="t",
                control_flow=control_flow,
                position_x=0.0,
                position_y=0.0,
            ),
        )
        return added[0].control_flow

    # Fresh agent node drop (no control_flow) -> default persisted.
    assert (await add("ai.agent", None))["retry_policy"]["timeout"] == (
        AGENT_TIMEOUT_SECONDS_DEFAULT
    )
    # Explicit in-range timeout survives.
    cf = await add("ai.action", {"retry_policy": {"timeout": 2000}})
    assert cf["retry_policy"]["timeout"] == 2000
    # Explicit out-of-bounds values clamp at write (stored == executed).
    cf = await add("ai.agent", {"retry_policy": {"timeout": 60}})
    assert cf["retry_policy"]["timeout"] == AGENT_TIMEOUT_SECONDS_DEFAULT
    cf = await add("ai.agent", {"retry_policy": {"timeout": 100_000}})
    assert cf["retry_policy"]["timeout"] == TRACECAT__AGENT_SANDBOX_TIMEOUT
    # Malformed values normalize to the default.
    cf = await add("ai.agent", {"retry_policy": {"timeout": "abc"}})
    assert cf["retry_policy"]["timeout"] == AGENT_TIMEOUT_SECONDS_DEFAULT
    # Sibling retry_policy keys survive injection.
    cf = await add("ai.agent", {"retry_policy": {"max_attempts": 3}})
    assert cf["retry_policy"] == {
        "max_attempts": 3,
        "timeout": AGENT_TIMEOUT_SECONDS_DEFAULT,
    }
    # Non-agent nodes stay as sent.
    assert await add("core.http_request", None) == {}
