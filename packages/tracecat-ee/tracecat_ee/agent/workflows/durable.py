from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import TypedSearchAttributes
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolApproved, ToolDenied

    from tracecat import config
    from tracecat.agent.common.stream_types import HarnessType
    from tracecat.agent.common.types import SandboxAgentConfig, SandboxSubagentConfig
    from tracecat.agent.executor.activity import (
        AgentExecutorInput,
        ApprovedToolCall,
        DeniedToolCall,
        run_agent_activity,
    )
    from tracecat.agent.llm_routing import get_scoped_litellm_route_model
    from tracecat.agent.mcp.executor import (
        AGENT_TOOL_PRIORITY,
        build_run_input,
        build_tracecat_mcp_role,
    )
    from tracecat.agent.mcp.metadata import strip_proxy_tool_metadata
    from tracecat.agent.mcp.utils import normalize_mcp_tool_name
    from tracecat.agent.parsers import try_parse_json
    from tracecat.agent.preset.activities import (
        ResolveAgentPresetConfigActivityInput,
        ResolveAgentsConfigActivityInput,
        ResolveAgentsConfigActivityResult,
        ResolvedSubagentConfig,
        resolve_agent_preset_config_activity,
        resolve_agents_config_activity,
        resolve_custom_model_provider_config_activity,
    )
    from tracecat.agent.schemas import AgentOutput, RunAgentArgs, RunUsage, ToolFilters
    from tracecat.agent.session.activities import (
        CreateSessionInput,
        LoadSessionInput,
        PendingToolResult,
        ReconcileToolResultsInput,
        create_session_activity,
        load_session_activity,
        reconcile_tool_results_activity,
    )
    from tracecat.agent.session.types import AgentSessionEntity
    from tracecat.agent.subagents import ResolvedAgentsConfig
    from tracecat.agent.tokens import (
        InternalToolContext,
        LLMRouteClaim,
        mint_llm_token,
        mint_mcp_token,
    )
    from tracecat.agent.types import AgentConfig
    from tracecat.agent.workflow_config import agent_config_from_payload
    from tracecat.auth.types import Role
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
        BuildToolDefsResult,
    )
    from tracecat_ee.agent.approvals.service import ApprovalManager, ApprovalMap
    from tracecat_ee.agent.context import AgentContext
    from tracecat_ee.agent.types import AgentWorkflowID


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
    )


def _merge_registry_locks(*locks: RegistryLock) -> RegistryLock:
    origins: dict[str, str] = {}
    actions: dict[str, str] = {}
    for lock in locks:
        for origin, version in lock.origins.items():
            if origin in origins and origins[origin] != version:
                raise ApplicationError(
                    f"Conflicting registry lock versions for origin '{origin}'",
                    non_retryable=True,
                )
            origins[origin] = version
        actions.update(lock.actions)
    return RegistryLock(origins=origins, actions=actions)


def _llm_route_for_config(
    cfg: AgentConfig,
    *,
    scope: str,
    use_workspace_credentials: bool,
) -> tuple[str, LLMRouteClaim]:
    route_model = get_scoped_litellm_route_model(
        model_provider=cfg.model_provider,
        model_name=cfg.model_name,
        passthrough=cfg.passthrough,
        scope=scope,
    )
    return route_model, LLMRouteClaim(
        model=cfg.model_name,
        provider=cfg.model_provider,
        base_url=cfg.base_url,
        model_settings=cfg.model_settings or {},
        use_workspace_credentials=use_workspace_credentials,
    )


