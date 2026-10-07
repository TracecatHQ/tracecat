"""Agent payloads from newer workers decode here with the same thinking choice.

A newer app version replaces ``enable_thinking`` with ``reasoning_effort`` in
the agent configs it writes to Temporal. While both versions share task queues,
this version must decode those payloads through the production converter:
``"off"`` disables thinking, and any other level, or null, enables it. An
explicit ``enable_thinking`` wins, and payloads in today's shape decode
unchanged. Constructing a config directly, as in ``AgentConfig(**payload)``,
must map the keys the same way.
"""

import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel
from tracecat_ee.agent.schemas import AgentActionArgs

from tracecat.agent.backends.schemas import AgentWorkflowArgs
from tracecat.agent.common.types import SandboxAgentConfig, SandboxSubagentConfig
from tracecat.agent.executor.activity import AgentExecutorInput
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import ResolvedAttachedSubagentRef
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl._converter import PydanticORJSONPayloadConverter

# (keys written into each serialized agent config, expected enable_thinking)
CONFIG_CASES: list[tuple[dict[str, Any], bool]] = [
    ({"reasoning_effort": None}, True),
    ({"reasoning_effort": "off"}, False),
    ({"reasoning_effort": "high"}, True),
    ({"enable_thinking": True, "reasoning_effort": "off"}, True),
    ({"enable_thinking": False}, False),
]
CONFIG_CASE_IDS = ["default", "off", "high", "explicit-flag-wins", "current-shape"]


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )


def _agent_config() -> AgentConfig:
    return AgentConfig(model_name="gpt-5-mini", model_provider="openai")


def _workflow_args() -> AgentWorkflowArgs:
    return AgentWorkflowArgs(
        role=_role(),
        agent_args=RunAgentArgs(
            user_prompt="Investigate the alert",
            session_id=uuid.uuid4(),
            config=_agent_config(),
        ),
        entity_type=AgentSessionEntity.CASE,
        entity_id=uuid.uuid4(),
    )


def _executor_input() -> AgentExecutorInput:
    config = _agent_config()
    return AgentExecutorInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_prompt="Investigate the alert",
        config=config,
        role=_role(),
        mcp_auth_token="synthetic-mcp-token",
        llm_gateway_auth_token="synthetic-llm-token",
        subagents=[
            SandboxSubagentConfig(
                alias="analyst",
                description="Use for enrichment analysis.",
                prompt="Analyze enrichment data.",
                config=SandboxAgentConfig.from_agent_config(config),
                mcp_auth_token="synthetic-subagent-token",
            )
        ],
    )


def _resolved_subagents() -> ResolvedAgentsRuntimeConfig:
    return ResolvedAgentsRuntimeConfig(
        subagents=[
            ResolvedSubagentConfig(
                binding=ResolvedAttachedSubagentRef(
                    preset="analyst",
                    preset_id=uuid.uuid4(),
                    preset_version_id=uuid.uuid4(),
                ),
                description="Use for enrichment analysis.",
                prompt="Analyze enrichment data.",
                config=agent_config_to_payload(_agent_config()),
            )
        ]
    )


def _action_args() -> AgentActionArgs:
    return AgentActionArgs(
        user_prompt="Investigate the alert",
        model_name="gpt-5-mini",
        model_provider="openai",
    )


type ConfigPaths = Callable[[dict[str, Any]], list[dict[str, Any]]]
type ThinkingChoices = Callable[[Any], list[bool]]

# (payload built by today's writer, its serialized configs, decoded thinking choices)
PAYLOAD_CASES: list[tuple[Callable[[], BaseModel], ConfigPaths, ThinkingChoices]] = [
    # Durable agent workflow input.
    (
        _workflow_args,
        lambda data: [data["agent_args"]["config"]],
        lambda args: [args.agent_args.config.enable_thinking],
    ),
    # run_agent_activity input: root config and each subagent's sandbox config.
    (
        _executor_input,
        lambda data: [data["config"], data["subagents"][0]["config"]],
        lambda args: [
            args.config.enable_thinking,
            args.subagents[0].config.enable_thinking,
        ],
    ),
    # resolve_agent_preset_config_activity result.
    (
        lambda: agent_config_to_payload(_agent_config()),
        lambda data: [data],
        lambda payload: [payload.enable_thinking],
    ),
    # resolve_agents_config_activity result.
    (
        _resolved_subagents,
        lambda data: [data["subagents"][0]["config"]],
        lambda result: [result.subagents[0].config.enable_thinking],
    ),
    # build_agent_args_activity result.
    (
        _action_args,
        lambda data: [data],
        lambda args: [args.enable_thinking],
    ),
]


@pytest.mark.parametrize(
    ("build", "config_paths", "thinking_choices"),
    PAYLOAD_CASES,
    ids=[
        "workflow-args",
        "executor-input",
        "preset-config",
        "resolved-subagents",
        "action-args",
    ],
)
@pytest.mark.parametrize(("config_keys", "expected"), CONFIG_CASES, ids=CONFIG_CASE_IDS)
def test_payload_keeps_thinking_choice(
    build: Callable[[], BaseModel],
    config_paths: ConfigPaths,
    thinking_choices: ThinkingChoices,
    config_keys: dict[str, Any],
    expected: bool,
) -> None:
    obj = build()
    converter = PydanticORJSONPayloadConverter()
    payload = converter.to_payload(obj)
    assert payload is not None
    data = json.loads(payload.data)
    for config in config_paths(data):
        # The converter omits unset fields, so action args may lack the flag.
        config.pop("enable_thinking", None)
        config.update(config_keys)
    payload.data = json.dumps(data).encode()

    decoded = converter.from_payload(payload, type(obj))

    assert thinking_choices(decoded) == [expected] * len(config_paths(data))


# (config type, its required constructor arguments)
CONSTRUCTOR_CASES: list[tuple[Callable[..., Any], dict[str, Any]]] = [
    (AgentConfig, {"model_name": "gpt-5-mini", "model_provider": "openai"}),
    (SandboxAgentConfig, {"model_name": "gpt-5-mini", "model_provider": "openai"}),
    (
        AgentConfigPayload,
        {"model_name": "gpt-5-mini", "model_provider": "openai", "retries": 3},
    ),
    (
        AgentActionArgs,
        {
            "user_prompt": "Investigate the alert",
            "model_name": "gpt-5-mini",
            "model_provider": "openai",
        },
    ),
]


@pytest.mark.parametrize(
    ("config_type", "required"),
    CONSTRUCTOR_CASES,
    ids=["agent-config", "sandbox-config", "config-payload", "action-args"],
)
@pytest.mark.parametrize(("config_keys", "expected"), CONFIG_CASES, ids=CONFIG_CASE_IDS)
def test_constructor_keeps_thinking_choice(
    config_type: Callable[..., Any],
    required: dict[str, Any],
    config_keys: dict[str, Any],
    expected: bool,
) -> None:
    """Pydantic dataclass constructors validate ``ArgsKwargs``, not a dict."""
    config = config_type(**required, **config_keys)

    assert config.enable_thinking is expected
