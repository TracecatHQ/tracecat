"""Internal router for agent execution (SDK/UDF use)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import APIRouter, HTTPException, status
from tracecat_registry import secrets as registry_secrets

from tracecat.agent.common.types import MCPServerConfig
from tracecat.agent.exceptions import AgentRunError
from tracecat.agent.runtime.pydantic_ai.runtime import run_agent as runtime_run_agent
from tracecat.agent.schemas import (
    AgentOutput,
    InternalRankItemsPairwiseRequest,
    InternalRankItemsRequest,
    InternalRunAgentRequest,
)
from tracecat.agent.service import AgentManagementService
from tracecat.agent.types import AgentConfig, OutputType
from tracecat.ai.ranker import rank_items as ranker_rank_items
from tracecat.ai.ranker import rank_items_pairwise as ranker_rank_items_pairwise
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.contexts import ctx_role, ctx_session_id
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.tiers.entitlements import Entitlement, check_entitlement

router = APIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    include_in_schema=False,
)

_PROVIDERS_WITH_OPTIONAL_WORKSPACE_CREDENTIALS = {"ollama"}
_SCALAR_OUTPUT_TYPES: set[str] = {"bool", "float", "int", "str"}
_LIST_OUTPUT_TYPES: set[str] = {
    "list[bool]",
    "list[float]",
    "list[int]",
    "list[str]",
}


async def _resolve_run_config(
    params: InternalRunAgentRequest, agent_svc: AgentManagementService
) -> AgentConfig:
    """Resolve runtime config from request config or preset slug."""
    if params.preset_slug:
        if agent_svc.presets is None:
            raise ValueError("Preset-based runs require workspace context.")
        return await agent_svc.presets.resolve_agent_preset_config(
            slug=params.preset_slug
        )

    if params.config is None:
        raise ValueError("Either 'config' or 'preset_slug' must be provided")
    return AgentConfig(**params.config.model_dump())


@asynccontextmanager
async def _provider_secrets_context(
    agent_svc: AgentManagementService, model_provider: str
):
    """Set provider credentials in registry secrets context for this request."""
    if model_provider in _PROVIDERS_WITH_OPTIONAL_WORKSPACE_CREDENTIALS:
        secrets_token = registry_secrets.set_context({})
        try:
            yield
        finally:
            registry_secrets.reset_context(secrets_token)
        return

    credentials = await agent_svc.get_workspace_provider_credentials(model_provider)
    if not credentials:
        raise ValueError(
            f"No credentials found for provider '{model_provider}'. "
            "Please configure credentials for this provider first."
        )

    secrets_token = registry_secrets.set_context(credentials)
    try:
        yield
    finally:
        registry_secrets.reset_context(secrets_token)


def _normalize_output_type(
    output_type: str | dict[str, Any] | None,
) -> OutputType | None:
    """Normalize runtime output_type to the narrow OutputType union."""
    if output_type is None:
        return None
    if isinstance(output_type, dict):
        return output_type
    if output_type in _SCALAR_OUTPUT_TYPES or output_type in _LIST_OUTPUT_TYPES:
        return cast(OutputType, output_type)
    raise ValueError(
        "Invalid output_type. Supported values are: bool, float, int, str, "
        "list[bool], list[float], list[int], list[str], or an object schema."
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
        agent_svc = AgentManagementService(session, role=role)
        config = await _resolve_run_config(params, agent_svc)
        mcp_servers: list[MCPServerConfig] | None = None
        if config.mcp_servers:
            mcp_servers = [MCPServerConfig(**s) for s in config.mcp_servers]

        if config and config.tool_approvals:
            await check_entitlement(session, role, Entitlement.AGENT_ADDONS)

        async with _provider_secrets_context(agent_svc, config.model_provider):
            result: AgentOutput = await runtime_run_agent(
                user_prompt=params.user_prompt,
                model_name=config.model_name,
                model_provider=config.model_provider,
                actions=config.actions,
                namespaces=config.namespaces,
                tool_approvals=config.tool_approvals,
                mcp_servers=mcp_servers,
                instructions=config.instructions,
                output_type=_normalize_output_type(config.output_type),
                model_settings=config.model_settings,
                max_tool_calls=params.max_tool_calls or 40,
                max_requests=params.max_requests,
                retries=config.retries,
                base_url=config.base_url,
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
        agent_svc = AgentManagementService(session, role=role)
        async with _provider_secrets_context(agent_svc, params.model_provider):
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
        agent_svc = AgentManagementService(session, role=role)
        async with _provider_secrets_context(agent_svc, params.model_provider):
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