class AgentWorkflowArgs(BaseModel):
    """Arguments for starting an agent workflow."""

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
        default=None, description="Pinned preset version used for this session"
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
        self.harness_type = args.harness_type or "claude_code"
        self.approvals = ApprovalManager(role=self.role)
        self.max_requests = args.agent_args.max_requests
        self.max_tool_calls = args.agent_args.max_tool_calls
        # Session state for Claude SDK resume
        self._sdk_session_id: str | None = None
        self._sdk_session_data: str | None = None
        # Registry lock for action resolution (set after tool compilation)
        self._registry_lock: RegistryLock | None = None

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
        *,
        use_workspace_credentials: bool,
    ) -> None:
        if cfg.model_provider != "custom-model-provider":
            return
        result = await workflow.execute_activity(
            resolve_custom_model_provider_config_activity,
            args=(
                self.role,
                use_workspace_credentials,
            ),
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
            use_workspace_credentials=use_workspace_credentials,
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

        await self._apply_custom_model_provider_config(
            cfg,
            use_workspace_credentials=args.agent_args.use_workspace_credentials,
        )
        return cfg

    async def _resolve_agents_config(
        self,
        args: AgentWorkflowArgs,
        cfg: AgentConfig,
    ) -> ResolveAgentsConfigActivityResult:
        if not cfg.agents.enabled:
            return ResolveAgentsConfigActivityResult()
        if not cfg.agents.subagents:
            return ResolveAgentsConfigActivityResult(
                agents_binding=ResolvedAgentsConfig(enabled=True)
            )
        return await workflow.execute_activity(
            resolve_agents_config_activity,
            ResolveAgentsConfigActivityInput(
                role=self.role,
                agents=cfg.agents,
                parent_preset_id=args.agent_preset_id,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )

    async def _compile_agent_scopes(
        self,
        *,
        cfg: AgentConfig,
        subagents: list[ResolvedSubagentConfig],
        internal_tool_context: InternalToolContext | None,
        use_workspace_credentials: bool,
    ) -> tuple[
        BuildToolDefsResult,
        RegistryLock,
        list[SandboxSubagentConfig],
        dict[str, LLMRouteClaim],
    ]:
        scope_args = [
            BuildAgentScopeToolDefsArgs(
                scope="root",
                tool_filters=ToolFilters(
                    namespaces=cfg.namespaces,
                    actions=cfg.actions,
                ),
                tool_approvals=cfg.tool_approvals,
                mcp_servers=cfg.mcp_servers,
                internal_tool_context=internal_tool_context,
            )
        ]
        prepared_subagents: list[tuple[ResolvedSubagentConfig, AgentConfig]] = []
        for subagent in subagents:
            child_cfg = agent_config_from_payload(subagent.config)
            await self._apply_custom_model_provider_config(
                child_cfg,
                use_workspace_credentials=use_workspace_credentials,
            )
            prepared_subagents.append((subagent, child_cfg))
            scope_args.append(
                BuildAgentScopeToolDefsArgs(
                    scope=subagent.alias,
                    tool_filters=ToolFilters(
                        namespaces=child_cfg.namespaces,
                        actions=child_cfg.actions,
                    ),
                    tool_approvals=child_cfg.tool_approvals,
                    mcp_servers=child_cfg.mcp_servers,
                    fail_on_mcp_discovery_error=True,
                )
            )

        try:
            build_result = await workflow.execute_activity_method(
                AgentActivities.build_agent_tool_definitions,
                arg=BuildAgentToolDefsArgs(
                    role=self.role,
                    scopes=scope_args,
                ),
                start_to_close_timeout=timedelta(seconds=120 * max(1, len(scope_args))),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
        except ActivityError as e:
            if isinstance(e.cause, ApplicationError):
                raise e.cause from e
            raise

        root_build_result = build_result.scopes.get("root")
        if root_build_result is None:
            raise ApplicationError(
                "Batched agent tool compilation did not return the root scope",
                non_retryable=True,
            )

        compiled: list[SandboxSubagentConfig] = []
        registry_locks = [root_build_result.registry_lock]
        llm_routes: dict[str, LLMRouteClaim] = {}

        info = workflow.info()
        for subagent, child_cfg in prepared_subagents:
            child_build_result = build_result.scopes.get(subagent.alias)
            if child_build_result is None:
                raise ApplicationError(
                    f"Batched agent tool compilation did not return scope '{subagent.alias}'",
                    non_retryable=True,
                )
            route_model, route_claim = _llm_route_for_config(
                child_cfg,
                scope=subagent.alias,
                use_workspace_credentials=use_workspace_credentials,
            )
            llm_routes[route_model] = route_claim

            mcp_auth_token = mint_mcp_token(
                workspace_id=self.workspace_id,
                organization_id=self.organization_id,
                user_id=self.role.user_id,
                allowed_actions=list(child_build_result.tool_definitions.keys()),
                session_id=self.session_id,
                parent_agent_workflow_id=info.workflow_id,
                parent_agent_run_id=info.run_id,
                user_mcp_servers=child_build_result.user_mcp_claims,
                allowed_internal_tools=child_build_result.allowed_internal_tools,
            )
            compiled.append(
                SandboxSubagentConfig(
                    alias=subagent.alias,
                    description=subagent.description,
                    prompt=subagent.prompt,
                    max_turns=subagent.max_turns,
                    config=SandboxAgentConfig.from_agent_config(child_cfg),
                    mcp_auth_token=mcp_auth_token,
                    allowed_actions=child_build_result.tool_definitions,
                )
            )
            registry_locks.append(child_build_result.registry_lock)

        return (
            root_build_result,
            _merge_registry_locks(*registry_locks),
            compiled,
            llm_routes,
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

        cfg = await self._build_config(args)

        # Run through the agent executor (only supported harness currently)
        return await self._run_with_agent_executor(args, cfg)

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> None:
        submission = WorkflowApprovalSubmission.model_validate(submission)
        logger.info(
            "Setting approvals",
            approvals=submission.approvals,
            approved_by=submission.approved_by,
        )
        self.approvals.set(
            submission.approvals,
            approved_by=submission.approved_by,
            decision_metadata=submission.decision_metadata,
        )

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
                agents_binding=agents_result.agents_binding,
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
        # preserving partitioned outputs for scope-specific tokens and locks.
        (
            build_result,
            self._registry_lock,
            compiled_subagents,
            subagent_llm_routes,
        ) = await self._compile_agent_scopes(
            cfg=cfg,
            subagents=agents_result.subagents,
            internal_tool_context=internal_tool_context,
            use_workspace_credentials=args.agent_args.use_workspace_credentials,
        )
        allowed_actions = build_result.tool_definitions
        user_mcp_claims = build_result.user_mcp_claims
        allowed_internal_tools = build_result.allowed_internal_tools

        logger.debug(
            "Resolved tool definitions",
            action_count=len(allowed_actions),
            actions=list(allowed_actions.keys()),
            registry_lock_origins=list(self._registry_lock.origins.keys()),
        )

        # Load existing session state for resume
        load_result = await workflow.execute_activity(
            load_session_activity,
            LoadSessionInput(role=self.role, session_id=self.session_id),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )

        is_fork = False
        if load_result.found and load_result.sdk_session_data:
            self._sdk_session_id = load_result.sdk_session_id
            self._sdk_session_data = load_result.sdk_session_data
            is_fork = load_result.is_fork
            logger.info(
                "Resuming from existing session",
                sdk_session_id=self._sdk_session_id,
                is_fork=is_fork,
            )

        # Mint tokens for MCP server and LLM gateway auth
        # These tokens are opaque to the jailed runtime - it cannot decode them
        info = workflow.info()
        mcp_auth_token = mint_mcp_token(
            workspace_id=self.workspace_id,
            organization_id=self.organization_id,
            user_id=self.role.user_id,
            allowed_actions=list(allowed_actions.keys()),
            session_id=self.session_id,
            parent_agent_workflow_id=info.workflow_id,
            parent_agent_run_id=info.run_id,
            user_mcp_servers=user_mcp_claims,
            allowed_internal_tools=allowed_internal_tools,
            internal_tool_context=internal_tool_context,
        )
        root_route_model, root_route_claim = _llm_route_for_config(
            cfg,
            scope="root",
            use_workspace_credentials=args.agent_args.use_workspace_credentials,
        )
        llm_routes = {
            root_route_model: root_route_claim,
            **subagent_llm_routes,
        }

        llm_gateway_auth_token = mint_llm_token(
            workspace_id=self.workspace_id,
            organization_id=self.organization_id,
            session_id=self.session_id,
            model=cfg.model_name,
            provider=cfg.model_provider,
            base_url=cfg.base_url,
            model_settings=cfg.model_settings,
            use_workspace_credentials=args.agent_args.use_workspace_credentials,
            routes=llm_routes,
        )

        # Prepare executor input
        executor_input = AgentExecutorInput(
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            user_prompt=args.agent_args.user_prompt,
            config=cfg,
            role=self.role,
            mcp_auth_token=mcp_auth_token,
            llm_gateway_auth_token=llm_gateway_auth_token,
            allowed_actions=allowed_actions,
            subagents=compiled_subagents,
            sdk_session_id=self._sdk_session_id,
            sdk_session_data=self._sdk_session_data,
            is_fork=is_fork,
            use_workspace_credentials=args.agent_args.use_workspace_credentials,
        )

        info = workflow.info()

        # Run the executor activity
        while True:
            logger.info("Executing agent turn", turn=self._turn)

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

            if not result.success:
                raise ApplicationError(
                    f"Agent execution failed: {result.error}",
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
                # Wait for approval signal
                await self.approvals.wait()
                # Persist approval decisions to DB (atomic with chat messages)
                await self.approvals.handle_decisions()

                # Execute approved tools and collect results
                approved_tools, denied_tools = self._build_tool_lists_from_approvals(
                    result.approval_items or []
                )

                tool_results = None
                if approved_tools or denied_tools:
                    tool_results = await self._execute_and_reconcile_approved_tools(
                        approved_tools=approved_tools,
                        denied_tools=denied_tools,
                    )
                    logger.info(
                        "Tool execution completed",
                        result_count=len(tool_results),
                        session_id=self.session_id,
                    )

                # Reload session data from DB to get lines persisted during previous turn
                reload_result = await workflow.execute_activity(
                    load_session_activity,
                    LoadSessionInput(role=self.role, session_id=self.session_id),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                )
                if reload_result.found and reload_result.sdk_session_data:
                    self._sdk_session_id = reload_result.sdk_session_id
                    self._sdk_session_data = reload_result.sdk_session_data
                    logger.info(
                        "Reloaded session data for continuation",
                        sdk_session_id=self._sdk_session_id,
                    )

                # Update executor input for resume (history now has proper tool_result)
                executor_input = AgentExecutorInput(
                    session_id=self.session_id,
                    workspace_id=self.workspace_id,
                    user_prompt=args.agent_args.user_prompt,
                    config=cfg,
                    role=self.role,
                    mcp_auth_token=mcp_auth_token,
                    llm_gateway_auth_token=llm_gateway_auth_token,
                    allowed_actions=allowed_actions,
                    subagents=compiled_subagents,
                    sdk_session_id=self._sdk_session_id,
                    sdk_session_data=self._sdk_session_data,
                    is_approval_continuation=True,
                    use_workspace_credentials=args.agent_args.use_workspace_credentials,
                )
                self._turn += 1
                continue

            # Agent completed successfully
            output = _resolve_agent_output(
                output=result.output,
            )
            return AgentOutput(
                output=output,
                message_history=result.messages,  # Messages fetched from DB by activity
                duration=(datetime.now(UTC) - info.start_time).total_seconds(),
                usage=RunUsage(
                    requests=result.result_num_turns or 0,
                    input_tokens=(result.result_usage or {}).get("input_tokens", 0),
                    output_tokens=(result.result_usage or {}).get("output_tokens", 0),
                ),
                session_id=self.session_id,
            )

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

    async def _execute_and_reconcile_approved_tools(
        self,
        *,
        approved_tools: list[ApprovedToolCall],
        denied_tools: list[DeniedToolCall],
    ) -> list:
        if self._registry_lock is None:
            raise ApplicationError(
                "Registry lock not initialized",
                non_retryable=True,
            )

        logical_time = workflow.now()
        service_role = build_tracecat_mcp_role(
            workspace_id=self.role.workspace_id,
            organization_id=self.role.organization_id,
            user_id=self.role.user_id,
        )
        pending_results: list[PendingToolResult] = []
        for tool_call in approved_tools:
            try:
                stored = await workflow.execute_activity(
                    ExecutorActivities.execute_action_activity,
                    args=[
                        _build_approved_tool_run_input(
                            tool_call=tool_call,
                            registry_lock=self._registry_lock,
                            workflow_id=workflow.uuid4(),
                            run_id=workflow.uuid4(),
                            execution_id=workflow.uuid4(),
                            logical_time=logical_time,
                        ),
                        service_role,
                    ],
                    task_queue=config.TRACECAT__EXECUTOR_QUEUE,
                    start_to_close_timeout=timedelta(
                        seconds=int(config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT)
                    ),
                    heartbeat_timeout=timedelta(
                        seconds=config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT
                    )
                    if config.TRACECAT__ACTIVITY_HEARTBEAT_TIMEOUT > 0
                    else None,
                    retry_policy=RETRY_POLICIES["activity:fail_fast"],
                    priority=AGENT_TOOL_PRIORITY,
                )
                pending_results.append(
                    PendingToolResult(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
                        stored_result=stored,
                    )
                )
            except ActivityError as e:
                pending_results.append(
                    PendingToolResult(
                        tool_call_id=tool_call.tool_call_id,
                        tool_name=tool_call.tool_name,
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
            ),
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=RETRY_POLICIES["activity:fail_fast"],
        )
        return reconcile.results
