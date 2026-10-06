"""Temporal payloads stored before reasoning levels decode with the same meaning.

Workflow history and in-flight activity inputs written by an older worker carry
``enable_thinking`` instead of ``reasoning_effort``. The production converter
must map ``false`` to ``"off"`` and ``true`` to the model default for both the
root ``AgentConfig`` and each subagent's ``SandboxAgentConfig``, and an
explicit ``reasoning_effort`` key, even null, must win over the legacy flag.
"""

import json
import uuid
from typing import Any

import pytest
from temporalio.api.common.v1 import Payload

from tracecat.agent.backends.schemas import AgentWorkflowArgs
from tracecat.agent.common.types import SandboxAgentConfig, SandboxSubagentConfig
from tracecat.agent.executor.activity import AgentExecutorInput
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl._converter import PydanticORJSONPayloadConverter

# (legacy keys written into each stored config, expected reasoning_effort)
LEGACY_CASES: list[tuple[dict[str, Any], str | None]] = [
    ({"enable_thinking": False}, "off"),
    ({"enable_thinking": True}, None),
    ({"enable_thinking": False, "reasoning_effort": None}, None),
    ({"enable_thinking": False, "reasoning_effort": "high"}, "high"),
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


def _as_legacy_config(config: dict[str, Any], legacy: dict[str, Any]) -> None:
    """Rewrite a serialized config into the pre-reasoning-levels shape."""
    del config["reasoning_effort"]
    config.update(legacy)


def _decode[T](payload: Payload, data: dict[str, Any], type_hint: type[T]) -> T:
    payload.data = json.dumps(data).encode()
    return PydanticORJSONPayloadConverter().from_payload(payload, type_hint)


@pytest.mark.parametrize(("legacy", "expected"), LEGACY_CASES)
def test_stored_workflow_args_keep_legacy_thinking_choice(
    legacy: dict[str, Any], expected: str | None
) -> None:
    converter = PydanticORJSONPayloadConverter()
    payload = converter.to_payload(
        AgentWorkflowArgs(
            role=_role(),
            agent_args=RunAgentArgs(
                user_prompt="Investigate the alert",
                session_id=uuid.uuid4(),
                config=_agent_config(),
            ),
            entity_type=AgentSessionEntity.CASE,
            entity_id=uuid.uuid4(),
        )
    )
    assert payload is not None
    data = json.loads(payload.data)
    _as_legacy_config(data["agent_args"]["config"], legacy)

    decoded = _decode(payload, data, AgentWorkflowArgs)

    assert decoded.agent_args.config is not None
    assert decoded.agent_args.config.reasoning_effort == expected


@pytest.mark.parametrize(("legacy", "expected"), LEGACY_CASES)
def test_stored_executor_activity_input_keeps_legacy_thinking_choice(
    legacy: dict[str, Any], expected: str | None
) -> None:
    """Covers activities an older worker scheduled and a newer worker runs."""
    config = _agent_config()
    converter = PydanticORJSONPayloadConverter()
    payload = converter.to_payload(
        AgentExecutorInput(
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
    )
    assert payload is not None
    data = json.loads(payload.data)
    _as_legacy_config(data["config"], legacy)
    _as_legacy_config(data["subagents"][0]["config"], legacy)

    decoded = _decode(payload, data, AgentExecutorInput)

    assert decoded.config.reasoning_effort == expected
    assert decoded.subagents[0].config.reasoning_effort == expected
