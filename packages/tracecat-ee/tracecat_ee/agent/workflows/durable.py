from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from temporalio import workflow
from temporalio.common import TypedSearchAttributes
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
)
from temporalio.exceptions import (
    CancelledError as TemporalCancelledError,
)

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolApproved, ToolDenied

    from tracecat import config
    from tracecat.agent.common.stream_types import HarnessType
    from tracecat.agent.common.types import (
        MCPToolDefinition,
        SandboxAgentConfig,
        SandboxSubagentConfig,
    )
    from tracecat.agent.executor.activity import (
        AgentExecutorInput,
        AgentExecutorResult,
        ApprovedToolCall,
        DeniedToolCall,
        run_agent_activity,
    )
    from tracecat.agent.executor.schemas import ToolExecutionResult
    from tracecat.agent.llm_routing import get_litellm_route_model
    from tracecat.agent.mcp.executor import (
        AGENT_TOOL_PRIORITY,
        build_run_input,
        build_tracecat_mcp_role,
    )
    from tracecat.agent.mcp.metadata import strip_proxy_tool_metadata
    from tracecat.agent.mcp.utils import (
        LEGACY_REGISTRY_MCP_SERVER_NAME,
        REGISTRY_MCP_SERVER_NAME,
        action_name_to_mcp_tool_name,
        normalize_mcp_tool_name,
    )
    from tracecat.agent.parsers import try_parse_json
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
    from tracecat.agent.schemas import AgentOutput, RunAgentArgs, RunUsage, ToolFilters
    from tracecat.agent.session.activities import (
        CreateSessionInput,
        FinalizeTurnInput,
        LoadSessionInput,
        LoadSessionMessagesInput,
        LoadSessionResult,
        PendingToolResult,
        ReconcileToolResultsInput,
        create_session_activity,
        finalize_turn_activity,
        load_session_activity,
        load_session_messages_activity,
        reconcile_tool_results_activity,
    )
    from tracecat.agent.session.types import AgentSessionEntity
    from tracecat.agent.subagents import (
        AgentSubagentsConfig,
        ResolvedAgentsConfig,
        has_manual_tool_approvals,
    )
    from tracecat.agent.tokens import (
        InternalToolContext,
        LLMRouteClaim,
        mint_llm_token,
        mint_mcp_token,
    )
    from tracecat.agent.types import AgentConfig
    from tracecat.agent.workflow_config import agent_config_from_payload
    from tracecat.auth.types import Role
    from tracecat.chat.schemas import ChatMessage
    from tracecat.contexts import ctx_role
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.executor.activities import ExecutorActivities
    from tracecat.logger import logger
    from tracecat.registry.lock.types import RegistryLock
    from tracecat.workflow.executions.correlation import (
        build_agent_session_correlation_id,
    )
    from tracecat.workflow.executions.enums import (
        ExecutionType,
        TemporalSearchAttr,
        TriggerType,
    )
    from tracecat_ee.agent.activities import (
        AgentActivities,
        BuildAgentScopeToolDefsArgs,
        BuildAgentToolDefsArgs,
        BuildToolDefsArgs,
        BuildToolDefsResult,
        EmitSessionCancelledInputs,
        EmitSessionDoneInputs,
        EmitSessionErrorInputs,
        ExecuteRemoteMCPToolArgs,
    )
    from tracecat_ee.agent.approvals.service import ApprovalManager, ApprovalMap
    from tracecat_ee.agent.context import AgentContext
    from tracecat_ee.agent.types import AgentWorkflowID


ROOT_AGENT_SCOPE = "root"
AGENT_TOOL_DEFINITION_ERROR = "AgentToolDefinitionError"
AGENT_EXECUTOR_PRE_STREAM_ERROR = "AgentExecutorPreStreamError"
AGENT_RUNTIME_EXECUTION_ERROR = "AgentRuntimeExecutionError"
BUILD_AGENT_TOOL_DEFINITIONS_PATCH = (
    "tracecat_ee.agent.workflows.durable.build_agent_tool_definitions"
)
EMIT_PRE_STREAM_SESSION_ERRORS_PATCH = (
    "tracecat_ee.agent.workflows.durable.emit_pre_stream_session_errors"
)
PERSIST_SESSION_ERROR_PATCH = (
    "tracecat_ee.agent.workflows.durable.persist_session_error"
)
# Temporal patch IDs are persisted in each workflow execution's history. Use a
# stable, unique ID for every command-producing workflow change, and never reuse
# an ID for another change. Keep both branches until old histories that lack the
# marker have aged out, then use workflow.deprecate_patch(...) before removing
# the marker entirely in a later cleanup.
AGENT_REQUEST_CANCEL_PATCH = "durable-agent-request-cancel-v1"
# Starts signing the caller scope snapshot and resolved Agent toolset into the
# MCP/executor chain. The legacy branch preserves deterministic replay for Agent
# workflow histories created before this authorization boundary existed.
AGENT_EXECUTION_GRANT_PATCH = "durable-agent-execution-grant-v1"


@dataclass(frozen=True, slots=True)
class LLMRouteResolution:
    route_model: str
    claim: LLMRouteClaim


def _activity_error_message(error: ActivityError) -> str:
    cause = error.cause
    if cause is not None:
        return str(cause)
    return str(error)


def _build_approved_tool_run_input(
    *,
    tool_call: ApprovedToolCall,
    registry_lock: RegistryLock,
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    execution_id: uuid.UUID,
    logical_time: datetime,
    allowed_actions: frozenset[str] | None = None,
):
    action_name = normalize_mcp_tool_name(tool_call.tool_name)
    return build_run_input(
        action_name=action_name,
        args=strip_proxy_tool_metadata(tool_call.args),
        registry_lock=registry_lock,
        workflow_id=workflow_id,
        run_id=run_id,
        execution_id=execution_id,
        logical_time=logical_time,
        allowed_actions=allowed_actions,
    )


def _approved_user_mcp_tool_name(tool_name: str) -> str | None:
    """Resolve an approved tool call to the tool name on its user MCP server.

    Approved user MCP tool calls arrive in one of two shapes: the normalized
    approval key (``mcp.{server}.{tool}``) or the raw proxy-routed runtime name
    (``mcp__tracecat-registry__mcp__{server}__{tool}``). Both map to the
    ``mcp__{server}__{tool}`` name expected by the trusted MCP router. Returns
    None for registry actions, which execute through the executor instead.
    """
    server, _, remote_tool = tool_name.removeprefix("mcp.").partition(".")
    is_normalized_user_mcp = (
        tool_name.startswith("mcp.")
        and bool(server and remote_tool)
        and server not in (REGISTRY_MCP_SERVER_NAME, LEGACY_REGISTRY_MCP_SERVER_NAME)
    )
    if not is_normalized_user_mcp:
        action_name = normalize_mcp_tool_name(tool_name)
        if not action_name.startswith("mcp."):
            return None
        server, _, remote_tool = action_name.removeprefix("mcp.").partition(".")
        if not server or not remote_tool:
            return None
        tool_name = action_name
    return action_name_to_mcp_tool_name(tool_name)


def _apply_tool_approvals(
    spec: AgentScopeSpec, build_result: BuildToolDefsResult
) -> None:
    """Adopt the effective approval policy computed during tool compilation."""
    if build_result.tool_approvals is not None:
        spec.config.tool_approvals = build_result.tool_approvals


