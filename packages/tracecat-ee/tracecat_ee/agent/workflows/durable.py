from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolApproved, ToolDenied

    from tracecat.agent.common.stream_types import HarnessType
    from tracecat.agent.executor.activity import (
        AgentExecutorInput,
        ApprovedToolCall,
        DeniedToolCall,
        ExecuteApprovedToolsInput,
        execute_approved_tools_activity,
        run_agent_activity,
    )
    from tracecat.agent.preset.activities import (
        ResolveAgentPresetConfigActivityInput,
        resolve_agent_preset_config_activity,
    )
    from tracecat.agent.schemas import AgentOutput, RunAgentArgs, RunUsage, ToolFilters
    from tracecat.agent.session.activities import (
        CreateSessionInput,
        LoadSessionInput,
        create_session_activity,
        load_session_activity,
    )
    from tracecat.agent.session.types import AgentSessionEntity
    from tracecat.agent.tokens import (
        InternalToolContext,
        mint_llm_token,
        mint_mcp_token,
    )
    from tracecat.agent.types import AgentConfig
    from tracecat.auth.types import Role
    from tracecat.contexts import ctx_role
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.feature_flags import FeatureFlag, is_feature_enabled
    from tracecat.logger import logger
    from tracecat.registry.lock.types import RegistryLock
    from tracecat_ee.agent.activities import (
        AgentActivities,
        BuildToolDefsArgs,
    )
    from tracecat_ee.agent.approvals.service import ApprovalManager, ApprovalMap
    from tracecat_ee.agent.context import AgentContext


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
    harness_type: HarnessType | None = Field(
        default=None,
        description="Agent harness type. Reserved for future multi-harness support.",
    )


