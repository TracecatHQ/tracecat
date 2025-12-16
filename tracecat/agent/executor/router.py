from fastapi import APIRouter

from tracecat.agent.factory import build_agent
from tracecat.agent.runtime import run_agent_sync
from tracecat.agent.schemas import AgentOutput, ExecutorAIActionRequest
from tracecat.agent.types import AgentConfig
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
