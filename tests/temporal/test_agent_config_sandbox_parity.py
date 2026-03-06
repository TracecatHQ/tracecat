"""Temporal regression tests for AgentConfig sandbox decode parity."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tracecat.agent.worker import new_sandbox_runner as new_agent_sandbox_runner
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.worker import new_sandbox_runner as new_dsl_sandbox_runner

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.common.types import (
        MCPHttpServerConfig,
        MCPServerConfig,
        MCPStdioServerConfig,
    )
    from tracecat.agent.types import AgentConfig

pytestmark = [pytest.mark.temporal]


class AgentConfigRoundTripResult(BaseModel):
    model_name: str
    enable_internet_access: bool
    server_count: int
    server_names: list[str]
    server_types: list[str]


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(),
    ) as workflow_env:
        yield workflow_env


def _build_mcp_servers(variant: str) -> list[MCPServerConfig] | None:
    http_server: MCPHttpServerConfig = {
        "name": "internal-tools",
        "url": "http://host.docker.internal:8080",
        "transport": "http",
        "headers": {"Authorization": "Bearer secret123"},
    }
    stdio_server: MCPStdioServerConfig = {
        "type": "stdio",
        "name": "local-tools",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"HOME": "/tmp"},
    }
    servers: list[MCPServerConfig]
    match variant:
        case "none":
            return None
        case "http":
            servers = [http_server]
        case "stdio":
            servers = [stdio_server]
        case "both":
            servers = [http_server, stdio_server]
        case _:
            raise AssertionError(f"Unexpected MCP variant: {variant}")
    return servers


def _build_agent_config(variant: str) -> AgentConfig:
    return AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        instructions="You are a security analyst.",
        actions=["tools.datadog.change_signal_state"],
        namespaces=["tools.datadog"],
        tool_approvals={"tools.datadog.change_signal_state": True},
        mcp_servers=_build_mcp_servers(variant),
        model_settings={"parallel_tool_calls": False},
        retries=3,
        enable_internet_access=True,
    )


@activity.defn(name="return_agent_config_round_trip")
async def return_agent_config_round_trip(variant: str) -> AgentConfig:
    return _build_agent_config(variant)


@workflow.defn
class AgentConfigRoundTripWorkflow:
    @workflow.run
    async def run(self, variant: str) -> AgentConfigRoundTripResult:
        config = await workflow.execute_activity(
            return_agent_config_round_trip,
            variant,
            start_to_close_timeout=timedelta(seconds=10),
        )
        servers = config.mcp_servers or []
        return AgentConfigRoundTripResult(
            model_name=config.model_name,
            enable_internet_access=config.enable_internet_access,
            server_count=len(servers),
            server_names=[server["name"] for server in servers],
            server_types=[server.get("type", "http") for server in servers],
        )


def _runner(kind: str):
    return new_agent_sandbox_runner() if kind == "agent" else new_dsl_sandbox_runner()


def _expected_round_trip(variant: str) -> AgentConfigRoundTripResult:
    servers = _build_mcp_servers(variant) or []
    return AgentConfigRoundTripResult(
        model_name="gpt-5.2",
        enable_internet_access=True,
        server_count=len(servers),
        server_names=[server["name"] for server in servers],
        server_types=[server.get("type", "http") for server in servers],
    )


@pytest.mark.anyio
@pytest.mark.parametrize("runner_kind", ["agent", "dsl"])
@pytest.mark.parametrize("variant", ["none", "http", "stdio", "both"])
async def test_agent_config_round_trips_in_both_workflow_sandboxes(
    env: WorkflowEnvironment,
    runner_kind: str,
    variant: str,
) -> None:
    task_queue = f"agent-config-round-trip-{runner_kind}-{variant}"
    async with Worker(
        env.client,
        task_queue=task_queue,
        activities=[return_agent_config_round_trip],
        workflows=[AgentConfigRoundTripWorkflow],
        workflow_runner=_runner(runner_kind),
    ):
        result = await env.client.execute_workflow(
            AgentConfigRoundTripWorkflow.run,
            variant,
            id=f"agent-config-round-trip-{runner_kind}-{variant}",
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=15),
        )

    assert result == _expected_round_trip(variant)
