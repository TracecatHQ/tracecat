"""Internal router for agent execution (SDK/UDF use)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import APIRouter, HTTPException, status
from tracecat_registry import secrets as registry_secrets

from tracecat.agent.exceptions import AgentRunError
from tracecat.agent.runtime.pydantic_ai.runtime import run_agent as runtime_run_agent
from tracecat.agent.schemas import (
    AgentOutput,
    InternalRankItemsPairwiseRequest,
    InternalRankItemsRequest,
    InternalRunAgentRequest,
    ModelSelection,
)
from tracecat.agent.service import (
    SOURCE_RUNTIME_API_KEY,
    SOURCE_RUNTIME_API_KEY_HEADER,
    SOURCE_RUNTIME_BASE_URL,
    AgentManagementService,
)
from tracecat.agent.types import AgentConfig, OutputType
from tracecat.ai.ranker import rank_items as ranker_rank_items
from tracecat.ai.ranker import rank_items_pairwise as ranker_rank_items_pairwise
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role, ctx_session_id
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement

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
_PROVIDER_BASE_URL_KEYS: dict[str, str] = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
    "azure_openai": "AZURE_API_BASE",
    "azure_ai": "AZURE_API_BASE",
}


def _merge_runtime_overrides(
    base: AgentConfig,
    overrides: AgentConfig,
    *,
    override_fields: set[str],
) -> AgentConfig:
    """Apply request-scoped fields on top of catalog-resolved runtime config."""
    if "instructions" in override_fields and overrides.instructions is not None:
        base.instructions = overrides.instructions
    if "output_type" in override_fields and overrides.output_type is not None:
        base.output_type = overrides.output_type
    if "actions" in override_fields and overrides.actions is not None:
        base.actions = overrides.actions
    if "namespaces" in override_fields and overrides.namespaces is not None:
        base.namespaces = overrides.namespaces
    if "tool_approvals" in override_fields and overrides.tool_approvals is not None:
        base.tool_approvals = overrides.tool_approvals
    if "model_settings" in override_fields and overrides.model_settings is not None:
        base.model_settings = overrides.model_settings
    if "mcp_servers" in override_fields and overrides.mcp_servers is not None:
        base.mcp_servers = overrides.mcp_servers
    if "retries" in override_fields:
        base.retries = overrides.retries
    if "enable_internet_access" in override_fields:
        base.enable_internet_access = overrides.enable_internet_access
    if "base_url" in override_fields:
        base.base_url = overrides.base_url
    return base


async def _resolve_run_config(
    params: InternalRunAgentRequest, agent_svc: AgentManagementService
) -> tuple[AgentConfig, set[str]]:
    """Resolve runtime config from request config or preset slug."""
    if params.preset_slug:
        if agent_svc.presets is None:
            raise ValueError("Preset-based runs require workspace context.")
        return (
            await agent_svc.presets.resolve_agent_preset_config(
                slug=params.preset_slug,
                preset_version=params.preset_version,
            ),
            set(),
        )

    if params.config is None:
        raise ValueError("Either 'config' or 'preset_slug' must be provided")
    return AgentConfig(**params.config.model_dump()), set(
        params.config.model_fields_set
    )


@asynccontextmanager
async def _provider_secrets_context(
    agent_svc: AgentManagementService,
    config: AgentConfig,
):
    """Set runtime credentials in registry secrets context for this request."""
    if config.model_provider in _PROVIDERS_WITH_OPTIONAL_WORKSPACE_CREDENTIALS:
        secrets_token = registry_secrets.set_context({})
        try:
            yield
        finally:
            registry_secrets.reset_context(secrets_token)
        return

    credentials = await agent_svc.get_runtime_credentials_for_config(config)
    if not credentials:
        raise ValueError(
            f"No credentials found for provider '{config.model_provider}'. "
            "Please configure credentials for this provider first."
        )
    if config.base_url is None:
        if source_base_url := credentials.get(SOURCE_RUNTIME_BASE_URL):
            config.base_url = source_base_url
        elif (
            base_url_key := _provider_base_url_key(agent_svc, config.model_provider)
        ) and (provider_base_url := credentials.get(base_url_key)):
            config.base_url = provider_base_url
    if (
        (api_key := credentials.get(SOURCE_RUNTIME_API_KEY))
        and (api_key_header := credentials.get(SOURCE_RUNTIME_API_KEY_HEADER))
        and api_key_header.lower() != "authorization"
    ):
        model_settings = dict(config.model_settings or {})
        extra_headers = dict(
            model_settings.get("extra_headers", {})
            if isinstance(model_settings.get("extra_headers"), dict)
            else {}
        )
        extra_headers[api_key_header] = api_key
        model_settings["extra_headers"] = extra_headers
        config.model_settings = model_settings

    secrets_token = registry_secrets.set_context(credentials)
    try:
        yield
    finally:
        registry_secrets.reset_context(secrets_token)


def _provider_base_url_key(
    agent_svc: AgentManagementService,
    provider: str,
) -> str | None:
    """Resolve provider base URL env key even when tests stub the service lightly."""
    if callable(
        resolver := getattr(agent_svc, "_provider_base_url_key", None)
    ) and isinstance(base_url_key := resolver(provider), str):
        return base_url_key
    return _PROVIDER_BASE_URL_KEYS.get(provider)


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
@require_scope("agent:execute")
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
        config, override_fields = await _resolve_run_config(params, agent_svc)
        config = _merge_runtime_overrides(
            await agent_svc.resolve_runtime_agent_config(config),
            config,
            override_fields=override_fields,
        )
        mcp_servers = config.mcp_servers

        if config and config.tool_approvals:
            await check_entitlement(session, role, Entitlement.AGENT_ADDONS)

        async with _provider_secrets_context(agent_svc, config):
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
@require_scope("agent:execute")
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
        config = await agent_svc.resolve_runtime_agent_config(
            AgentConfig(
                source_id=params.source_id,
                model_name=params.model_name,
                model_provider=params.model_provider,
                model_settings=params.model_settings,
                base_url=params.base_url,
            )
        )
        await agent_svc.require_enabled_model_selection(
            ModelSelection(
                source_id=config.source_id,
                model_name=config.model_name,
                model_provider=config.model_provider,
            ),
            workspace_id=role.workspace_id,
        )
        async with _provider_secrets_context(agent_svc, config):
            return await ranker_rank_items(
                items=params.items,
                criteria_prompt=params.criteria_prompt,
                model_name=config.model_name,
                model_provider=config.model_provider,
                model_settings=config.model_settings,
                max_requests=params.max_requests,
                retries=params.retries,
                base_url=config.base_url,
                min_items=params.min_items,
                max_items=params.max_items,
            )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/rank-pairwise", status_code=status.HTTP_200_OK)
@require_scope("agent:execute")
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
        config = await agent_svc.resolve_runtime_agent_config(
            AgentConfig(
                source_id=params.source_id,
                model_name=params.model_name,
                model_provider=params.model_provider,
                model_settings=params.model_settings,
                base_url=params.base_url,
            )
        )
        await agent_svc.require_enabled_model_selection(
            ModelSelection(
                source_id=config.source_id,
                model_name=config.model_name,
                model_provider=config.model_provider,
            ),
            workspace_id=role.workspace_id,
        )
        async with _provider_secrets_context(agent_svc, config):
            return await ranker_rank_items_pairwise(
                items=params.items,
                criteria_prompt=params.criteria_prompt,
                model_name=config.model_name,
                model_provider=config.model_provider,
                id_field=params.id_field,
                batch_size=params.batch_size,
                num_passes=params.num_passes,
                refinement_ratio=params.refinement_ratio,
                model_settings=config.model_settings,
                max_requests=params.max_requests,
                retries=params.retries,
                base_url=config.base_url,
                min_items=params.min_items,
                max_items=params.max_items,
            )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
