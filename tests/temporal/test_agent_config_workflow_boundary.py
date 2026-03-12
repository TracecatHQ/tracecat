"""Temporal regression tests for AgentConfig workflow-boundary decoding.

These tests pin both sides of the behavior:
- returning AgentConfig directly across the activity boundary reproduces the old
  decode failure
- returning AgentConfigPayload and rehydrating inside the workflow succeeds
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import TimeoutError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tracecat.agent.worker import new_sandbox_runner
from tracecat.agent.workflow_config import (
    agent_config_from_payload,
    agent_config_to_payload,
)
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.dsl._converter import get_data_converter

with workflow.unsafe.imports_passed_through():
    from tracecat_registry.sdk.agents import AgentConfig as RegistryAgentConfig

    from tracecat.agent.types import AgentConfig as TracecatAgentConfig

pytestmark = [pytest.mark.temporal]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(),
    ) as workflow_env:
        yield workflow_env


def _build_agent_config() -> TracecatAgentConfig:
    return TracecatAgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        source_id=None,
        instructions="You are a security analyst.",
        actions=["tools.datadog.change_signal_state"],
        namespaces=["tools.datadog"],
        tool_approvals={"tools.datadog.change_signal_state": True},
        mcp_servers=[
            {
                "name": "internal-tools",
                "url": "http://host.docker.internal:8080",
                "transport": "http",
                "headers": {"Authorization": "Bearer secret123"},
            }
        ],
        model_settings={"parallel_tool_calls": False},
        retries=3,
        enable_internet_access=True,
    )


@activity.defn(name="return_tracecat_agent_config")
async def return_tracecat_agent_config() -> TracecatAgentConfig:
    """Mock activity that returns the full runtime AgentConfig."""
    return _build_agent_config()


@activity.defn(name="return_agent_config_payload")
async def return_agent_config_payload() -> AgentConfigPayload:
    """Mock activity that returns the workflow-safe payload."""
    return agent_config_to_payload(_build_agent_config())


@workflow.defn
class DirectAgentConfigDecodeWorkflow:
    @workflow.run
    async def run(self, _: str) -> str:
        # Use the activity name string so Temporal respects result_type instead
        # of replacing it with the callable's annotated return type.
        config = await workflow.execute_activity(
            "return_tracecat_agent_config",
            start_to_close_timeout=timedelta(seconds=10),
            result_type=RegistryAgentConfig,
        )
        return config.model_name


@workflow.defn
class AgentConfigDecodeWorkflow:
    @workflow.run
    async def run(self, _: str) -> str:
        payload = await workflow.execute_activity(
            return_agent_config_payload,
            start_to_close_timeout=timedelta(seconds=10),
        )
        config = agent_config_from_payload(payload)
        assert config.model_name == "gpt-5.2"
        assert config.model_provider == "openai"
        assert config.source_id is None
        assert config.instructions == "You are a security analyst."
        assert config.actions == ["tools.datadog.change_signal_state"]
        assert config.namespaces == ["tools.datadog"]
        assert config.tool_approvals == {"tools.datadog.change_signal_state": True}
        assert config.mcp_servers == [
            {
                "type": "http",
                "name": "internal-tools",
                "url": "http://host.docker.internal:8080",
                "transport": "http",
                "headers": {"Authorization": "Bearer secret123"},
            }
        ]
        assert config.model_settings == {"parallel_tool_calls": False}
        assert config.retries == 3
        return config.model_name


@workflow.defn
class LegacyAgentConfigPayloadDecodeWorkflow:
    @workflow.run
    async def run(self, _: str) -> str:
        # This simulates replaying an activity result recorded before
        # resolve_agent_preset_config_activity switched to AgentConfigPayload.
        payload = await workflow.execute_activity(
            "return_tracecat_agent_config",
            start_to_close_timeout=timedelta(seconds=10),
            result_type=AgentConfigPayload,
        )
        config = agent_config_from_payload(payload)
        return config.model_name


@pytest.mark.anyio
async def test_agent_config_direct_boundary_reproduces_decode_failure(
    env: WorkflowEnvironment,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Direct AgentConfig workflow boundary should reproduce the old failure."""
    task_queue = "test-agent-config-direct-decode"
    caplog.set_level(logging.WARNING, logger="temporalio.worker._workflow_instance")

    async with Worker(
        env.client,
        task_queue=task_queue,
        activities=[return_tracecat_agent_config],
        workflows=[DirectAgentConfigDecodeWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        with pytest.raises(WorkflowFailureError) as exc_info:
            await env.client.execute_workflow(
                DirectAgentConfigDecodeWorkflow.run,
                "x",
                id="test-agent-config-direct-decode-1",
                task_queue=task_queue,
                execution_timeout=timedelta(seconds=3),
            )

    assert isinstance(exc_info.value.cause, TimeoutError)
    assert "Failed to decode payload for type AgentConfig" in caplog.text
    assert "Payload at index 0 with encoding json/plain could not be converted" in (
        caplog.text
    )
    assert "Failed decoding arguments" in caplog.text


@pytest.mark.anyio
async def test_agent_config_payload_survives_temporal_activity_boundary(
    env: WorkflowEnvironment,
) -> None:
    """Workflow-safe payload must round-trip through the Temporal boundary."""
    task_queue = "test-agent-config-decode"
    async with Worker(
        env.client,
        task_queue=task_queue,
        activities=[return_agent_config_payload],
        workflows=[AgentConfigDecodeWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await env.client.execute_workflow(
            AgentConfigDecodeWorkflow.run,
            "x",
            id="test-agent-config-decode-1",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=15),
        )
        assert result == "gpt-5.2"


@pytest.mark.anyio
async def test_legacy_agent_config_payload_replays_as_agent_config_payload(
    env: WorkflowEnvironment,
) -> None:
    """Legacy AgentConfig activity results should decode into AgentConfigPayload."""
    task_queue = "test-legacy-agent-config-payload-decode"
    async with Worker(
        env.client,
        task_queue=task_queue,
        activities=[return_tracecat_agent_config],
        workflows=[LegacyAgentConfigPayloadDecodeWorkflow],
        workflow_runner=new_sandbox_runner(),
    ):
        result = await env.client.execute_workflow(
            LegacyAgentConfigPayloadDecodeWorkflow.run,
            "x",
            id="test-legacy-agent-config-payload-decode-1",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=15),
        )
        assert result == "gpt-5.2"
