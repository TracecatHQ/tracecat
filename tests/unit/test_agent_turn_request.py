"""Tests for the additive durable-agent turn request contract."""

from __future__ import annotations

import uuid

from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import (
    AgentConfigPayload,
    AgentTurnRequest,
    InlineAgentSource,
    PresetAgentSource,
)
from tracecat.auth.types import Role


def _role() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute"}),
    )


def _legacy_workflow_args(role: Role) -> AgentWorkflowArgs:
    return AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            user_prompt="Investigate this alert",
            session_id=uuid.uuid4(),
            config=AgentConfig(
                model_name="test-model",
                model_provider="test-provider",
            ),
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )


def test_legacy_workflow_args_decode_without_request() -> None:
    """Workflow inputs recorded before the expansion retain the legacy shape."""
    legacy_args = _legacy_workflow_args(_role())
    legacy_payload = legacy_args.model_dump(mode="python", exclude={"request"})

    decoded = AgentWorkflowArgs.model_validate(legacy_payload)

    assert decoded.request is None
    assert decoded.agent_args == legacy_args.agent_args


def test_workflow_args_round_trip_with_turn_request() -> None:
    """The expanded envelope preserves and discriminates the new request."""
    role = _role()
    legacy_args = _legacy_workflow_args(role)
    request = AgentTurnRequest(
        role=role,
        session_id=legacy_args.agent_args.session_id,
        active_stream_id=uuid.uuid4(),
        user_prompt=legacy_args.agent_args.user_prompt,
        source=PresetAgentSource(
            slug="investigation-agent",
            version=3,
            preset_id=uuid.uuid4(),
            preset_version_id=uuid.uuid4(),
            actions=["core.http_request"],
            instructions="Focus on the highest-confidence evidence.",
        ),
        title="Alert investigation",
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=legacy_args.entity_id,
        tools=["core.http_request"],
        harness_type=HarnessType.CLAUDE_CODE,
        max_requests=10,
        max_tool_calls=20,
    )
    expanded_args = legacy_args.model_copy(update={"request": request})

    decoded = AgentWorkflowArgs.model_validate_json(expanded_args.model_dump_json())
    decoded_request = decoded.request

    assert decoded_request is not None
    assert decoded_request == request
    assert isinstance(decoded_request.source, PresetAgentSource)
    assert decoded_request.source.slug == "investigation-agent"
    assert decoded.agent_args == legacy_args.agent_args


def test_inline_agent_source_round_trip() -> None:
    """Inline requests retain their workflow-safe configuration payload."""
    request = AgentTurnRequest(
        role=_role(),
        session_id=uuid.uuid4(),
        user_prompt="Summarize the evidence",
        source=InlineAgentSource(
            config=AgentConfigPayload(
                model_name="test-model",
                model_provider="test-provider",
                retries=3,
            )
        ),
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=uuid.uuid4(),
    )

    decoded = AgentTurnRequest.model_validate_json(request.model_dump_json())

    assert isinstance(decoded.source, InlineAgentSource)
    assert decoded.source.config.model_name == "test-model"
