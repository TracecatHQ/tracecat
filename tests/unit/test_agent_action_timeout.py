"""Generic action-timeout wiring for agent-backed actions."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tracecat.agent.constants import (
    AGENT_TIMEOUT_SECONDS_DEFAULT,
    AGENT_TIMEOUT_SECONDS_MAX,
    AGENT_TIMEOUT_SECONDS_MIN,
)
from tracecat.auth.types import Role
from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.schemas import (
    STRICT_TIMEOUTS_CONTEXT,
    ActionRetryPolicy,
    ActionStatement,
)
from tracecat.identifiers import WorkflowUUID
from tracecat.workflow.actions.schemas import ActionControlFlow, ActionCreate
from tracecat.workflow.actions.service import WorkflowActionService
from tracecat.workflow.management.schemas import ExternalWorkflowDefinition


def _task(
    action: str, retry_policy: ActionRetryPolicy | None = None
) -> ActionStatement:
    if retry_policy is None:
        return ActionStatement(ref="task", action=action, args={})
    return ActionStatement(
        ref="task", action=action, args={}, retry_policy=retry_policy
    )


def test_regular_action_keeps_default_timeout() -> None:
    assert _task("core.http_request").retry_policy.timeout == DEFAULT_ACTION_TIMEOUT


def test_agent_actions_default_to_thirty_minutes() -> None:
    for action in ("ai.agent", "ai.action", "ai.preset_agent"):
        assert _task(action).retry_policy.timeout == AGENT_TIMEOUT_SECONDS_DEFAULT


def test_agent_action_explicit_timeout_survives() -> None:
    task = _task("ai.agent", ActionRetryPolicy(timeout=900))
    assert task.retry_policy.timeout == 900


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


def test_agent_timeout_clamped_to_bounds() -> None:
    """Legacy stored values outside the sandbox budget are normalized at
    parse time so the statement value is exactly what executes."""
    low = _task("ai.agent", ActionRetryPolicy(timeout=1))
    high = _task("ai.agent", ActionRetryPolicy(timeout=100_000))
    assert low.retry_policy.timeout == AGENT_TIMEOUT_SECONDS_MIN
    assert high.retry_policy.timeout == AGENT_TIMEOUT_SECONDS_MAX


def test_action_read_response_returns_control_flow_as_stored() -> None:
    """The action API must not inflate retry_policy defaults: the builder
    round-trips whatever it receives, turning invented defaults into
    explicit author choices."""
    import uuid

    from tracecat.workflow.actions.router import router
    from tracecat.workflow.actions.schemas import ActionControlFlow, ActionRead

    read = ActionRead(
        id=uuid.uuid4(),
        type="ai.agent",
        title="Agent",
        description="",
        status="online",
        inputs="",
        control_flow=ActionControlFlow.model_validate(
            {"retry_policy": {"max_attempts": 2}}
        ),
        is_interactive=False,
    )
    payload = read.model_dump(mode="json", exclude_unset=True)
    assert payload["control_flow"] == {"retry_policy": {"max_attempts": 2}}

    # Both ActionRead routes must serialize with exclude_unset.
    from fastapi.routing import APIRoute

    flagged = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path.endswith("/{action_id}")
        and (route.methods or set()) & {"GET", "POST"}
    ]
    assert len(flagged) == 2
    assert all(route.response_model_exclude_unset for route in flagged)


@pytest.mark.anyio
async def test_action_create_preserves_omitted_timeout() -> None:
    """Creating from a sparse read must not persist the generic 300s default."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    workspace_id = uuid.uuid4()
    service = WorkflowActionService(
        session,
        role=Role(
            type="user",
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            workspace_id=workspace_id,
            scopes=frozenset({"workflow:create"}),
        ),
    )

    action = await service.create_action(
        ActionCreate(
            workflow_id=WorkflowUUID.new(uuid.uuid4()),
            type="ai.agent",
            title="Agent",
            control_flow=ActionControlFlow.model_validate(
                {"retry_policy": {"max_attempts": 2}}
            ),
        )
    )

    assert action.control_flow == {"retry_policy": {"max_attempts": 2}}


def test_action_write_api_rejects_out_of_bounds_agent_timeout() -> None:
    from fastapi import HTTPException

    from tracecat.workflow.actions.router import _validate_agent_timeout_bounds
    from tracecat.workflow.actions.schemas import ActionControlFlow

    out_of_bounds = ActionControlFlow.model_validate(
        {"retry_policy": {"timeout": 100_000}}
    )
    with pytest.raises(HTTPException) as exc_info:
        _validate_agent_timeout_bounds("ai.agent", out_of_bounds)
    assert exc_info.value.status_code == 422

    # Non-agent actions and unset agent timeouts are not bounded here.
    _validate_agent_timeout_bounds("core.http_request", out_of_bounds)
    _validate_agent_timeout_bounds(
        "ai.agent",
        ActionControlFlow.model_validate({"retry_policy": {"max_attempts": 3}}),
    )
    _validate_agent_timeout_bounds("ai.agent", None)


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


