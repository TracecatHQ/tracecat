"""Agent payloads from either app version decode with the same reasoning choice.

During a rollout or rollback, workers on the previous version write
``enable_thinking`` where this version writes ``reasoning_effort``. Every agent
config that crosses a Temporal boundary must decode both shapes through the
production converter: legacy ``false`` maps to ``"off"`` and ``true`` to the
model default, and an explicit ``reasoning_effort`` key, even null, wins.
Constructing a config directly, as in ``AgentConfig(**payload)``, must migrate
the same way.
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
from tracecat.agent.internal_router import build_agent_workflow_args
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.schemas import (
    AgentConfigSchema,
    InternalRunAgentRequest,
    RunAgentArgs,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import ResolvedAttachedSubagentRef
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl._converter import PydanticORJSONPayloadConverter

# (keys written into each serialized agent config, expected reasoning_effort)
CONFIG_CASES: list[tuple[dict[str, Any], str | None]] = [
    ({"enable_thinking": False}, "off"),
    ({"enable_thinking": True}, None),
    ({"enable_thinking": False, "reasoning_effort": None}, None),
    ({"enable_thinking": False, "reasoning_effort": "high"}, "high"),
    ({"reasoning_effort": "off"}, "off"),
    ({"reasoning_effort": "high"}, "high"),
]
CONFIG_CASE_IDS = [
    "legacy-off",
    "legacy-on",
    "explicit-null-wins",
    "explicit-level-wins",
    "current-off",
    "current-high",
]


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
type ReasoningChoices = Callable[[Any], list[str | None]]

# (payload built by this version's writer, its serialized configs, decoded levels)
PAYLOAD_CASES: list[tuple[Callable[[], BaseModel], ConfigPaths, ReasoningChoices]] = [
    # Durable agent workflow input.
    (
        _workflow_args,
        lambda data: [data["agent_args"]["config"]],
        lambda args: [args.agent_args.config.reasoning_effort],
    ),
    # run_agent_activity input: root config and each subagent's sandbox config.
    (
        _executor_input,
        lambda data: [data["config"], data["subagents"][0]["config"]],
        lambda args: [
            args.config.reasoning_effort,
            args.subagents[0].config.reasoning_effort,
        ],
    ),
    # resolve_agent_preset_config_activity result.
    (
        lambda: agent_config_to_payload(_agent_config()),
        lambda data: [data],
        lambda payload: [payload.reasoning_effort],
    ),
    # resolve_agents_config_activity result.
    (
        _resolved_subagents,
        lambda data: [data["subagents"][0]["config"]],
        lambda result: [result.subagents[0].config.reasoning_effort],
    ),
    # build_agent_args_activity result.
    (
        _action_args,
        lambda data: [data],
        lambda args: [args.reasoning_effort],
    ),
]


@pytest.mark.parametrize(
    ("build", "config_paths", "reasoning_choices"),
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
def test_payload_keeps_reasoning_choice(
    build: Callable[[], BaseModel],
    config_paths: ConfigPaths,
    reasoning_choices: ReasoningChoices,
    config_keys: dict[str, Any],
    expected: str | None,
) -> None:
    obj = build()
    converter = PydanticORJSONPayloadConverter()
    payload = converter.to_payload(obj)
    assert payload is not None
    data = json.loads(payload.data)
    for config in config_paths(data):
        # The converter omits unset fields, so action args may lack the key.
        config.pop("reasoning_effort", None)
        config.update(config_keys)
    payload.data = json.dumps(data).encode()

    decoded = converter.from_payload(payload, type(obj))

    assert reasoning_choices(decoded) == [expected] * len(config_paths(data))


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
def test_constructor_keeps_reasoning_choice(
    config_type: Callable[..., Any],
    required: dict[str, Any],
    config_keys: dict[str, Any],
    expected: str | None,
) -> None:
    """Pydantic dataclass constructors validate ``ArgsKwargs``, not a dict."""
    config = config_type(**required, **config_keys)

    assert config.reasoning_effort == expected


@pytest.mark.parametrize(
    ("enable_thinking", "expected"), [(False, "off"), (True, None)]
)
def test_internal_agent_route_keeps_legacy_thinking_choice(
    enable_thinking: bool, expected: str | None
) -> None:
    """The retired internal agent route's pinned request schema still carries
    ``enable_thinking`` and builds ``AgentConfig`` with keyword arguments."""
    params = InternalRunAgentRequest(
        user_prompt="Investigate the alert",
        config=AgentConfigSchema(
            model_name="gpt-5-mini",
            model_provider="openai",
            enable_thinking=enable_thinking,
        ),
    )

    workflow_args = build_agent_workflow_args(
        params, role=_role(), session_id=uuid.uuid4()
    )

    assert workflow_args.agent_args.config is not None
    assert workflow_args.agent_args.config.reasoning_effort == expected
