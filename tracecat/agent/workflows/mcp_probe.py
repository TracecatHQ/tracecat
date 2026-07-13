"""Workflows for MCP connection probing."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy

    from tracecat import config
    from tracecat.agent.mcp.stdio_probe_types import (
        MCP_STDIO_PERSIST_ACTIVITY_NAME,
        MCP_STDIO_PROBE_ACTIVITY_NAME,
        MCP_STDIO_PROBE_TIMEOUT_CAP,
        StdioMCPPersistInput,
        StdioMCPProbeInput,
        StdioMCPProbeResult,
        StdioMCPProbeWorkflowInput,
    )


@workflow.defn
class StdioMCPProbeWorkflow:
    """Probe a saved stdio MCP integration via the executor sandbox."""

    @workflow.run
    async def run(self, input: StdioMCPProbeWorkflowInput) -> StdioMCPProbeResult:
        probe_result: StdioMCPProbeResult = await workflow.execute_activity(
            MCP_STDIO_PROBE_ACTIVITY_NAME,
            StdioMCPProbeInput(
                mcp_integration_id=input.mcp_integration_id,
                role=input.role,
            ),
            task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
            start_to_close_timeout=timedelta(seconds=MCP_STDIO_PROBE_TIMEOUT_CAP + 30),
            schedule_to_close_timeout=timedelta(
                seconds=MCP_STDIO_PROBE_TIMEOUT_CAP + 60
            ),
            retry_policy=RetryPolicy(maximum_attempts=1),
            result_type=StdioMCPProbeResult,
        )
        if not input.persist_result or not probe_result.success:
            return probe_result

        persist_result: StdioMCPProbeResult = await workflow.execute_activity(
            MCP_STDIO_PERSIST_ACTIVITY_NAME,
            StdioMCPPersistInput(
                mcp_integration_id=input.mcp_integration_id,
                role=input.role,
                tools=probe_result.tools,
            ),
            task_queue=config.TRACECAT__AGENT_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            schedule_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(maximum_attempts=3),
            result_type=StdioMCPProbeResult,
        )
        return persist_result
