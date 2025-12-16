from fastapi import APIRouter

from tracecat import config
from tracecat.agent.factory import build_agent
from tracecat.agent.runtime import run_agent, run_agent_sync
from tracecat.agent.schemas import (
    AgentOutput,
    ExecutorAIActionRequest,
    ExecutorRunAgentRequest,
)
from tracecat.agent.types import AgentConfig, MCPServerConfig
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession

router = APIRouter(
    prefix="/internal/agent", tags=["internal-agent"], include_in_schema=False
)


@router.post("/action", response_model=AgentOutput)
async def executor_ai_action(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: ExecutorAIActionRequest,
) -> AgentOutput:
    """Run an AI action without tool calling support."""
    _ = (role, session)
    agent = await build_agent(
        AgentConfig(
            model_name=params.model_name,
            model_provider=params.model_provider,
            instructions=params.instructions,
            output_type=params.output_type,
            model_settings=params.model_settings,
            retries=params.retries,
            base_url=params.base_url,
        )
    )
    result = await run_agent_sync(
        agent,
        params.user_prompt,
        max_requests=params.max_requests,
    )
    return result


@router.post("/run", response_model=AgentOutput)
async def executor_run_agent(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: ExecutorRunAgentRequest,
) -> AgentOutput:
    """Run an AI agent with full feature support including MCP servers and tool calling."""
    _ = (role, session)

    # Convert MCP server schemas to MCPServerConfig TypedDicts
    mcp_servers: list[MCPServerConfig] | None = None
    if params.mcp_servers:
        mcp_servers = [
            MCPServerConfig(url=server.url, headers=server.headers)
            for server in params.mcp_servers
        ]

    result = await run_agent(
        user_prompt=params.user_prompt,
        model_name=params.model_name,
        model_provider=params.model_provider,
        instructions=params.instructions,
        output_type=params.output_type,
        model_settings=params.model_settings,
        max_requests=params.max_requests,
        max_tool_calls=params.max_tool_calls or config.TRACECAT__AGENT_MAX_TOOL_CALLS,
        retries=params.retries,
        base_url=params.base_url,
        mcp_servers=mcp_servers,
        actions=params.actions,
        namespaces=params.namespaces,
        tool_approvals=params.tool_approvals,
    )
    return result