@pytest.mark.parametrize("timeout", [1, 100_000])
def test_strict_context_rejects_out_of_bounds_agent_timeout(timeout: int) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ActionStatement.model_validate(
            _agent_action(timeout), context=dict(STRICT_TIMEOUTS_CONTEXT)
        )
    assert (
        f"between {AGENT_TIMEOUT_SECONDS_MIN} and {AGENT_TIMEOUT_SECONDS_MAX}"
        in str(exc_info.value)
    )


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [(1, AGENT_TIMEOUT_SECONDS_MIN), (100_000, AGENT_TIMEOUT_SECONDS_MAX)],
)
def test_lenient_parse_still_normalizes_stored_values(
    timeout: int, expected: int
) -> None:
    stmt = ActionStatement.model_validate(_agent_action(timeout))
    assert stmt.retry_policy.timeout == expected


@pytest.mark.parametrize("context", [None, dict(STRICT_TIMEOUTS_CONTEXT)])
def test_non_agent_actions_are_unaffected_in_both_modes(
    context: dict[str, object] | None,
) -> None:
    stmt = ActionStatement.model_validate(
        {
            "ref": "http",
            "action": "core.http_request",
            "args": {},
            "retry_policy": {"timeout": 100_000},
        },
        context=context,
    )
    assert stmt.retry_policy.timeout == 100_000


def test_strict_context_still_applies_the_default_when_timeout_is_unset() -> None:
    stmt = ActionStatement.model_validate(
        {"ref": "agent", "action": "ai.agent", "args": {}},
        context=dict(STRICT_TIMEOUTS_CONTEXT),
    )
    assert stmt.retry_policy.timeout == AGENT_TIMEOUT_SECONDS_DEFAULT


@pytest.mark.parametrize("timeout", [1, 100_000])
def test_external_workflow_definition_upload_rejects_out_of_bounds(
    timeout: int,
) -> None:
    """YAML/JSON upload, MCP create, and internal create all funnel here."""
    payload = {"definition": _dsl_payload(_agent_action(timeout))}
    with pytest.raises(ValidationError):
        ExternalWorkflowDefinition.model_validate(
            payload, context=dict(STRICT_TIMEOUTS_CONTEXT)
        )
    lenient = ExternalWorkflowDefinition.model_validate(payload)
    assert lenient.definition.actions[0].retry_policy.timeout in (
        AGENT_TIMEOUT_SECONDS_MIN,
        AGENT_TIMEOUT_SECONDS_MAX,
    )


def test_mcp_workflow_yaml_payload_rejects_out_of_bounds() -> None:
    """MCP update_workflow inline definition_yaml boundary."""
    from tracecat.mcp.schemas import WorkflowYamlPayload

    payload = {"definition": _dsl_payload(_agent_action(100_000))}
    with pytest.raises(ValidationError):
        WorkflowYamlPayload.model_validate(
            payload, context=dict(STRICT_TIMEOUTS_CONTEXT)
        )
    lenient = WorkflowYamlPayload.model_validate(payload)
    assert lenient.definition is not None
    assert lenient.definition.actions[0].retry_policy.timeout == (
        AGENT_TIMEOUT_SECONDS_MAX
    )


def test_workflow_edit_document_patch_rejects_out_of_bounds() -> None:
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
    with pytest.raises(ValidationError):
        WorkflowEditDocument.model_validate(
            payload, context=dict(STRICT_TIMEOUTS_CONTEXT)
        )
    assert (
        WorkflowEditDocument.model_validate(payload)
        .definition.actions[0]
        .retry_policy.timeout
        == AGENT_TIMEOUT_SECONDS_MAX
    )


def test_git_sync_parse_reports_out_of_bounds_as_a_diagnostic() -> None:
    """Git-sync import must return a clean PullDiagnostic, never a 500."""
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
    assert spec is None
    assert diagnostic is not None
    assert diagnostic.error_type == "validation"
    assert (
        f"between {AGENT_TIMEOUT_SECONDS_MIN} and {AGENT_TIMEOUT_SECONDS_MAX}"
        in diagnostic.message
    )


def test_executor_result_active_seconds_defaults_for_legacy_results() -> None:
    """Legacy activity results lack active_seconds; 0.0 keeps full budgets on
    replay so old histories never hit the exceeded-runtime error."""
    from tracecat.agent.executor.activity import AgentExecutorResult

    legacy = AgentExecutorResult.model_validate({"success": True})
    assert legacy.active_seconds == 0.0
