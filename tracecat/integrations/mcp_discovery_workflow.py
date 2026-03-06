"""Temporal workflows for persisted MCP discovery."""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity, workflow

from tracecat.contexts import ctx_role
from tracecat.integrations.mcp_discovery_types import (
    MCPDiscoveryWorkflowArgs,
    MCPDiscoveryWorkflowResult,
)

with workflow.unsafe.imports_passed_through():
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.integrations.service import IntegrationService


@workflow.defn(name="mcp_remote_discovery")
class MCPRemoteDiscoveryWorkflow:
    """Workflow wrapper for persisted remote HTTP/SSE MCP discovery."""

    @workflow.run
    async def run(self, args: MCPDiscoveryWorkflowArgs) -> MCPDiscoveryWorkflowResult:
        return await workflow.execute_activity(
            run_remote_mcp_discovery_activity,
            args,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )


@activity.defn
async def run_remote_mcp_discovery_activity(
    args: MCPDiscoveryWorkflowArgs,
) -> MCPDiscoveryWorkflowResult:
    """Run persisted remote MCP discovery and catalog persistence."""
    ctx_role.set(args.role)
    async with IntegrationService.with_session(role=args.role) as service:
        return await service.run_remote_mcp_discovery(
            mcp_integration_id=args.mcp_integration_id,
            trigger=args.trigger,
            started_at=args.started_at,
        )


@workflow.defn(name="mcp_local_stdio_discovery")
class MCPLocalStdioDiscoveryWorkflow:
    """Workflow wrapper for persisted local stdio MCP discovery."""

    @workflow.run
    async def run(self, args: MCPDiscoveryWorkflowArgs) -> MCPDiscoveryWorkflowResult:
        return await workflow.execute_activity(
            run_local_mcp_discovery_activity,
            args,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )


@activity.defn
async def run_local_mcp_discovery_activity(
    args: MCPDiscoveryWorkflowArgs,
) -> MCPDiscoveryWorkflowResult:
    """Run persisted local stdio MCP discovery and catalog persistence."""
    ctx_role.set(args.role)
    async with IntegrationService.with_session(role=args.role) as service:
        return await service.run_local_mcp_discovery(
            mcp_integration_id=args.mcp_integration_id,
            trigger=args.trigger,
            started_at=args.started_at,
        )


def get_mcp_discovery_activities() -> list:
    """Return activities required for remote MCP discovery."""
    return [
        run_remote_mcp_discovery_activity,
        run_local_mcp_discovery_activity,
    ]


def get_mcp_discovery_workflows() -> list[type]:
    """Return workflows required for persisted MCP discovery."""
    return [
        MCPRemoteDiscoveryWorkflow,
        MCPLocalStdioDiscoveryWorkflow,
    ]
