"""Generic action-timeout wiring for agent-backed actions."""

from tracecat.agent.constants import (
    AGENT_TIMEOUT_SECONDS_DEFAULT,
    AGENT_TIMEOUT_SECONDS_MAX,
    AGENT_TIMEOUT_SECONDS_MIN,
)
from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement


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


def test_action_write_api_rejects_out_of_bounds_agent_timeout() -> None:
    import pytest
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


def test_executor_result_active_seconds_defaults_for_legacy_results() -> None:
    """Legacy activity results lack active_seconds; 0.0 keeps full budgets on
    replay so old histories never hit the exceeded-runtime error."""
    from tracecat.agent.executor.activity import AgentExecutorResult

    legacy = AgentExecutorResult.model_validate({"success": True})
    assert legacy.active_seconds == 0.0