class WorkflowApprovalSubmission(BaseModel):
    approvals: ApprovalMap
    approved_by: uuid.UUID | None = None


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
        # Registry lock for action resolution (set after build_tool_definitions)
        self._registry_lock: RegistryLock | None = None

    async def _build_config(self, args: AgentWorkflowArgs) -> AgentConfig:
        if args.agent_args.preset_slug:
            preset_config = await workflow.execute_activity(
                resolve_agent_preset_config_activity,
                ResolveAgentPresetConfigActivityInput(
                    role=self.role, preset_slug=args.agent_args.preset_slug
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            )
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
        return cfg

    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        """Run the agent until completion. The agent will call tools until it needs human approval."""
        logger.info(
            "DurableAgentWorkflow run", args=args, harness_type=self.harness_type
        )
        logger.debug("AGENT CONTEXT", agent_context=AgentContext.get())
        if workflow.unsafe.is_replaying():
            logger.info("Workflow is replaying")
        else:
            logger.info("Starting agent", prompt=args.agent_args.user_prompt)

        cfg = await self._build_config(args)

        # Enforce feature flag for tool approvals
        if cfg.tool_approvals and not is_feature_enabled(FeatureFlag.AGENT_APPROVALS):
            raise ApplicationError(
                "`tool_approvals` requires the 'agent-approvals' feature flag to be enabled.",
                non_retryable=True,
            )

        # Run with NSJail harness (only supported harness currently)
        return await self._run_with_nsjail(args, cfg)

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> None:
        submission = WorkflowApprovalSubmission.model_validate(submission)
        logger.info(
            "Setting approvals",
            approvals=submission.approvals,
            approved_by=submission.approved_by,
        )
        self.approvals.set(submission.approvals, approved_by=submission.approved_by)

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

    async def _run_with_nsjail(
        self, args: AgentWorkflowArgs, cfg: AgentConfig
    ) -> AgentOutput:
        """Run the agent using NSJail-sandboxed Claude SDK execution.

        This path:
        1. Resolves tool definitions from registry
        2. Loads session history from DB (for resume)
        3. Mints JWT/LiteLLM tokens
        4. Calls run_agent_executor_activity which spawns NSJail
        5. Persists session history after execution
        6. Handles approval requests
        """
        logger.info("Running agent with NSJail harness", session_id=self.session_id)

        # Create or get the AgentSession - idempotent, safe to call on resume
        # Pass session_id as curr_run_id since the workflow ID is agent/<session_id>
        create_result = await workflow.execute_activity(
            create_session_activity,
            CreateSessionInput(
                role=self.role,
                session_id=self.session_id,
                title=args.title,
                created_by=self.role.user_id,
                entity_type=args.entity_type,
                entity_id=args.entity_id,
                tools=args.tools,
                agent_preset_id=args.agent_preset_id,
                harness_type=HarnessType(self.harness_type),
                curr_run_id=self.session_id,
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

        # Resolve tool definitions and registry lock from registry
        # Also discovers user MCP tools if configured
        build_result = await workflow.execute_activity_method(
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
        allowed_actions = build_result.tool_definitions
        self._registry_lock = build_result.registry_lock
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

        # Mint tokens for MCP server and LiteLLM gateway auth
        # These tokens are opaque to the jailed runtime - it cannot decode them
        mcp_auth_token = mint_mcp_token(
            workspace_id=self.workspace_id,
            organization_id=self.organization_id,
            user_id=self.role.user_id,
            allowed_actions=list(allowed_actions.keys()),
            session_id=self.session_id,
            user_mcp_servers=user_mcp_claims,
            allowed_internal_tools=allowed_internal_tools,
            internal_tool_context=internal_tool_context,
        )
        litellm_auth_token = mint_llm_token(
            workspace_id=self.workspace_id,
            organization_id=self.organization_id,
            session_id=self.session_id,
            model=cfg.model_name,
            provider=cfg.model_provider,
            model_settings=cfg.model_settings,
            use_workspace_credentials=args.agent_args.use_workspace_credentials,
        )

        # Prepare executor input
        executor_input = AgentExecutorInput(
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            user_prompt=args.agent_args.user_prompt,
            config=cfg,
            role=self.role,
            mcp_auth_token=mcp_auth_token,
            litellm_auth_token=litellm_auth_token,
            allowed_actions=allowed_actions,
            sdk_session_id=self._sdk_session_id,
            sdk_session_data=self._sdk_session_data,
            is_fork=is_fork,
        )

        info = workflow.info()

        # Run the NSJail executor activity
        while True:
            logger.info("Executing NSJail agent", turn=self._turn)

            result = await workflow.execute_activity(
                run_agent_activity,
                executor_input,
                start_to_close_timeout=timedelta(seconds=600),
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
                    # Persist approval requests to DB (atomic with chat messages)
                    await self.approvals.prepare(tool_call_parts)
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
                    if self._registry_lock is None:
                        raise ApplicationError(
                            "Registry lock not initialized",
                            non_retryable=True,
                        )
                    tool_exec_result = await workflow.execute_activity(
                        execute_approved_tools_activity,
                        ExecuteApprovedToolsInput(
                            session_id=self.session_id,
                            workspace_id=self.workspace_id,
                            role=self.role,
                            approved_tools=approved_tools,
                            denied_tools=denied_tools,
                            allowed_actions=list(allowed_actions.keys()),
                            registry_lock=self._registry_lock,
                        ),
                        start_to_close_timeout=timedelta(seconds=300),
                        heartbeat_timeout=timedelta(seconds=60),
                        retry_policy=RETRY_POLICIES["activity:fail_fast"],
                    )
                    tool_results = tool_exec_result.results
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
                    litellm_auth_token=litellm_auth_token,
                    allowed_actions=allowed_actions,
                    sdk_session_id=self._sdk_session_id,
                    sdk_session_data=self._sdk_session_data,
                    is_approval_continuation=True,
                )
                self._turn += 1
                continue

            # Agent completed successfully
            return AgentOutput(
                output=result.structured_output,
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
