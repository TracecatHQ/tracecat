"""Unit tests for DurableAgentWorkflow search attribute upsert behavior."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.common import TypedSearchAttributes
from temporalio.exceptions import ApplicationError
from tracecat_ee.agent.workflows.durable import (
    UPSERT_TRACECAT_SEARCH_ATTRIBUTES_PATCH,
    AgentWorkflowArgs,
    DurableAgentWorkflow,
)

from tracecat.agent.executor.activity import AgentExecutorResult
from tracecat.agent.preset.activities import ResolveAgentPresetConfigActivityInput
from tracecat.agent.schemas import AgentOutput, RunAgentArgs
from tracecat.agent.session.activities import CreateSessionResult, LoadSessionResult
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import RunAgentActivityFailureMode
from tracecat.auth.types import Role
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.registry.lock.types import RegistryLock
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)


def _build_workflow_args(role: Role) -> AgentWorkflowArgs:
    agent_config_ctor = cast(Any, AgentConfig)
    return AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            session_id=uuid.uuid4(),
            user_prompt="hello",
            config=agent_config_ctor(
                model_name="gpt-4o-mini",
                model_provider="openai",
                actions=["core.http_request"],
            ),
        ),
        entity_type=AgentSessionEntity.COPILOT,
        entity_id=uuid.uuid4(),
    )


def _update_map(updates: list[Any]) -> dict[str, str]:
    return {update.key.name: update.value for update in updates}


@pytest.mark.anyio
async def test_upsert_tracecat_search_attributes_fills_missing_keys() -> None:
    role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "secret:read"}),
    )
    workflow_args = _build_workflow_args(role)
    workflow_instance = DurableAgentWorkflow(workflow_args)
    existing_attrs = TypedSearchAttributes(search_attributes=[])

    with (
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.info",
            return_value=SimpleNamespace(typed_search_attributes=existing_attrs),
        ),
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.upsert_search_attributes"
        ) as upsert_mock,
    ):
        workflow_instance._upsert_tracecat_search_attributes()

    upsert_mock.assert_called_once()
    updates = upsert_mock.call_args.args[0]
    values = _update_map(updates)
    assert values[TemporalSearchAttr.TRIGGER_TYPE.value] == TriggerType.MANUAL.value
    assert (
        values[TemporalSearchAttr.EXECUTION_TYPE.value] == ExecutionType.PUBLISHED.value
    )
    assert values[TemporalSearchAttr.WORKSPACE_ID.value] == str(role.workspace_id)
    assert values[TemporalSearchAttr.TRIGGERED_BY_USER_ID.value] == str(role.user_id)


@pytest.mark.anyio
async def test_upsert_tracecat_search_attributes_preserves_existing_values() -> None:
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=None,
    )
    workflow_args = _build_workflow_args(role)
    workflow_instance = DurableAgentWorkflow(workflow_args)
    existing_workspace = str(uuid.uuid4())
    existing_user = str(uuid.uuid4())
    existing_attrs = TypedSearchAttributes(
        search_attributes=[
            TemporalSearchAttr.TRIGGER_TYPE.create_pair(TriggerType.SCHEDULED.value),
            TemporalSearchAttr.EXECUTION_TYPE.create_pair(ExecutionType.DRAFT.value),
            TemporalSearchAttr.WORKSPACE_ID.create_pair(existing_workspace),
            TemporalSearchAttr.TRIGGERED_BY_USER_ID.create_pair(existing_user),
        ]
    )

    with (
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.info",
            return_value=SimpleNamespace(typed_search_attributes=existing_attrs),
        ),
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.upsert_search_attributes"
        ) as upsert_mock,
    ):
        workflow_instance._upsert_tracecat_search_attributes()

    upsert_mock.assert_not_called()


@pytest.mark.anyio
async def test_run_skips_search_attribute_upsert_without_patch_marker() -> None:
    role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "secret:read"}),
    )
    workflow_args = _build_workflow_args(role)
    workflow_instance = DurableAgentWorkflow(workflow_args)
    cfg = cast(Any, workflow_args.agent_args.config)
    expected_output = AgentOutput(
        output="ok",
        duration=0.1,
        session_id=workflow_args.agent_args.session_id,
    )

    with (
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.patched",
            return_value=False,
        ) as patched_mock,
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.unsafe.is_replaying",
            return_value=False,
        ),
        patch.object(
            workflow_instance, "_upsert_tracecat_search_attributes"
        ) as upsert_mock,
        patch.object(workflow_instance, "_build_config", AsyncMock(return_value=cfg)),
        patch.object(
            workflow_instance,
            "_run_with_nsjail",
            AsyncMock(return_value=expected_output),
        ) as run_mock,
    ):
        result = await workflow_instance.run(workflow_args)

    patched_mock.assert_called_once_with(UPSERT_TRACECAT_SEARCH_ATTRIBUTES_PATCH)
    upsert_mock.assert_not_called()
    run_mock.assert_awaited_once_with(workflow_args, cfg)
    assert result == expected_output


@pytest.mark.anyio
async def test_build_config_prefers_pinned_preset_version_id() -> None:
    role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "secret:read"}),
    )
    pinned_version_id = uuid.uuid4()
    workflow_args = AgentWorkflowArgs(
        role=role,
        agent_args=RunAgentArgs(
            session_id=uuid.uuid4(),
            user_prompt="hello",
            preset_slug="triage-agent",
            preset_version=None,
            config=cast(
                Any,
                AgentConfig(
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                    actions=["core.http_request"],
                    instructions="append this",
                ),
            ),
        ),
        entity_type=AgentSessionEntity.COPILOT,
        entity_id=uuid.uuid4(),
        agent_preset_version_id=pinned_version_id,
    )
    workflow_instance = DurableAgentWorkflow(workflow_args)
    pinned_config = AgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
        instructions="base instructions",
        actions=["core.http_request"],
    )

    with patch(
        "tracecat_ee.agent.workflows.durable.workflow.execute_activity",
        AsyncMock(return_value=agent_config_to_payload(pinned_config)),
    ) as execute_activity_mock:
        cfg = await workflow_instance._build_config(workflow_args)

    execute_activity_mock.assert_awaited_once()
    assert execute_activity_mock.await_args is not None
    call_args = execute_activity_mock.await_args.args
    assert isinstance(call_args[1], ResolveAgentPresetConfigActivityInput)
    assert call_args[1].preset_version_id == pinned_version_id
    assert call_args[1].preset_slug is None
    assert call_args[1].preset_version is None
    assert cfg.model_name == pinned_config.model_name
    assert cfg.model_provider == pinned_config.model_provider
    assert cfg.actions == ["core.http_request"]
    assert cfg.instructions == "base instructions\nappend this"


@pytest.mark.anyio
async def test_run_with_nsjail_uses_retry_policy_for_retry_mode() -> None:
    role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "secret:read"}),
    )
    workflow_args = _build_workflow_args(role)
    workflow_args.run_agent_failure_mode = RunAgentActivityFailureMode.RETRY
    workflow_instance = DurableAgentWorkflow(workflow_args)
    cfg = cast(Any, workflow_args.agent_args.config)

    build_result = SimpleNamespace(
        tool_definitions={},
        registry_lock=RegistryLock(origins={}, actions={}),
        user_mcp_claims=None,
        allowed_internal_tools=None,
    )

    execute_activity_mock = AsyncMock(
        side_effect=[
            CreateSessionResult(
                session_id=workflow_args.agent_args.session_id,
                success=True,
            ),
            LoadSessionResult(
                found=False,
                sdk_session_id=None,
                sdk_session_data=None,
            ),
            AgentExecutorResult(success=False, error="sandbox failed"),
        ]
    )

    with (
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.info",
            return_value=SimpleNamespace(
                workflow_id=f"agent/{workflow_args.agent_args.session_id}"
            ),
        ),
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.execute_activity",
            execute_activity_mock,
        ),
        patch(
            "tracecat_ee.agent.workflows.durable.workflow.execute_activity_method",
            AsyncMock(return_value=build_result),
        ),
        patch(
            "tracecat_ee.agent.workflows.durable.mint_mcp_token",
            return_value="mcp-token",
        ),
        patch(
            "tracecat_ee.agent.workflows.durable.mint_llm_token",
            return_value="llm-token",
        ),
    ):
        with pytest.raises(ApplicationError, match="Agent execution failed"):
            await workflow_instance._run_with_nsjail(workflow_args, cfg)

    run_agent_call = execute_activity_mock.await_args_list[2]
    assert run_agent_call.kwargs["retry_policy"] == RETRY_POLICIES["activity:fail_slow"]
    executor_input = run_agent_call.args[1]
    assert executor_input.run_agent_failure_mode is RunAgentActivityFailureMode.RETRY