def _start_remote_mcp_tool_call(
    tool_call: ApprovedToolCall,
    *,
    remote_tool_name: str,
    mcp_auth_token: str,
) -> workflow.ActivityHandle[str]:
    """Route an approved user MCP tool call through the trusted MCP router."""
    return workflow.start_activity_method(
        AgentActivities.execute_remote_mcp_tool,
        arg=ExecuteRemoteMCPToolArgs(
            mcp_auth_token=mcp_auth_token,
            tool_name=remote_tool_name,
            args=tool_call.args,
        ),
        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        start_to_close_timeout=timedelta(
            seconds=int(config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT)
        ),
        retry_policy=RETRY_POLICIES["activity:fail_fast"],
    )


def _start_registry_tool_call(
    tool_call: ApprovedToolCall,
    *,
    registry_lock: RegistryLock,
    service_role: Role,
    logical_time: datetime,
    allowed_actions: frozenset[str] | None = None,
) -> workflow.ActivityHandle[Any]:
    """Execute an approved registry action on the executor task queue."""
    return workflow.start_activity(
        ExecutorActivities.execute_action_activity,
        args=[
            _build_approved_tool_run_input(
                tool_call=tool_call,
                registry_lock=registry_lock,
                workflow_id=workflow.uuid4(),
                run_id=workflow.uuid4(),
                execution_id=workflow.uuid4(),
                logical_time=logical_time,
                allowed_actions=allowed_actions,
            ),
            service_role,
        ],
        task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        start_to_close_timeout=timedelta(
            seconds=int(config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT)
        ),
        heartbeat_timeout=timedelta(seconds=config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT)
        if config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT > 0
        else None,
        retry_policy=RETRY_POLICIES["activity:fail_fast"],
        priority=AGENT_TOOL_PRIORITY,
    )


def _cancelled_tool_result(
    tool_call: ApprovedToolCall, *, started: bool
) -> PendingToolResult:
    """Build the error result recorded for an approved tool skipped or stopped
    by a user cancel request."""
    phase = "during" if started else "before"
    return PendingToolResult(
        tool_call_id=tool_call.tool_call_id,
        tool_name=tool_call.tool_name,
        tool_input=tool_call.args,
        raw_result=f"Tool execution cancelled by user {phase} execution",
        is_error=True,
    )


def _llm_route_for_config(
    cfg: AgentConfig,
) -> LLMRouteResolution:
    route_model = get_litellm_route_model(
        model_provider=cfg.model_provider,
        model_name=cfg.model_name,
        passthrough=cfg.passthrough,
    )
    return LLMRouteResolution(
        route_model=route_model,
        claim=LLMRouteClaim(
            model=cfg.model_name,
            provider=cfg.model_provider,
            catalog_id=cfg.catalog_id,
            base_url=cfg.base_url,
            model_settings=cfg.model_settings or {},
        ),
    )


def _subagent_litellm_route_model(alias: str, route_model: str) -> str:
    """Return a unique incoming LiteLLM model key for one subagent scope."""
    return f"{route_model}::tracecat-subagent::{alias}"


class AgentScopeSpec(BaseModel):
    """Workflow-local description of one agent tool/token scope."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str
    config: AgentConfig
    internal_tool_context: InternalToolContext | None = None
    fail_on_mcp_discovery_error: bool = False

    def to_tool_defs_arg(self) -> BuildAgentScopeToolDefsArgs:
        return BuildAgentScopeToolDefsArgs(
            scope=self.name,
            tool_filters=ToolFilters(
                namespaces=self.config.namespaces,
                actions=self.config.actions,
            ),
            tool_approvals=self.config.tool_approvals,
            mcp_servers=self.config.mcp_servers,
            internal_tool_context=self.internal_tool_context,
            fail_on_mcp_discovery_error=self.fail_on_mcp_discovery_error,
        )


class SubagentScopeSpec(BaseModel):
    """Subagent metadata paired with its shared compile scope."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    scope: AgentScopeSpec
    resolved: ResolvedSubagentConfig


class CompiledAgentScope(BaseModel):
    """Workflow-local compiled form of one agent scope."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    spec: AgentScopeSpec
    build_result: BuildToolDefsResult
    mcp_auth_token: str
    model_route: str | None = None

    @property
    def tool_definitions(self) -> dict[str, MCPToolDefinition]:
        return self.build_result.tool_definitions


class CompiledSubagentScope(BaseModel):
    """Compiled subagent scope plus child-only runtime metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    scope: CompiledAgentScope
    resolved: ResolvedSubagentConfig

    def to_sandbox_subagent(self) -> SandboxSubagentConfig:
        return SandboxSubagentConfig(
            alias=self.resolved.alias,
            description=self.resolved.description,
            prompt=self.resolved.prompt,
            max_turns=self.resolved.max_turns,
            config=SandboxAgentConfig.from_agent_config(self.scope.spec.config),
            mcp_auth_token=self.scope.mcp_auth_token,
            model_route=self.scope.model_route,
            allowed_actions=self.scope.tool_definitions,
        )


