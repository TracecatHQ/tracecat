"""Generic action-timeout wiring for agent-backed actions."""

from tracecat.agent.constants import (
    AGENT_TIMEOUT_SECONDS_DEFAULT,
    AGENT_TIMEOUT_SECONDS_MAX,
    AGENT_TIMEOUT_SECONDS_MIN,
)
from tracecat.dsl.constants import DEFAULT_ACTION_TIMEOUT
from tracecat.dsl.schemas import ActionRetryPolicy, ActionStatement
from tracecat.dsl.workflow import _agent_action_timeout


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
    assert _agent_action_timeout(task) == 900


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
    assert (
        _agent_action_timeout(_task("ai.agent", ActionRetryPolicy(timeout=1)))
        == AGENT_TIMEOUT_SECONDS_MIN
    )
    assert (
        _agent_action_timeout(_task("ai.agent", ActionRetryPolicy(timeout=100_000)))
        == AGENT_TIMEOUT_SECONDS_MAX
    )
