"""Generic action-timeout wiring for agent-backed actions.

Agent timeouts clamp to [deployment default, deployment cap] at parse time.
No boundary rejects: out-of-bounds values normalize wherever they are seen.
"""

import pytest

from tracecat.agent import types as agent_types
from tracecat.agent.constants import (
    AGENT_TIMEOUT_SECONDS_DEFAULT,
    AGENT_TIMEOUT_SECONDS_MAX,
)
from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement
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


def test_agent_actions_default_to_deployment_default() -> None:
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
        (100_000, AGENT_TIMEOUT_SECONDS_MAX),
    ],
)
def test_agent_timeout_clamps_to_deployment_bounds(timeout: int, expected: int) -> None:
    """Out-of-bounds values normalize at parse time so the statement value is
    exactly what executes. There is no minimum below the deployment default."""
    stmt = ActionStatement.model_validate(_agent_action(timeout))
    assert stmt.retry_policy.timeout == expected


def test_agent_timeout_floor_follows_deployment_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 900)

    low = ActionStatement.model_validate(_agent_action(60))
    in_range = ActionStatement.model_validate(_agent_action(1200))
    assert low.retry_policy.timeout == 900
    assert in_range.retry_policy.timeout == 1200


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
    [(1, AGENT_TIMEOUT_SECONDS_DEFAULT), (100_000, AGENT_TIMEOUT_SECONDS_MAX)],
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
        AGENT_TIMEOUT_SECONDS_MAX
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
        == AGENT_TIMEOUT_SECONDS_MAX
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
        AGENT_TIMEOUT_SECONDS_MAX
    )
