"""Internal router for agent execution (SDK/UDF use)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status

from tracecat.agent.exceptions import AgentRunError
from tracecat.agent.runtime.pydantic_ai.runtime import run_agent as runtime_run_agent
from tracecat.agent.schemas import (
    AgentOutput,
    InternalRankItemsPairwiseRequest,
    InternalRankItemsRequest,
    InternalRunAgentRequest,
)
from tracecat.agent.types import MCPServerConfig
from tracecat.ai.ranker import rank_items as ranker_rank_items
from tracecat.ai.ranker import rank_items_pairwise as ranker_rank_items_pairwise
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.contexts import ctx_role, ctx_session_id
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger

router = APIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    include_in_schema=False,
)


@router.post("/run", status_code=status.HTTP_200_OK)
async def run_agent_endpoint(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalRunAgentRequest,
) -> dict[str, Any]:
    """Execute run_agent() with provided configuration."""
    ctx_role.set(role)
    session_id = uuid.uuid4()
    ctx_session_id.set(session_id)

    try:
        # Convert request schema to runtime types
        config = params.config
        mcp_servers: list[MCPServerConfig] | None = None
        if config and config.mcp_servers:
            mcp_servers = [MCPServerConfig(**s) for s in config.mcp_servers]

        result: AgentOutput = await runtime_run_agent(
            user_prompt=params.user_prompt,
            model_name=config.model_name if config else "",
            model_provider=config.model_provider if config else "",
            actions=config.actions if config else None,
            namespaces=config.namespaces if config else None,
            tool_approvals=config.tool_approvals if config else None,
            mcp_servers=mcp_servers,
            instructions=config.instructions if config else None,
            output_type=config.output_type if config else None,
            model_settings=config.model_settings if config else None,
            max_tool_calls=params.max_tool_calls or 40,
            max_requests=params.max_requests,
            retries=config.retries if config else 20,
            base_url=config.base_url if config else None,
        )
        return result.model_dump(mode="json")
    except AgentRunError as e:
        logger.exception("Agent run error", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_type": e.exc_cls.__name__, "message": e.exc_msg},
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/rank", status_code=status.HTTP_200_OK)
async def rank_items_endpoint(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalRankItemsRequest,
) -> list[str | int]:
    """Rank items using LLM based on natural language criteria."""
    ctx_role.set(role)

    try:
        return await ranker_rank_items(
            items=params.items,
            criteria_prompt=params.criteria_prompt,
            model_name=params.model_name,
            model_provider=params.model_provider,
            model_settings=params.model_settings,
            max_requests=params.max_requests,
            retries=params.retries,
            base_url=params.base_url,
            min_items=params.min_items,
            max_items=params.max_items,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/rank-pairwise", status_code=status.HTTP_200_OK)
async def rank_items_pairwise_endpoint(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalRankItemsPairwiseRequest,
) -> list[str | int]:
    """Rank items using pairwise LLM comparisons."""
    ctx_role.set(role)

    try:
        return await ranker_rank_items_pairwise(
            items=params.items,
            criteria_prompt=params.criteria_prompt,
            model_name=params.model_name,
            model_provider=params.model_provider,
            id_field=params.id_field,
            batch_size=params.batch_size,
            num_passes=params.num_passes,
            refinement_ratio=params.refinement_ratio,
            model_settings=params.model_settings,
            max_requests=params.max_requests,
            retries=params.retries,
            base_url=params.base_url,
            min_items=params.min_items,
            max_items=params.max_items,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
