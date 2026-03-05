"""Unit tests for DurableAgentWorkflow search attribute upsert behavior."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from temporalio.common import TypedSearchAttributes
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs, DurableAgentWorkflow

from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
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

    upsert_mock.assert_called_once()
    updates = upsert_mock.call_args.args[0]
    values = _update_map(updates)
    assert values[TemporalSearchAttr.TRIGGER_TYPE.value] == TriggerType.SCHEDULED.value
    assert values[TemporalSearchAttr.EXECUTION_TYPE.value] == ExecutionType.DRAFT.value
    assert values[TemporalSearchAttr.WORKSPACE_ID.value] == existing_workspace
    assert values[TemporalSearchAttr.TRIGGERED_BY_USER_ID.value] == existing_user
