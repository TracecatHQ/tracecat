"""Fused pre-executor prepare phase for the durable agent workflow.

Before dispatching the executor activity, a durable-agent turn must:

1. Resolve the effective agent config (preset + caller overrides + custom
   model provider runtime config).
2. Load session resume metadata (SDK session id, fork state, stored binding).
3. Resolve the subagent topology (a resumed session's stored binding wins).
4. Create or update the session row (curr_run_id, stream buffer, auto-title).
5. Compile tool definitions for the root scope and every subagent scope.

Historically the workflow scheduled each step as its own activity — up to six
sequential task-queue round-trips per turn. ``prepare_agent_turn_activity``
runs the same steps, in the same order, in one activity. The workflow gates it
behind a patch marker so pre-fuse histories keep replaying the old chain (see
``DurableAgentWorkflow._prepare_turn``).

Token minting stays in the workflow: MCP/LLM gateway tokens embed workflow
identity (``workflow.info()``) and are re-minted after approval waits.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.preset.activities import (
    ResolveAgentPresetConfigActivityInput,
    ResolveAgentsConfigActivityInput,
    resolve_agent_preset_config_activity,
    resolve_agents_config_activity,
    resolve_custom_model_provider_config_activity,
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.session.activities import (
    CreateSessionInput,
    LoadSessionInput,
    LoadSessionResult,
    create_session_activity,
    load_session_activity,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAgentsConfig,
    has_manual_tool_approvals,
)
from tracecat.agent.tokens import InternalToolContext
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import agent_config_from_payload
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat_ee.agent.activities import (
    AgentActivities,
    BuildAgentScopeToolDefsArgs,
    BuildAgentToolDefsArgs,
    BuildToolDefsResult,
)

ROOT_AGENT_SCOPE = "root"


class PrepareAgentTurnInput(BaseModel):
    """Inputs for the fused pre-executor prepare phase."""

    role: Role
    session_id: uuid.UUID
    curr_run_id: uuid.UUID
    user_prompt: str
    # Agent config source: explicit config, or a preset (with optional overrides).
    config: AgentConfig | None = None
    preset_slug: str | None = None
    preset_version: int | None = None
    agent_preset_id: uuid.UUID | None = None
    agent_preset_version_id: uuid.UUID | None = None
    # Session row fields.
    title: str = "New Chat"
    entity_type: AgentSessionEntity
    entity_id: uuid.UUID
    tools: list[str] | None = None
    harness_type: HarnessType = HarnessType.CLAUDE_CODE
    continue_existing_session: bool = False


class PreparedSubagent(BaseModel):
    """One resolved subagent and its compiled tool scope."""

    resolved: ResolvedSubagentConfig
    config: AgentConfig
    build_result: BuildToolDefsResult


class PrepareAgentTurnResult(BaseModel):
    """Everything the workflow needs to compile and dispatch the executor."""

    config: AgentConfig
    sdk_session_id: str | None = None
    is_fork: bool = False
    root_build_result: BuildToolDefsResult
    subagents: list[PreparedSubagent]


def internal_tool_context_for(
    entity_type: AgentSessionEntity, entity_id: uuid.UUID
) -> InternalToolContext | None:
    """Internal-tool context for builder assistant sessions, else None."""
    if entity_type != AgentSessionEntity.AGENT_PRESET_BUILDER:
        return None
    return InternalToolContext(
        preset_id=entity_id,
        entity_type="agent_preset_builder",
    )


async def _resolve_turn_config(input: PrepareAgentTurnInput) -> AgentConfig:
    """Resolve the effective root agent config.

    Preset runs pin an exact version when the workflow recorded one, otherwise
    resolve by slug (+ optional version number). The caller-provided config
    then acts as an override layer for actions and appended instructions.
    """
    if input.preset_slug:
        if input.agent_preset_version_id is not None:
            resolve_input = ResolveAgentPresetConfigActivityInput(
                role=input.role,
                preset_version_id=input.agent_preset_version_id,
            )
        else:
            resolve_input = ResolveAgentPresetConfigActivityInput(
                role=input.role,
                preset_slug=input.preset_slug,
                preset_version=input.preset_version,
            )
        payload = await resolve_agent_preset_config_activity(resolve_input)
        config = agent_config_from_payload(payload)
        if override := input.config:
            if override.actions:
                config.actions = override.actions
            if override.instructions:
                config.instructions = (
                    "\n".join([config.instructions, override.instructions])
                    if config.instructions
                    else override.instructions
                )
    else:
        if input.config is None:
            raise ApplicationError(
                "Config must be provided if preset_slug is not set",
                non_retryable=True,
            )
        config = input.config

    await _apply_custom_model_provider_config(input.role, config)
    return config


async def _apply_custom_model_provider_config(role: Role, config: AgentConfig) -> None:
    if config.model_provider != "custom-model-provider":
        return
    result = await resolve_custom_model_provider_config_activity(
        role, config.catalog_id
    )
    config.base_url = result.base_url
    config.passthrough = result.passthrough
    if result.model_name:
        config.model_name = result.model_name


def _preserved_agents_binding(
    load_result: LoadSessionResult,
) -> ResolvedAgentsConfig | None:
    """Return the resumed session's stored subagent binding, if any.

    A resumed session's stored binding is the stable runtime contract, even if
    the preset now follows a newer child version. A session with resume state
    but no binding predates bindings and is pinned to no subagents.
    """
    if not load_result.found:
        return None
    if load_result.agents_binding is not None:
        return load_result.agents_binding
    if load_result.has_resume_state:
        return ResolvedAgentsConfig()
    return None


async def _resolve_subagents(
    input: PrepareAgentTurnInput,
    config: AgentConfig,
    load_result: LoadSessionResult,
) -> ResolvedAgentsRuntimeConfig:
    if binding := _preserved_agents_binding(load_result):
        agents = AgentSubagentsConfig.model_validate(binding.model_dump(mode="json"))
        follow_latest_versions: bool | None = False
    else:
        agents = config.agents
        follow_latest_versions = None

    if not agents.enabled:
        return ResolvedAgentsRuntimeConfig()
    if not agents.subagents:
        return ResolvedAgentsRuntimeConfig(enabled=True)
    return await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=input.role,
            agents=agents,
            parent_preset_id=input.agent_preset_id,
            parent_slug=input.preset_slug,
            follow_latest_versions=follow_latest_versions,
        )
    )


def _scope_args(
    scope: str,
    config: AgentConfig,
    *,
    internal_tool_context: InternalToolContext | None = None,
    fail_on_mcp_discovery_error: bool = False,
) -> BuildAgentScopeToolDefsArgs:
    return BuildAgentScopeToolDefsArgs(
        scope=scope,
        tool_filters=ToolFilters(
            namespaces=config.namespaces,
            actions=config.actions,
        ),
        tool_approvals=config.tool_approvals,
        mcp_servers=config.mcp_servers,
        internal_tool_context=internal_tool_context,
        fail_on_mcp_discovery_error=fail_on_mcp_discovery_error,
    )


def _unsupported_subagent_approvals_error(preset: str) -> ApplicationError:
    return ApplicationError(
        f"Subagent preset '{preset}' uses manual approvals, "
        "which are not supported for subagents yet.",
        non_retryable=True,
    )


@activity.defn
async def prepare_agent_turn_activity(
    input: PrepareAgentTurnInput,
) -> PrepareAgentTurnResult:
    """Run the whole pre-executor prepare phase in one activity.

    Performs the same steps the legacy activity chain scheduled individually,
    in the same order, by calling the same underlying activity functions
    in-process (they are plain async functions and this activity provides the
    activity context they expect).
    """
    ctx_role.set(input.role)

    # 1. Effective agent config (preset + overrides + custom provider).
    config = await _resolve_turn_config(input)

    # 2. Session resume metadata.
    load_result = await load_session_activity(
        LoadSessionInput(role=input.role, session_id=input.session_id)
    )

    # 3. Subagent topology (a resumed session's stored binding wins).
    agents_result = await _resolve_subagents(input, config, load_result)

    # 4. Session row: create or update, pin curr_run_id, init stream buffer.
    create_result = await create_session_activity(
        CreateSessionInput(
            role=input.role,
            session_id=input.session_id,
            require_existing=input.continue_existing_session,
            title=input.title,
            created_by=input.role.user_id,
            entity_type=input.entity_type,
            entity_id=input.entity_id,
            tools=input.tools,
            agent_preset_id=input.agent_preset_id,
            agent_preset_version_id=input.agent_preset_version_id,
            agents_binding=agents_result.to_agents_binding(),
            harness_type=input.harness_type,
            curr_run_id=input.curr_run_id,
            initial_user_prompt=input.user_prompt,
        )
    )
    if not create_result.success:
        raise ApplicationError(
            f"Failed to create agent session: {create_result.error}",
            non_retryable=True,
        )

    # 5. Compile tool definitions for the root and every subagent scope.
    internal_tool_context = internal_tool_context_for(
        input.entity_type, input.entity_id
    )
    scopes = [
        _scope_args(
            ROOT_AGENT_SCOPE,
            config,
            internal_tool_context=internal_tool_context,
        )
    ]
    subagent_configs: list[tuple[ResolvedSubagentConfig, AgentConfig]] = []
    for resolved_subagent in agents_result.subagents:
        child_config = agent_config_from_payload(resolved_subagent.config)
        if has_manual_tool_approvals(child_config.tool_approvals):
            raise _unsupported_subagent_approvals_error(
                resolved_subagent.binding.preset
            )
        await _apply_custom_model_provider_config(input.role, child_config)
        subagent_configs.append((resolved_subagent, child_config))
        scopes.append(
            _scope_args(
                resolved_subagent.alias,
                child_config,
                fail_on_mcp_discovery_error=True,
            )
        )

    build_result = await AgentActivities().build_agent_tool_definitions(
        BuildAgentToolDefsArgs(role=input.role, scopes=scopes)
    )

    root_build_result = build_result.scopes.get(ROOT_AGENT_SCOPE)
    if root_build_result is None:
        raise ApplicationError(
            "Batched agent tool compilation did not return the root scope",
            non_retryable=True,
        )
    # Adopt the effective approval policy computed during tool compilation.
    if root_build_result.tool_approvals is not None:
        config.tool_approvals = root_build_result.tool_approvals

    subagents: list[PreparedSubagent] = []
    for resolved_subagent, child_config in subagent_configs:
        child_build_result = build_result.scopes.get(resolved_subagent.alias)
        if child_build_result is None:
            raise ApplicationError(
                "Batched agent tool compilation did not return scope "
                f"'{resolved_subagent.alias}'",
                non_retryable=True,
            )
        # Compilation may add approval-gated tools (e.g. user MCP policies).
        if has_manual_tool_approvals(child_build_result.tool_approvals):
            raise _unsupported_subagent_approvals_error(
                resolved_subagent.binding.preset
            )
        if child_build_result.tool_approvals is not None:
            child_config.tool_approvals = child_build_result.tool_approvals
        subagents.append(
            PreparedSubagent(
                resolved=resolved_subagent,
                config=child_config,
                build_result=child_build_result,
            )
        )

    return PrepareAgentTurnResult(
        config=config,
        sdk_session_id=load_result.sdk_session_id,
        is_fork=load_result.is_fork,
        root_build_result=root_build_result,
        subagents=subagents,
    )
