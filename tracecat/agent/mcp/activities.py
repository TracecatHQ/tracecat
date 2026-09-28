"""Temporal activities for MCP control-plane operations."""

from __future__ import annotations

from temporalio import activity

from tracecat.agent.mcp.stdio_probe_types import (
    StdioMCPPersistInput,
    StdioMCPProbeResult,
    sanitize_stdio_probe_error,
)
from tracecat.integrations.service import IntegrationService


@activity.defn(name="persist_stdio_mcp_connection_activity")
async def persist_stdio_mcp_connection_activity(
    input: StdioMCPPersistInput,
) -> StdioMCPProbeResult:
    """Persist a successful saved stdio MCP probe result."""
    activity.heartbeat(f"Persisting stdio MCP probe: {input.mcp_integration_id}")

    async with IntegrationService.with_session(role=input.role) as integrations_svc:
        try:
            tools = await integrations_svc.persist_mcp_integration_tools(
                mcp_integration_id=input.mcp_integration_id,
                discovered_tools=input.tools,
            )
        except ValueError as exc:
            return StdioMCPProbeResult(
                success=False,
                message="MCP integration probe result could not be persisted",
                error=sanitize_stdio_probe_error(str(exc)),
            )

    activity.heartbeat(f"Persisted stdio MCP probe: {input.mcp_integration_id}")
    return StdioMCPProbeResult(
        success=True,
        tools=tools,
        message=f"Connected successfully — {len(tools)} tools available",
    )