class CompiledAgentRun(BaseModel):
    """Workflow-local compiled runtime inputs for a root agent plus subagents."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    root: CompiledAgentScope
    subagents: list[CompiledSubagentScope]
    registry_lock: RegistryLock
    """Root-agent lock used by parent-workflow approval execution."""
    llm_routes: dict[str, LLMRouteClaim]

    @property
    def sandbox_subagents(self) -> list[SandboxSubagentConfig]:
        return [subagent.to_sandbox_subagent() for subagent in self.subagents]


class AgentWorkflowArgs(BaseModel):
    """Arguments for starting an agent workflow."""

    # Temporal stores the original workflow input in history. Keep stale keys
    # replayable after workflow args evolve, including the removed legacy
    # ``use_workspace_credentials`` flag.
    model_config = ConfigDict(extra="ignore")

    role: Role
    agent_args: RunAgentArgs
    # Session metadata
    title: str = Field(default="New Chat", description="Session title")
    entity_type: AgentSessionEntity = Field(
        ..., description="Type of entity this session is associated with"
    )
    entity_id: uuid.UUID = Field(..., description="ID of the associated entity")
    tools: list[str] | None = Field(
        default=None, description="Tools available to the agent"
    )
    agent_preset_id: uuid.UUID | None = Field(
        default=None, description="Agent preset used for this session"
    )
    agent_preset_version_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Pinned preset version used for this workflow run. "
            "If null, the run follows the preset's current version."
        ),
    )
    harness_type: HarnessType | None = Field(
        default=None,
        description="Agent harness type. Reserved for future multi-harness support.",
    )
    continue_existing_session: bool = Field(
        default=False,
        description=("If true, session_id is caller-supplied and must already exist."),
    )


class WorkflowApprovalSubmission(BaseModel):
    approvals: ApprovalMap
    approved_by: uuid.UUID | None = None
    decision_metadata: dict[str, dict[str, Any]] | None = None
    new_stream_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Rotated per-turn Redis stream ID. When set, the workflow sends every "
            "event emitted after approval resumes to this new stream instead of "
            "the stream that ended at the approval pause, which may already have "
            "expired."
        ),
    )


class WorkflowCancelRequest(BaseModel):
    reason: Literal["user_cancel"] = "user_cancel"


def _resolve_agent_output(
    *,
    output: Any,
) -> Any:
    """Resolve final agent output."""
    if output is not None:
        return try_parse_json(output) if isinstance(output, str) else output
    return None


UPSERT_TRACECAT_SEARCH_ATTRIBUTES_PATCH = (
    "durable-agent-upsert-tracecat-search-attributes-v1"
)
# Temporal patch IDs are persisted in each workflow execution's history. Use a
# stable, unique ID for every command-producing workflow change, and never reuse
# an ID for another change. Keep both branches until old histories that lack the
# marker have aged out, then use workflow.deprecate_patch(...) before removing
# the marker entirely in a later cleanup.
LOAD_TERMINAL_MESSAGE_HISTORY_PATCH = "durable-agent-load-terminal-message-history-v1"
PRESERVE_RESUMED_AGENT_BINDINGS_PATCH = (
    "durable-agent-preserve-resumed-agent-bindings-v1"
)


def _agents_config_from_binding(
    binding: ResolvedAgentsConfig,
) -> AgentSubagentsConfig:
    return AgentSubagentsConfig.model_validate(binding.model_dump(mode="json"))


def _preserved_agents_binding(
    load_result: LoadSessionResult,
) -> ResolvedAgentsConfig | None:
    if not load_result.found:
        return None
    if load_result.agents_binding is not None:
        return load_result.agents_binding
    if load_result.has_resume_state:
        return ResolvedAgentsConfig()
    return None


FINALIZE_TURN_PATCH = "durable-agent-finalize-turn-v1"
REMINT_SCOPE_TOKENS_PATCH = "durable-agent-remint-scope-tokens-v1"
# Gates the approval-stream lifecycle as one capability: persist approvals before
# closing the pause stream, rotate continuations, and best-effort stream closure.
APPROVAL_STREAM_V2_PATCH = "durable-agent-approval-stream-v2"


@workflow.defn
class DurableAgentWorkflow:
    @workflow.init
    def __init__(self, args: AgentWorkflowArgs):
        self.role = args.role
        ctx_role.set(args.role)
        AgentContext.set(session_id=args.agent_args.session_id)

        self._status: Literal["running", "waiting_for_results", "done"] = "running"
        self._turn: int = 0
        if args.role.workspace_id is None:
            raise ApplicationError("Role must have a workspace ID", non_retryable=True)
        if args.role.organization_id is None:
            raise ApplicationError(
                "Role must have an organization ID", non_retryable=True
            )
        self.workspace_id = args.role.workspace_id
        self.organization_id = args.role.organization_id
        self.session_id = args.agent_args.session_id
        self.active_stream_id = args.agent_args.active_stream_id
        self._approval_stream_v2 = False
        self.harness_type = args.harness_type or "claude_code"
        self.approvals = ApprovalManager(role=self.role)
        self.max_requests = args.agent_args.max_requests
        self.max_tool_calls = args.agent_args.max_tool_calls
        self._cancel_requested: bool = False
        self._cancel_reason: str | None = None

    def _upsert_tracecat_search_attributes(self) -> None:
        """Ensure direct agent runs have core Tracecat search attributes.

        For workflows started with existing Tracecat attributes (e.g. DSL child
        workflows), this only fills missing keys from role/defaults and does
        not overwrite existing values.
        """
        search_attributes = (
            workflow.info().typed_search_attributes or TypedSearchAttributes.empty
        )
        updates = []

        if search_attributes.get(TemporalSearchAttr.TRIGGER_TYPE.key) is None:
            updates.append(
                TemporalSearchAttr.TRIGGER_TYPE.key.value_set(TriggerType.MANUAL.value)
            )
        if search_attributes.get(TemporalSearchAttr.EXECUTION_TYPE.key) is None:
            updates.append(
                TemporalSearchAttr.EXECUTION_TYPE.key.value_set(
                    ExecutionType.PUBLISHED.value
                )
            )
        if (
            search_attributes.get(TemporalSearchAttr.TRIGGERED_BY_USER_ID.key) is None
            and self.role.user_id is not None
        ):
            updates.append(
                TemporalSearchAttr.TRIGGERED_BY_USER_ID.key.value_set(
                    str(self.role.user_id)
                )
            )
        if (
            search_attributes.get(TemporalSearchAttr.WORKSPACE_ID.key) is None
            and self.role.workspace_id is not None
        ):
            updates.append(
                TemporalSearchAttr.WORKSPACE_ID.key.value_set(
                    str(self.role.workspace_id)
                )
            )
        if search_attributes.get(TemporalSearchAttr.CORRELATION_ID.key) is None:
            updates.append(
                TemporalSearchAttr.CORRELATION_ID.key.value_set(
                    build_agent_session_correlation_id(self.session_id)
                )
            )

        if updates:
            workflow.upsert_search_attributes(updates)

    async def _apply_custom_model_provider_config(
        self,
        cfg: AgentConfig,
    ) -> None:
        if cfg.model_provider != "custom-model-provider":
            return
        result = await workflow.execute_activity(
            resolve_custom_model_provider_config_activity,
            args=(self.role, cfg.catalog_id),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        cfg.base_url = result.base_url
        cfg.passthrough = result.passthrough
        if result.model_name:
            cfg.model_name = result.model_name
        logger.info(
            "Applied custom model provider runtime config",
            passthrough=cfg.passthrough,
            has_model_name_override=result.model_name is not None,
            has_base_url=bool(cfg.base_url),
        )

    async def _build_config(self, args: AgentWorkflowArgs) -> AgentConfig:
        if args.agent_args.preset_slug:
            activity_input = (
                ResolveAgentPresetConfigActivityInput(
                    role=self.role,
                    preset_version_id=args.agent_preset_version_id,
                )
                if args.agent_preset_version_id is not None
                else ResolveAgentPresetConfigActivityInput(
                    role=self.role,
                    preset_slug=args.agent_args.preset_slug,
                    preset_version=args.agent_args.preset_version,
                )
            )
            preset_config_payload = await workflow.execute_activity(
                resolve_agent_preset_config_activity,
                activity_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
            preset_config = agent_config_from_payload(preset_config_payload)
            # Apply overrides from the provided config (if any)
            # When using a preset, the 'config' in args acts as an override layer
            if override_cfg := args.agent_args.config:
                if override_cfg.actions:
                    preset_config.actions = override_cfg.actions

                if override_cfg.instructions:
                    if preset_config.instructions:
                        preset_config.instructions = "\n".join(
                            [
                                preset_config.instructions,
                                override_cfg.instructions,
                            ]
                        )
                    else:
                        preset_config.instructions = override_cfg.instructions

            cfg = preset_config
        else:
            if args.agent_args.config is None:
                raise ApplicationError(
                    "Config must be provided if preset_slug is not set",
                    non_retryable=True,
                )
            cfg = args.agent_args.config

        await self._apply_custom_model_provider_config(cfg)
        return cfg

    async def _resolve_agents_config(
        self,
        args: AgentWorkflowArgs,
        cfg: AgentConfig,
        *,
        agents: AgentSubagentsConfig | None = None,
        follow_latest_versions: bool | None = None,
    ) -> ResolvedAgentsRuntimeConfig:
        agents_config = agents if agents is not None else cfg.agents
        if not agents_config.enabled:
            return ResolvedAgentsRuntimeConfig()
        if not agents_config.subagents:
            return ResolvedAgentsRuntimeConfig(enabled=True)
        return await workflow.execute_activity(
            resolve_agents_config_activity,
            ResolveAgentsConfigActivityInput(
                role=self.role,
                agents=agents_config,
                parent_preset_id=args.agent_preset_id,
                parent_slug=args.agent_args.preset_slug,
                follow_latest_versions=follow_latest_versions,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )

    def _mint_scope_mcp_token(
        self,
        *,
        build_result: BuildToolDefsResult,
        internal_tool_context: InternalToolContext | None = None,
    ) -> str:
        info = workflow.info()
        return mint_mcp_token(
            workspace_id=self.workspace_id,
            organization_id=self.organization_id,
            user_id=self.role.user_id,
            allowed_actions=list(build_result.tool_definitions.keys()),
            scopes=self.role.scopes
            if workflow.patched(AGENT_EXECUTION_GRANT_PATCH)
            else None,
            session_id=self.session_id,
            parent_agent_workflow_id=info.workflow_id,
            parent_agent_run_id=info.run_id,
            user_mcp_servers=build_result.user_mcp_claims,
            allowed_internal_tools=build_result.allowed_internal_tools,
            internal_tool_context=internal_tool_context,
            registry_lock=build_result.registry_lock,
        )

    def _remint_scope_tokens(
        self,
        compiled_run: CompiledAgentRun,
        *,
        internal_tool_context: InternalToolContext | None,
    ) -> CompiledAgentRun:
        root = compiled_run.root.model_copy(
            update={
                "mcp_auth_token": self._mint_scope_mcp_token(
                    build_result=compiled_run.root.build_result,
                    internal_tool_context=internal_tool_context,
                )
            }
        )
        subagents = [
            subagent.model_copy(
                update={
                    "scope": subagent.scope.model_copy(
                        update={
                            "mcp_auth_token": self._mint_scope_mcp_token(
                                build_result=subagent.scope.build_result,
                            )
                        }
                    )
                }
            )
            for subagent in compiled_run.subagents
        ]
        return compiled_run.model_copy(update={"root": root, "subagents": subagents})

    async def _compile_agent_run(
        self,
        *,
        cfg: AgentConfig,
        subagents: list[ResolvedSubagentConfig],
        internal_tool_context: InternalToolContext | None,
    ) -> CompiledAgentRun:
        root_spec = AgentScopeSpec(
            name=ROOT_AGENT_SCOPE,
            config=cfg,
            internal_tool_context=internal_tool_context,
        )
        if not workflow.patched(BUILD_AGENT_TOOL_DEFINITIONS_PATCH):
            try:
                legacy_build_result = await workflow.execute_activity_method(
                    AgentActivities.build_tool_definitions,
                    arg=BuildToolDefsArgs(
                        role=self.role,
                        tool_filters=ToolFilters(
                            namespaces=cfg.namespaces,
                            actions=cfg.actions,
                        ),
                        tool_approvals=cfg.tool_approvals,
                        mcp_servers=cfg.mcp_servers,
                        internal_tool_context=internal_tool_context,
                    ),
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )
            except ActivityError as e:
                if isinstance(e.cause, ApplicationError):
                    raise e.cause from e
                raise

            _apply_tool_approvals(root_spec, legacy_build_result)
            root_scope = CompiledAgentScope(
                spec=root_spec,
                build_result=legacy_build_result,
                mcp_auth_token=self._mint_scope_mcp_token(
                    build_result=legacy_build_result,
                    internal_tool_context=internal_tool_context,
                ),
            )
            return CompiledAgentRun(
                root=root_scope,
                subagents=[],
                registry_lock=legacy_build_result.registry_lock,
                llm_routes={},
            )

        subagent_specs: list[SubagentScopeSpec] = []
        scope_specs = [root_spec]
        for resolved_subagent in subagents:
            child_cfg = agent_config_from_payload(resolved_subagent.config)
            if has_manual_tool_approvals(child_cfg.tool_approvals):
                raise ApplicationError(
                    f"Subagent preset '{resolved_subagent.binding.preset}' uses manual approvals, "
                    "which are not supported for subagents yet.",
                    non_retryable=True,
                )
            await self._apply_custom_model_provider_config(child_cfg)
            scope_spec = AgentScopeSpec(
                name=resolved_subagent.alias,
                config=child_cfg,
                fail_on_mcp_discovery_error=True,
            )
            subagent_specs.append(
                SubagentScopeSpec(
                    scope=scope_spec,
                    resolved=resolved_subagent,
                )
            )
            scope_specs.append(scope_spec)

        try:
            build_result = await workflow.execute_activity_method(
                AgentActivities.build_agent_tool_definitions,
                arg=BuildAgentToolDefsArgs(
                    role=self.role,
                    scopes=[spec.to_tool_defs_arg() for spec in scope_specs],
                ),
                start_to_close_timeout=timedelta(
                    seconds=120 * max(1, len(scope_specs))
                ),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as e:
            if isinstance(e.cause, ApplicationError):
                raise e.cause from e
            raise

        root_build_result = build_result.scopes.get(ROOT_AGENT_SCOPE)
        if root_build_result is None:
            raise ApplicationError(
                "Batched agent tool compilation did not return the root scope",
                non_retryable=True,
            )

        _apply_tool_approvals(root_spec, root_build_result)
        root_scope = CompiledAgentScope(
            spec=root_spec,
            build_result=root_build_result,
            mcp_auth_token=self._mint_scope_mcp_token(
                build_result=root_build_result,
                internal_tool_context=internal_tool_context,
            ),
        )
        compiled_subagents: list[CompiledSubagentScope] = []
        llm_routes: dict[str, LLMRouteClaim] = {}

        for subagent_spec in subagent_specs:
            scope_spec = subagent_spec.scope
            child_build_result = build_result.scopes.get(scope_spec.name)
            if child_build_result is None:
                raise ApplicationError(
                    f"Batched agent tool compilation did not return scope '{scope_spec.name}'",
                    non_retryable=True,
                )
            if has_manual_tool_approvals(child_build_result.tool_approvals):
                raise ApplicationError(
                    f"Subagent preset '{subagent_spec.resolved.binding.preset}' uses manual approvals, "
                    "which are not supported for subagents yet.",
                    non_retryable=True,
                )
            _apply_tool_approvals(scope_spec, child_build_result)
            route_resolution = _llm_route_for_config(
                scope_spec.config,
            )
            scoped_route_model = _subagent_litellm_route_model(
                scope_spec.name,
                route_resolution.route_model,
            )
            llm_routes[scoped_route_model] = route_resolution.claim

            compiled_subagents.append(
                CompiledSubagentScope(
                    scope=CompiledAgentScope(
                        spec=scope_spec,
                        build_result=child_build_result,
                        mcp_auth_token=self._mint_scope_mcp_token(
                            build_result=child_build_result,
                        ),
                        model_route=scoped_route_model,
                    ),
                    resolved=subagent_spec.resolved,
                )
            )

        return CompiledAgentRun(
            root=root_scope,
            subagents=compiled_subagents,
            registry_lock=root_build_result.registry_lock,
            llm_routes=llm_routes,
        )

    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        """Run the agent until completion. The agent will call tools until it needs human approval."""
        if workflow.patched(UPSERT_TRACECAT_SEARCH_ATTRIBUTES_PATCH):
            self._upsert_tracecat_search_attributes()
        logger.debug(
            "DurableAgentWorkflow run", args=args, harness_type=self.harness_type
        )
        logger.debug("AGENT CONTEXT", agent_context=AgentContext.get())
        if workflow.unsafe.is_replaying():
            logger.debug("Workflow is replaying")
        else:
            logger.debug("Starting agent", prompt=args.agent_args.user_prompt)

        try:
            cfg = await self._build_config(args)
            # Success needs no write: last_error was already cleared at turn
            # start, and last_error is the only persisted run-outcome signal.
            return await self._run_with_agent_executor(args, cfg)
        except ActivityError as e:
            # Pre-stream failure: persist last_error and stream it (the loopback
            # was not yet wired up to surface it inline).
            await self._finalize_session_error(
                _activity_error_message(e),
                should_stream=workflow.patched(EMIT_PRE_STREAM_SESSION_ERRORS_PATCH),
            )
            raise
        except ApplicationError as e:
            # Runtime errors stream inline via the loopback, so persist-only.
            # Pre-stream errors (tool-definition / pre-runtime) stream too.
            should_stream = e.type == AGENT_TOOL_DEFINITION_ERROR or (
                e.type != AGENT_RUNTIME_EXECUTION_ERROR
                and workflow.patched(EMIT_PRE_STREAM_SESSION_ERRORS_PATCH)
            )
            await self._finalize_session_error(e.message, should_stream=should_stream)
            raise
        finally:
            # Terminal boundary only: approval-pause awaits inside the executor
            # loop and never reaches here. Clear the active-turn pointers so the
            # mid-turn DB filter releases the final rows and reconnect -> 204.
            # Patch-gated: finalize_turn_activity is a new command, so old
            # histories recorded before this change must not replay it.
            if workflow.patched(FINALIZE_TURN_PATCH):
                await self._finalize_turn()

    async def _finalize_turn(self) -> None:
        """Clear active-turn pointers at terminal (compare-and-clear by run_id)."""
        # Use the workflow-id token (same as the persisted curr_run_id), not
        # args.agent_args.curr_run_id, which is None for DSL/workflow callers and
        # would skip cleanup. workflow.info() is replay-safe.
        run_id = AgentWorkflowID.from_workflow_id(
            workflow.info().workflow_id
        ).session_id
        try:
            await workflow.execute_activity(
                finalize_turn_activity,
                FinalizeTurnInput(
                    role=self.role,
                    session_id=self.session_id,
                    run_id=run_id,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                # Idempotent (compare-and-clear by run_id); retry so a transient
                # failure doesn't leave curr_run_id set and hide the final row.
                retry_policy=RETRY_POLICIES["activity:fail_slow"],
            )
        except ActivityError as exc:
            logger.warning(
                "Failed to finalize agent turn pointers",
                session_id=str(self.session_id),
                run_id=str(run_id),
                error=str(exc),
            )

    async def _finalize_session_error(
        self, message: str, *, should_stream: bool
    ) -> None:
        """Persist last_error (and optionally stream it) on terminal failure.

        Replay-gated and best-effort: the activity swallows its own persistence
        failures, and we guard the schedule so finalizing never masks the
        agent's real error or aborts the workflow's own error propagation.
        """
        if not workflow.patched(PERSIST_SESSION_ERROR_PATCH):
            # Pre-patch histories kept their original pre-stream-only behavior,
            # so preserve that command shape on replay.
            if not should_stream:
                return
        try:
            await workflow.execute_activity_method(
                AgentActivities.emit_session_error,
                EmitSessionErrorInputs(
                    role=self.role,
                    session_id=self.session_id,
                    workspace_id=self.workspace_id,
                    message=message,
                    # Chat turns pin a per-turn stream ID; the client reads that
                    # stream, so the error/done markers must land there.
                    # None falls back to the per-session key for non-chat turns.
                    active_stream_id=self.active_stream_id,
                    should_stream=should_stream,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as emit_error:
            logger.warning(
                "Failed to finalize terminal agent session error",
                session_id=self.session_id,
                error=str(emit_error),
            )

    async def _emit_session_cancelled(
        self,
        *,
        emit_stream: bool,
        interrupted_tool_call_ids: list[str] | None = None,
    ) -> None:
        """Record the cancelled turn (timeline marker + optional stream notice).

        The activity always persists the cancelled-marker history row so the
        divider survives DB reloads. Stream emission depends on the cancel
        path: cancellation during the mid-turn executor activity flows through
        the loopback handler, which emits the stream notice itself
        (``emit_stream=False`` here); cancelling while waiting on approval
        decisions never starts (or has already finished) that activity, so the
        workflow must emit the notice too (``emit_stream=True``).
        """
        try:
            await workflow.execute_activity_method(
                AgentActivities.emit_session_cancelled,
                EmitSessionCancelledInputs(
                    role=self.role,
                    session_id=self.session_id,
                    workspace_id=self.workspace_id,
                    reason=self._cancel_reason or "user_cancel",
                    # Chat turns pin a per-turn stream ID; the client reads that
                    # stream, so the cancelled/done markers must land there.
                    # None falls back to the per-session key for non-chat turns.
                    active_stream_id=self.active_stream_id,
                    emit_stream=emit_stream,
                    interrupted_tool_call_ids=interrupted_tool_call_ids,
                    # Pin the marker to this run explicitly: the session row's
                    # curr_run_id may already point at a newer turn by the time
                    # this cancelled workflow finalizes.
                    curr_run_id=AgentWorkflowID.from_workflow_id(
                        workflow.info().workflow_id
                    ).session_id,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as emit_error:
            logger.warning(
                "Failed to emit agent session cancelled notice",
                session_id=self.session_id,
                error=str(emit_error),
            )

    async def _emit_approval_pause_done(self) -> None:
        """Close the approval-pause stream after approval rows are durable."""
        try:
            await workflow.execute_activity_method(
                AgentActivities.emit_session_done,
                EmitSessionDoneInputs(
                    role=self.role,
                    session_id=self.session_id,
                    workspace_id=self.workspace_id,
                    active_stream_id=self.active_stream_id,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as emit_error:
            logger.warning(
                "Failed to emit approval-pause stream done",
                session_id=self.session_id,
                error=str(emit_error),
            )

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> bool:
        submission = WorkflowApprovalSubmission.model_validate(submission)
        logger.info(
            "Setting approvals",
            approvals=submission.approvals,
            approved_by=submission.approved_by,
            new_stream_id=str(submission.new_stream_id)
            if submission.new_stream_id
            else None,
        )
        self.approvals.set(
            submission.approvals,
            approved_by=submission.approved_by,
            decision_metadata=submission.decision_metadata,
        )
        # This synchronous handler cannot resume mid-update, and new_stream_id is
        # the compatibility gate (pre-rotation updates omit it). Keep the pointer
        # mutation last so a failed ApprovalManager.set cannot partially rotate to
        # a definitively rejected continuation attempt.
        if submission.new_stream_id is not None:
            self.active_stream_id = submission.new_stream_id
        return self.approvals.is_ready()

    @set_approvals.validator
    def validate_set_approvals(self, submission: WorkflowApprovalSubmission) -> None:
        """Ensure all expected tool approvals are provided."""
        submission = WorkflowApprovalSubmission.model_validate(submission)
        logger.info(
            "Validating approvals update",
            approvals=list(submission.approvals.keys()),
            approved_by=submission.approved_by,
        )
        self.approvals.validate_responses(submission.approvals)
        if submission.decision_metadata:
            unexpected_metadata_ids = set(submission.decision_metadata) - set(
                submission.approvals
            )
            if unexpected_metadata_ids:
                raise ValueError(
                    "Received decision metadata for unknown tool calls: "
                    + ", ".join(sorted(unexpected_metadata_ids))
                )

    @workflow.update
    def request_cancel(self, request: WorkflowCancelRequest) -> None:
        logger.info(
            "Agent cancellation requested",
            session_id=self.session_id,
            reason=request.reason,
        )
        if self._cancel_reason is None:
            self._cancel_reason = request.reason
        self._cancel_requested = True

    @request_cancel.validator
    def validate_request_cancel(self, request: WorkflowCancelRequest) -> None:
        WorkflowCancelRequest.model_validate(request)

    async def _run_with_agent_executor(
        self, args: AgentWorkflowArgs, cfg: AgentConfig
    ) -> AgentOutput:
        """Run the agent through the executor activity.

        This path:
        1. Resolves tool definitions from registry
        2. Loads session history from DB (for resume)
        3. Mints JWT/LLM gateway tokens
        4. Calls run_agent_activity, which dispatches one runtime turn
        5. Persists session history after execution
        6. Handles approval requests
        """
        logger.info("Running agent executor", session_id=self.session_id)

        # Persist the workflow-id UUID token used to start this execution so
        # approval continuation can target the exact live workflow later.
        curr_run_id = AgentWorkflowID.from_workflow_id(
            workflow.info().workflow_id
        ).session_id
        load_result: LoadSessionResult | None = None
        if workflow.patched(PRESERVE_RESUMED_AGENT_BINDINGS_PATCH):
            # Load session topology before resolving agents. A resumed session's
            # stored binding is the stable runtime contract, even if the preset
            # now follows a newer child version.
            load_result = await workflow.execute_activity(
                load_session_activity,
                LoadSessionInput(role=self.role, session_id=self.session_id),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
            if preserved_binding := _preserved_agents_binding(load_result):
                agents_result = await self._resolve_agents_config(
                    args,
                    cfg,
                    agents=_agents_config_from_binding(preserved_binding),
                    follow_latest_versions=False,
                )
            else:
                agents_result = await self._resolve_agents_config(args, cfg)
        else:
            agents_result = await self._resolve_agents_config(args, cfg)

        # Create or get the AgentSession - idempotent, safe to call on resume
        # Persist the active workflow token as curr_run_id for approval lookups.
        create_result = await workflow.execute_activity(
            create_session_activity,
            CreateSessionInput(
                role=self.role,
                session_id=self.session_id,
                require_existing=args.continue_existing_session,
                title=args.title,
                created_by=self.role.user_id,
                entity_type=args.entity_type,
                entity_id=args.entity_id,
                tools=args.tools,
                agent_preset_id=args.agent_preset_id,
                agent_preset_version_id=args.agent_preset_version_id,
                agents_binding=agents_result.to_agents_binding(),
                harness_type=HarnessType(self.harness_type),
                curr_run_id=curr_run_id,
                initial_user_prompt=args.agent_args.user_prompt,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        if not create_result.success:
            raise ApplicationError(
                f"Failed to create agent session: {create_result.error}",
                non_retryable=True,
            )

        # Build internal tool context for builder assistant sessions
        internal_tool_context: InternalToolContext | None = None
        if args.entity_type == AgentSessionEntity.AGENT_PRESET_BUILDER:
            internal_tool_context = InternalToolContext(
                preset_id=args.entity_id,
                entity_type="agent_preset_builder",
            )

        # Resolve root and subagent tool definitions in one activity, while
        # preserving partitioned outputs for scope-specific tokens and tools.
        compiled_run = await self._compile_agent_run(
            cfg=cfg,
            subagents=agents_result.subagents,
            internal_tool_context=internal_tool_context,
        )
        root_registry_lock = compiled_run.registry_lock
        allowed_actions = compiled_run.root.tool_definitions

        logger.debug(
            "Resolved tool definitions",
            action_count=len(allowed_actions),
            actions=list(allowed_actions.keys()),
            registry_lock_origins=list(root_registry_lock.origins.keys()),
        )

        if load_result is None:
            # Legacy command order for histories without the binding-preservation
            # patch marker. sdk_session_data is replay compatibility only; new
            # activity executions leave it unset.
            load_result = await workflow.execute_activity(
                load_session_activity,
                LoadSessionInput(role=self.role, session_id=self.session_id),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )

        if load_result.found and load_result.sdk_session_id:
            logger.info(
                "Resuming from existing session",
                sdk_session_id=load_result.sdk_session_id,
                is_fork=load_result.is_fork,
            )

        info = workflow.info()
        # Mint the LLM gateway token after compiling subagent routes. MCP tokens
        # are scoped and minted as part of the compiled agent run.
        llm_gateway_auth_token = mint_llm_token(
            workspace_id=self.workspace_id,
            organization_id=self.organization_id,
            session_id=self.session_id,
            model=cfg.model_name,
            provider=cfg.model_provider,
            catalog_id=cfg.catalog_id,
            base_url=cfg.base_url,
            model_settings=cfg.model_settings,
            routes=compiled_run.llm_routes,
        )
        self._approval_stream_v2 = workflow.patched(APPROVAL_STREAM_V2_PATCH)

        # Prepare executor input
        executor_input = AgentExecutorInput(
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            active_stream_id=args.agent_args.active_stream_id,
            curr_run_id=curr_run_id,
            user_prompt=args.agent_args.user_prompt,
            config=cfg,
            role=self.role,
            mcp_auth_token=compiled_run.root.mcp_auth_token,
            llm_gateway_auth_token=llm_gateway_auth_token,
            allowed_actions=allowed_actions,
            subagents=compiled_run.sandbox_subagents,
            sdk_session_id=load_result.sdk_session_id,
            sdk_session_data=load_result.sdk_session_data,
            defer_done_on_approval=self._approval_stream_v2,
            is_fork=load_result.is_fork,
        )

        # Run the executor activity
        while True:
            logger.info("Executing agent turn", turn=self._turn)

            # Run one executor activity turn with update-driven cancellation.
            if not workflow.patched(AGENT_REQUEST_CANCEL_PATCH):
                result = await workflow.execute_activity(
                    run_agent_activity,
                    executor_input,
                    task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
                    start_to_close_timeout=timedelta(
                        seconds=config.TRACECAT__AGENT_SANDBOX_TIMEOUT
                    ),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )
            else:
                activity_handle = workflow.start_activity(
                    run_agent_activity,
                    executor_input,
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    task_queue=config.TRACECAT__AGENT_EXECUTOR_QUEUE,
                    start_to_close_timeout=timedelta(
                        seconds=config.TRACECAT__AGENT_SANDBOX_TIMEOUT
                    ),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )
                # ActivityHandle is an asyncio.Task subclass, so .done() is
                # valid. Neither wait_condition nor the handle poll emits
                # history commands, so this race stays replay-safe.
                await workflow.wait_condition(
                    lambda handle=activity_handle: (
                        handle.done() or self._cancel_requested
                    )
                )
                if not activity_handle.done():
                    activity_handle.cancel()
                try:
                    result = await activity_handle
                except ActivityError as e:
                    if self._cancel_requested and isinstance(
                        e.cause, TemporalCancelledError
                    ):
                        # The activity was cancelled without returning a
                        # loopback result (never picked up, still in setup, or
                        # hard-cancelled), so no executor wrote the cancelled/
                        # done frames to the per-turn stream. Emit them here or
                        # the client's SSE reader blocks until disconnect.
                        return await self._cancelled_turn_output(
                            AgentExecutorResult(
                                success=True,
                                cancelled=True,
                                cancelled_reason=self._cancel_reason or "user_cancel",
                            ),
                            info,
                            emit_cancelled=True,
                        )
                    raise

            if result.cancelled:
                logger.info(
                    "Agent turn cancelled",
                    session_id=self.session_id,
                    reason=result.cancelled_reason,
                )
                # Executor loopback already emitted the cancelled stream notice.
                return await self._cancelled_turn_output(
                    result, info, emit_cancelled=False
                )

            if not result.success:
                # Missing means a legacy activity result from before the flag
                # existed; preserve the old no-fallback behavior on replay.
                terminal_stream_error_emitted = (
                    result.terminal_stream_error_emitted is not False
                )
                raise ApplicationError(
                    f"Agent execution failed: {result.error}",
                    type=AGENT_RUNTIME_EXECUTION_ERROR
                    if terminal_stream_error_emitted
                    else AGENT_EXECUTOR_PRE_STREAM_ERROR,
                    non_retryable=True,
                )

            if result.approval_requested:
                logger.info("Agent waiting for approval", session_id=self.session_id)
                # Convert ToolCallContent to ToolCallPart for ApprovalManager
                if result.approval_items:
                    tool_call_parts = [
                        ToolCallPart(
                            tool_call_id=item.id,
                            tool_name=item.name,
                            args=item.input,
                        )
                        for item in result.approval_items
                    ]
                    request_metadata = {
                        item.id: item.metadata
                        for item in result.approval_items
                        if item.metadata
                    }
                    # Persist approval requests to DB (atomic with chat messages)
                    await self.approvals.prepare(
                        tool_call_parts,
                        request_metadata=request_metadata,
                    )
                if self._approval_stream_v2:
                    await self._emit_approval_pause_done()
                # Wait for either approval decisions or a user cancellation.
                await workflow.wait_condition(
                    lambda: self.approvals.is_ready() or self._cancel_requested
                )
                if self._cancel_requested:
                    logger.info(
                        "Agent turn cancelled while waiting for approval",
                        session_id=self.session_id,
                        reason=self._cancel_reason,
                    )
                    self.approvals.set(
                        {
                            item.id: ToolDenied(
                                message="Cancelled while waiting for approval"
                            )
                            for item in result.approval_items or []
                        }
                    )
                    await self.approvals.handle_decisions()
                    return await self._cancelled_turn_output(
                        result, info, emit_cancelled=True
                    )
                # Persist approval decisions to DB (atomic with chat messages)
                await self.approvals.handle_decisions()
                if self._cancel_requested:
                    logger.info(
                        "Agent turn cancelled after approval decisions",
                        session_id=self.session_id,
                        reason=self._cancel_reason,
                    )
                    return await self._cancelled_turn_output(
                        result, info, emit_cancelled=True
                    )

                # Approval waits are unbounded. Tokens are turn-scoped, so resumed
                # user-MCP tool execution and the continuation need fresh tokens.
                if workflow.patched(REMINT_SCOPE_TOKENS_PATCH):
                    compiled_run = self._remint_scope_tokens(
                        compiled_run,
                        internal_tool_context=internal_tool_context,
                    )
                    llm_gateway_auth_token = mint_llm_token(
                        workspace_id=self.workspace_id,
                        organization_id=self.organization_id,
                        session_id=self.session_id,
                        model=cfg.model_name,
                        provider=cfg.model_provider,
                        catalog_id=cfg.catalog_id,
                        base_url=cfg.base_url,
                        model_settings=cfg.model_settings,
                        routes=compiled_run.llm_routes,
                    )

                # Execute approved tools and reconcile the SDK transcript.
                approved_tools, denied_tools = self._build_tool_lists_from_approvals(
                    result.approval_items or []
                )

                if self._cancel_requested:
                    logger.info(
                        "Agent turn cancelled before approved tool execution",
                        session_id=self.session_id,
                        reason=self._cancel_reason,
                    )
                    return await self._cancelled_turn_output(
                        result, info, emit_cancelled=True
                    )

                tool_results: list[ToolExecutionResult] = []
                cancelled_tool_call_ids: list[str] = []
                if approved_tools or denied_tools:
                    (
                        tool_results,
                        cancelled_tool_call_ids,
                    ) = await self._execute_and_reconcile_approved_tools(
                        approved_tools=approved_tools,
                        denied_tools=denied_tools,
                        registry_lock=root_registry_lock,
                        agent_allowed_actions=frozenset(
                            compiled_run.root.tool_definitions
                        ),
                        mcp_auth_token=compiled_run.root.mcp_auth_token,
                        # Post-approval: emit to the (possibly rotated) stream.
                        # set_approvals rotated self.active_stream_id when the
                        # rotation patch is active; otherwise it is the original.
                        active_stream_id=self.active_stream_id,
                    )
                    logger.info(
                        "Tool execution completed",
                        result_count=len(tool_results),
                        session_id=self.session_id,
                    )

                if self._cancel_requested:
                    # A cancel arrived during approved tool execution. The
                    # transcript has already been reconciled with cancelled
                    # tool_result entries, so end the turn here instead of
                    # resuming the executor. The executor result predates the
                    # approved-tool run, so carry the aborted tool ids
                    # explicitly or the marker misses them and the UI renders
                    # the cancelled rows as tool errors.
                    logger.info(
                        "Agent turn cancelled during approved tool execution",
                        session_id=self.session_id,
                        reason=self._cancel_reason,
                    )
                    return await self._cancelled_turn_output(
                        result,
                        info,
                        emit_cancelled=True,
                        extra_interrupted_tool_call_ids=cancelled_tool_call_ids,
                    )

                # Reload session metadata after reconciliation. Full SDK history
                # is loaded inside run_agent_activity.
                reload_result = await workflow.execute_activity(
                    load_session_activity,
                    LoadSessionInput(role=self.role, session_id=self.session_id),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )

                # Update executor input for resume. Reconcile has replaced the
                # interrupt artifacts with the real tool_result entry; the
                # runtime only sends a hidden continuation tick. Emit the resumed
                # model output to the (possibly rotated) post-approval stream.
                executor_input = AgentExecutorInput(
                    session_id=self.session_id,
                    workspace_id=self.workspace_id,
                    active_stream_id=self.active_stream_id,
                    curr_run_id=curr_run_id,
                    user_prompt=args.agent_args.user_prompt,
                    config=cfg,
                    role=self.role,
                    mcp_auth_token=compiled_run.root.mcp_auth_token,
                    llm_gateway_auth_token=llm_gateway_auth_token,
                    allowed_actions=allowed_actions,
                    subagents=compiled_run.sandbox_subagents,
                    sdk_session_id=reload_result.sdk_session_id,
                    sdk_session_data=reload_result.sdk_session_data,
                    defer_done_on_approval=self._approval_stream_v2,
                    is_fork=reload_result.is_fork,
                    is_approval_continuation=True,
                )
                self._turn += 1
                continue

            # Agent completed successfully
            output = _resolve_agent_output(
                output=result.output,
            )
            message_history = await self._load_terminal_message_history(result)
            return AgentOutput(
                output=output,
                message_history=message_history,
                duration=(datetime.now(UTC) - info.start_time).total_seconds(),
                usage=RunUsage(
                    requests=result.result_num_turns or 0,
                    input_tokens=(result.result_usage or {}).get("input_tokens", 0),
                    output_tokens=(result.result_usage or {}).get("output_tokens", 0),
                ),
                session_id=self.session_id,
            )

    async def _cancelled_turn_output(
        self,
        result: AgentExecutorResult,
        info: workflow.Info,
        *,
        emit_cancelled: bool,
        extra_interrupted_tool_call_ids: list[str] | None = None,
    ) -> AgentOutput:
        """Build the terminal output for a cancelled turn.

        The executor-cancel loopback already emitted the cancelled stream
        notice, so that path passes emit_cancelled=False and the activity only
        persists the timeline marker. Approval-wait cancels have not emitted
        yet and pass emit_cancelled=True. Either way the marker is persisted
        before loading history so the terminal history includes it.

        ``extra_interrupted_tool_call_ids`` carries tool calls aborted after
        the executor result was produced (the approved-tool run), which the
        result's own ``interrupted_tool_call_ids`` cannot know about.
        """
        interrupted_ids = list(result.interrupted_tool_call_ids or [])
        for tool_call_id in extra_interrupted_tool_call_ids or []:
            if tool_call_id not in interrupted_ids:
                interrupted_ids.append(tool_call_id)
        await self._emit_session_cancelled(
            emit_stream=emit_cancelled,
            interrupted_tool_call_ids=interrupted_ids or None,
        )
        message_history = await self._load_terminal_message_history(result)
        return AgentOutput(
            output=None,
            message_history=message_history,
            duration=(datetime.now(UTC) - info.start_time).total_seconds(),
            usage=RunUsage(requests=0, input_tokens=0, output_tokens=0),
            session_id=self.session_id,
        )

    async def _load_terminal_message_history(
        self,
        result: AgentExecutorResult,
    ) -> list[ChatMessage] | None:
        """Load terminal chat history in a replay-compatible way.

        Legacy histories may already contain a completed run_agent_activity
        result with messages populated. Preserve that payload and avoid
        scheduling another activity.

        If a legacy history has messages=None, it also lacks the patch marker
        for the new load_session_messages_activity command. In that case,
        workflow.patched(...) returns False during replay, so the workflow keeps
        the old behavior and returns no terminal history.

        New executions record the patch marker, schedule the message-loading
        activity, and replay through the same branch later.
        """
        if result.messages is not None:
            return result.messages

        if not workflow.patched(LOAD_TERMINAL_MESSAGE_HISTORY_PATCH):
            return None

        try:
            load_result = await workflow.execute_activity(
                load_session_messages_activity,
                LoadSessionMessagesInput(role=self.role, session_id=self.session_id),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as e:
            logger.warning(
                "Failed to load terminal agent message history",
                session_id=str(self.session_id),
                error=str(e),
            )
            return None

        if load_result.error is not None:
            logger.warning(
                "Terminal agent message history unavailable",
                session_id=str(self.session_id),
                error=load_result.error,
            )
        return load_result.messages

    def _build_tool_lists_from_approvals(
        self,
        approval_items: list,
    ) -> tuple[list[ApprovedToolCall], list[DeniedToolCall]]:
        """Build approved and denied tool lists from approval decisions.

        Uses the ApprovalManager's stored decisions to categorize each tool call.
        """
        approved: list[ApprovedToolCall] = []
        denied: list[DeniedToolCall] = []

        for item in approval_items:
            tool_call_id = item.id
            tool_name = item.name
            original_args = item.input or {}

            # Get the decision from ApprovalManager
            decision = self.approvals.get_decision(tool_call_id)

            if decision is None:
                # No decision found - treat as denied
                denied.append(
                    DeniedToolCall(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        reason="No approval decision received",
                    )
                )
            elif decision is True:
                # Simple approval - use original args
                approved.append(
                    ApprovedToolCall(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        args=original_args,
                    )
                )
            elif isinstance(decision, ToolApproved):
                # Approval with potential override args
                final_args = {**original_args, **(decision.override_args or {})}
                approved.append(
                    ApprovedToolCall(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        args=final_args,
                    )
                )
            elif isinstance(decision, ToolDenied):
                denied.append(
                    DeniedToolCall(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        reason=decision.message or "Tool denied by user",
                    )
                )
            elif decision is False:
                denied.append(
                    DeniedToolCall(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        reason="Tool denied by user",
                    )
                )

        logger.info(
            "Built tool lists from approvals",
            approved_count=len(approved),
            denied_count=len(denied),
        )

        return approved, denied

    async def _race_tool_activity_against_cancel[T](
        self, handle: workflow.ActivityHandle[T]
    ) -> T:
        """Await an approved tool activity, cancelling it on a user cancel.

        Mirrors the run_agent_activity cancel race: ActivityHandle is an
        asyncio.Task subclass so .done() is valid, and neither wait_condition
        nor the handle poll emits history commands, so this race stays
        replay-safe when no cancel occurs. A cancelled handle raises
        ActivityError with a CancelledError cause, handled by the caller.
        """
        await workflow.wait_condition(lambda: handle.done() or self._cancel_requested)
        if not handle.done():
            handle.cancel()
        return await handle

    async def _execute_and_reconcile_approved_tools(
        self,
        *,
        approved_tools: list[ApprovedToolCall],
        denied_tools: list[DeniedToolCall],
        registry_lock: RegistryLock,
        agent_allowed_actions: frozenset[str],
        mcp_auth_token: str,
        active_stream_id: uuid.UUID | None,
    ) -> tuple[list, list[str]]:
        """Returns the reconciled results and the tool call ids the user's
        cancel aborted (cancelled in flight or never started), so the caller
        can carry them on the cancelled marker for the UI."""
        logical_time = workflow.now()
        service_role = build_tracecat_mcp_role(
            workspace_id=self.role.workspace_id,
            organization_id=self.role.organization_id,
            user_id=self.role.user_id,
            scopes=self.role.scopes
            if workflow.patched(AGENT_EXECUTION_GRANT_PATCH)
            else None,
        )
        pending_results: list[PendingToolResult] = []
        cancelled_tool_call_ids: list[str] = []
        for index, tool_call in enumerate(approved_tools):
            if self._cancel_requested:
                # Stop launching new tool activities once a cancel arrives;
                # every not-yet-started tool still needs a tool_result entry.
                skipped_calls = approved_tools[index:]
                cancelled_tool_call_ids.extend(
                    skipped.tool_call_id for skipped in skipped_calls
                )
                pending_results.extend(
                    _cancelled_tool_result(skipped, started=False)
                    for skipped in skipped_calls
                )
                break
            remote_mcp_tool_name = _approved_user_mcp_tool_name(tool_call.tool_name)
            try:
                if remote_mcp_tool_name is not None:
                    raw_result = await self._race_tool_activity_against_cancel(
                        _start_remote_mcp_tool_call(
                            tool_call,
                            remote_tool_name=remote_mcp_tool_name,
                            mcp_auth_token=mcp_auth_token,
                        )
                    )
                    result = PendingToolResult(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        tool_input=tool_call.args,
                        raw_result=raw_result,
                    )
                else:
                    stored = await self._race_tool_activity_against_cancel(
                        _start_registry_tool_call(
                            tool_call,
                            registry_lock=registry_lock,
                            service_role=service_role,
                            logical_time=logical_time,
                            # The lock includes recursive template steps that are
                            # executable only as part of the approved template.
                            # The Agent grant contains only exposed tool names.
                            allowed_actions=agent_allowed_actions
                            if workflow.patched(AGENT_EXECUTION_GRANT_PATCH)
                            else None,
                        )
                    )
                    result = PendingToolResult(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        tool_input=tool_call.args,
                        stored_result=stored,
                    )
                pending_results.append(result)
            except ActivityError as e:
                if self._cancel_requested and isinstance(
                    e.cause, TemporalCancelledError
                ):
                    # The in-flight tool activity was cancelled by the user.
                    skipped_calls = approved_tools[index + 1 :]
                    cancelled_tool_call_ids.append(tool_call.tool_call_id)
                    cancelled_tool_call_ids.extend(
                        skipped.tool_call_id for skipped in skipped_calls
                    )
                    pending_results.append(
                        _cancelled_tool_result(tool_call, started=True)
                    )
                    pending_results.extend(
                        _cancelled_tool_result(skipped, started=False)
                        for skipped in skipped_calls
                    )
                    break
                pending_results.append(
                    PendingToolResult(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        tool_input=tool_call.args,
                        raw_result=f"Tool execution failed: {_activity_error_message(e)}",
                        is_error=True,
                    )
                )

        for denied_tool in denied_tools:
            pending_results.append(
                PendingToolResult(
                    tool_call_id=denied_tool.tool_call_id,
                    tool_name=denied_tool.tool_name,
                    raw_result=f"Tool denied by user: {denied_tool.reason}",
                    is_error=True,
                )
            )

        reconcile = await workflow.execute_activity(
            reconcile_tool_results_activity,
            ReconcileToolResultsInput(
                session_id=self.session_id,
                workspace_id=self.workspace_id,
                role=self.role,
                pending_results=pending_results,
                active_stream_id=active_stream_id,
            ),
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        return reconcile.results, cancelled_tool_call_ids
