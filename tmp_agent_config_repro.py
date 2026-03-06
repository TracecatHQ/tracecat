from __future__ import annotations

import asyncio
import os
import traceback
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from tracecat.agent.worker import new_sandbox_runner as new_agent_sandbox_runner
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.worker import new_sandbox_runner as new_dsl_sandbox_runner

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.types import AgentConfig
    from tracecat.agent.types import AgentConfig as WorkflowAgentConfig


@activity.defn
async def resolve_cfg() -> AgentConfig:
    variant = os.environ.get("REPRO_VARIANT", "full")
    match variant:
        case "minimal":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                retries=3,
                enable_internet_access=False,
            )
        case "instructions":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                instructions="synthetic",
                retries=3,
                enable_internet_access=False,
            )
        case "actions":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                actions=["tools.slack.post_message"],
                retries=3,
                enable_internet_access=False,
            )
        case "model_settings":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                model_settings={"parallel_tool_calls": False},
                retries=3,
                enable_internet_access=False,
            )
        case "mcp_empty":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                mcp_servers=[],
                retries=3,
                enable_internet_access=False,
            )
        case "mcp_http":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "Jira",
                        "url": "https://mcp.atlassian.com/v1/mcp",
                    }
                ],
                retries=3,
                enable_internet_access=False,
            )
        case "mcp_stdio":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                mcp_servers=[
                    {
                        "type": "stdio",
                        "name": "Jira",
                        "command": "python",
                        "args": ["-V"],
                    }
                ],
                retries=3,
                enable_internet_access=False,
            )
        case "mcp_http_headers":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "Jira",
                        "url": "https://mcp.atlassian.com/v1/mcp",
                        "headers": {"Authorization": "Bearer TEST"},
                    }
                ],
                retries=3,
                enable_internet_access=False,
            )
        case "full":
            return AgentConfig(
                model_name="gpt-5.2",
                model_provider="openai",
                instructions="synthetic",
                actions=["tools.slack.post_message"],
                model_settings={"parallel_tool_calls": False},
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "Jira",
                        "url": "https://mcp.atlassian.com/v1/mcp",
                        "headers": {"Authorization": "Bearer TEST"},
                    }
                ],
                retries=3,
                enable_internet_access=False,
            )
        case _:
            raise ValueError(f"Unknown REPRO_VARIANT: {variant}")


@workflow.defn
class ReproWorkflow:
    @workflow.run
    async def run(self) -> str:
        cfg = await workflow.execute_activity(
            resolve_cfg,
            start_to_close_timeout=timedelta(seconds=3),
        )
        assert isinstance(cfg, WorkflowAgentConfig)
        return cfg.model_name


async def run_case(name: str, runner) -> None:
    queue = f"repro-agent-config-decode-{name}"
    print(f"CASE={name}")
    try:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=get_data_converter(compression_enabled=False)
        ) as env:
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[ReproWorkflow],
                activities=[resolve_cfg],
                workflow_runner=runner,
            ):
                handle = await env.client.start_workflow(
                    ReproWorkflow.run,
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=5),
                )
                result = await handle.result()
                print("RESULT", result)
    except Exception as exc:
        print("EXC", type(exc).__name__, exc)
        traceback.print_exc()


async def main() -> None:
    await run_case("agent-sandbox", new_agent_sandbox_runner())
    await run_case("dsl-sandbox", new_dsl_sandbox_runner())


if __name__ == "__main__":
    asyncio.run(main())
