"""Workflows for MCP connection probing."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy

    from tracecat import config
    from tracecat.agent.mcp.stdio_probe import (
        MCP_STDIO_DRAFT_PROBE_ACTIVITY_NAME,
        MCP_STDIO_PROBE_ACTIVITY_NAME,
        MCP_STDIO_PROBE_TIMEOUT_CAP,
        StdioMCPDraftProbeInput,
        StdioMCPProbeInput,
        StdioMCPProbeResult,
    )


@workflow.defn
class StdioMCPProbeWorkflow:
    """Probe a saved stdio MCP integration via the executor sandbox."""

    @workflow.run
    async def run(self, input: StdioMCPProbeInput) -> StdioMCPProbeResult:
        return await workflow.execute_activity(
            MCP_STDIO_PROBE_ACTIVITY_NAME,
            input,
            task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
            start_to_close_timeout=timedelta(seconds=MCP_STDIO_PROBE_TIMEOUT_CAP + 30),
            schedule_to_close_timeout=timedelta(
                seconds=MCP_STDIO_PROBE_TIMEOUT_CAP + 60
            ),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@workflow.defn
class StdioMCPDraftProbeWorkflow:
    """Probe a draft/unsaved stdio MCP config via the executor sandbox."""

    @workflow.run
    async def run(self, input: StdioMCPDraftProbeInput) -> StdioMCPProbeResult:
        return await workflow.execute_activity(
            MCP_STDIO_DRAFT_PROBE_ACTIVITY_NAME,
            input,
            task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
            start_to_close_timeout=timedelta(seconds=MCP_STDIO_PROBE_TIMEOUT_CAP + 30),
            schedule_to_close_timeout=timedelta(
                seconds=MCP_STDIO_PROBE_TIMEOUT_CAP + 60
            ),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
