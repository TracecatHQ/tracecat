from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.schemas import RunActionInput, RunContext
from tracecat.identifiers.workflow import WorkflowUUID


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def test_dsl_run_args_defaults_outbound_http_interception_disabled() -> None:
    role = _role()
    args = DSLRunArgs.model_validate(
        {
            "role": role,
            "wf_id": str(WorkflowUUID.new_uuid4()),
        }
    )

    assert args.outbound_http_interception_enabled is False


def test_run_context_defaults_optional_execution_metadata() -> None:
    wf_id = WorkflowUUID.new_uuid4()
    run_context = RunContext.model_validate(
        {
            "wf_id": str(wf_id),
            "wf_exec_id": f"{wf_id.short()}/exec-test",
            "wf_run_id": str(uuid.uuid4()),
            "environment": "default",
            "logical_time": datetime.now(UTC).isoformat(),
        }
    )

    assert run_context.trigger_type is None
    assert run_context.execution_type is None


def test_run_action_input_defaults_outbound_http_interception_and_entity_metadata() -> (
    None
):
    wf_id = WorkflowUUID.new_uuid4()
    action_input = RunActionInput.model_validate(
        {
            "task": {
                "action": "core.http_request",
                "args": {"url": "https://example.com"},
                "ref": "call_api",
            },
            "exec_context": {"ACTIONS": {}, "TRIGGER": None},
            "run_context": {
                "wf_id": str(wf_id),
                "wf_exec_id": f"{wf_id.short()}/exec-test",
                "wf_run_id": str(uuid.uuid4()),
                "environment": "default",
                "logical_time": datetime.now(UTC).isoformat(),
            },
            "registry_lock": {
                "origins": {"tracecat_registry": "test"},
                "actions": {"core.http_request": "tracecat_registry"},
            },
        }
    )

    assert action_input.outbound_http_interception_enabled is False
    assert action_input.entity_type is None
    assert action_input.entity_id is None


def test_agent_workflow_args_defaults_outbound_http_interception_disabled() -> None:
    role = _role()
    args = AgentWorkflowArgs.model_validate(
        {
            "role": role,
            "agent_args": RunAgentArgs(
                user_prompt="hello",
                session_id=uuid.uuid4(),
                config=AgentConfig(
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                ),
            ),
            "entity_type": "copilot",
            "entity_id": str(uuid.uuid4()),
        }
    )

    assert args.outbound_http_interception_enabled is False


def test_mcp_token_claims_defaults_outbound_http_interception_and_entity_metadata() -> (
    None
):
    claims = MCPTokenClaims.model_validate(
        {
            "workspace_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "allowed_actions": ["core.http_request"],
        }
    )

    assert claims.outbound_http_interception_enabled is False
    assert claims.entity_type is None
    assert claims.entity_id is None
