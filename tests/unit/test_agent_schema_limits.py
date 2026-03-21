import uuid

import pytest
from pydantic import ValidationError
from tracecat_ee.agent.schemas import PresetAgentActionArgs

from tracecat import config
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.types import AgentConfig


def _agent_config() -> AgentConfig:
    return AgentConfig(
        model_name="gpt-5-mini",
        model_provider="openai",
    )


def test_preset_agent_action_args_rejects_limits_above_config() -> None:
    with pytest.raises(ValidationError):
        PresetAgentActionArgs(
            preset="triage",
            user_prompt="Analyze this alert",
            max_tool_calls=config.TRACECAT__AGENT_MAX_TOOL_CALLS + 1,
            max_requests=config.TRACECAT__AGENT_MAX_REQUESTS + 1,
        )


def test_run_agent_args_rejects_limits_above_config() -> None:
    with pytest.raises(ValidationError):
        RunAgentArgs(
            user_prompt="Analyze this alert",
            session_id=uuid.uuid4(),
            config=_agent_config(),
            max_tool_calls=config.TRACECAT__AGENT_MAX_TOOL_CALLS + 1,
            max_requests=config.TRACECAT__AGENT_MAX_REQUESTS + 1,
        )


def test_run_agent_args_allows_zero_tool_calls() -> None:
    args = RunAgentArgs(
        user_prompt="Analyze this alert",
        session_id=uuid.uuid4(),
        config=_agent_config(),
        max_tool_calls=0,
        max_requests=3,
    )

    assert args.max_tool_calls == 0
    assert args.max_requests == 3
