from __future__ import annotations

import uuid
from collections.abc import AsyncIterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings, RunContext, UsageLimits
from pydantic_ai.durable_exec.temporal import TemporalRunContext
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    ModelRequest,
)
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
)
from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from tracecat.agent.parsers import parse_output_type, try_parse_json
    from tracecat.agent.preset.activities import (
        ResolveAgentPresetConfigActivityInput,
        resolve_agent_preset_config_activity,
    )
    from tracecat.agent.schemas import AgentOutput, ModelInfo, RunAgentArgs, ToolFilters
    from tracecat.agent.stream.common import PersistableStreamingAgentDepsSpec
    from tracecat.agent.types import AgentConfig
    from tracecat.auth.types import Role
    from tracecat.contexts import ctx_role
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.feature_flags import FeatureFlag, is_feature_enabled
    from tracecat.logger import logger
    from tracecat_ee.agent.activities import (
        AgentActivities,
        BuildToolDefsArgs,
        EventStreamHandlerArgs,
    )
    from tracecat_ee.agent.approvals.service import ApprovalManager, ApprovalMap
    from tracecat_ee.agent.context import AgentContext
    from tracecat_ee.agent.durable import DurableModel
    from tracecat_ee.agent.toolset import RemoteToolset


class AgentWorkflowArgs(BaseModel):
    role: Role
    agent_args: RunAgentArgs


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
        # Probably want to keep a dict of this instead of overwritting it
        self._deferred_tool_results: DeferredToolResults = DeferredToolResults()
        self._turn: int = 0
        if args.role.workspace_id is None:
            raise ApplicationError("Role must have a workspace ID", non_retryable=True)
        self.workspace_id = args.role.workspace_id
        self.session_id = args.agent_args.session_id
        self.run_ctx_type = TemporalRunContext[PersistableStreamingAgentDepsSpec]
        self.approvals = ApprovalManager(role=self.role)
        self.max_requests = args.agent_args.max_requests
        self.max_tool_calls = args.agent_args.max_tool_calls
        self.usage_limits = UsageLimits(
            request_limit=self.max_requests,
            tool_calls_limit=self.max_tool_calls,
        )

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
            cfg = args.agent_args.config
            if cfg is None:
                raise ApplicationError(
                    "Config must be provided if preset_slug is not set",
                    non_retryable=True,
                )
        return cfg

    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        """Run the agent until completion. The agent will call tools until it needs human approval."""
        logger.info("DurableAgentWorkflow run", args=args)
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
        ext_toolset = await self._build_toolset(cfg)
        logger.debug("TOOLSET", toolset=ext_toolset)

        messages: list[ModelMessage] = [
            ModelRequest.user_text_prompt(args.agent_args.user_prompt)
        ]

        # Build model settings from agent config
        model_settings: ModelSettings | None = (
            ModelSettings(**cfg.model_settings) if cfg.model_settings else None
        )

        model = DurableModel(
            role=args.role,
            info=ModelInfo(
                name=cfg.model_name,
                provider=cfg.model_provider,
                base_url=cfg.base_url,
            ),
            activity_config=workflow.ActivityConfig(
                start_to_close_timeout=timedelta(seconds=300),
                retry_policy=RETRY_POLICIES["activity:fail_fast"],
            ),
            deps_type=PersistableStreamingAgentDepsSpec,
        )
        logger.debug("DURABLE MODEL", model_info=model._info)

        # Determine base output type from config
        base_output_type = parse_output_type(cfg.output_type)

        # Always allow DeferredToolRequests so approvals can flow through
        output_type_for_agent: list[type[Any]] = [
            base_output_type,
            DeferredToolRequests,
        ]

        # Use configured instructions when available
        instructions = cfg.instructions or "You are a helpful assistant."

        agent = Agent(
            model,
            name="durable-agent",
            output_type=output_type_for_agent,
            instructions=instructions,
            model_settings=model_settings,
            retries=cfg.retries,
            deps_type=PersistableStreamingAgentDepsSpec,
            event_stream_handler=self._event_stream_handler,
            toolsets=[ext_toolset],
        )

        deps = PersistableStreamingAgentDepsSpec(
            session_id=self.session_id,
            workspace_id=self.workspace_id,
            persistent=False,
            namespace="agent",
        )

        # TODO: Implement max turns
        while True:
            logger.info("Running agent", turn=self._turn)
            result = await agent.run(
                message_history=messages,
                deferred_tool_results=self.approvals.get(),
                deps=deps,
                usage_limits=self.usage_limits,
            )
            logger.debug("AGENT RUN RESULT", result=result)

            # perf: Can probably early exit here if the result is not a DeferredToolRequests
            messages = result.all_messages()

            match result.output:
                case DeferredToolRequests(approvals=approvals):
                    logger.info(
                        "Waiting for tool results",
                        turn=self._turn,
                        approvals=approvals,
                    )
                    # If there are approvals, we need to wait for the tool results
                    if approvals:
                        await self.approvals.prepare(approvals)
                        # Wait for the approval results
                        await self.approvals.wait()
                        logger.info(
                            "Tool results", deferred_tool_results=self.approvals.get()
                        )
                        await self.approvals.handle_decisions()
                    # The next run() of the workflow will handle the tool calls
                    # depending on the results of the approvals
                case _:
                    info = workflow.info()
                    return AgentOutput(
                        output=try_parse_json(result.output),
                        message_history=result.all_messages(),
                        duration=(datetime.now(UTC) - info.start_time).total_seconds(),
                        usage=result.usage(),
                        session_id=self.session_id,
                    )
            self._turn += 1

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

    async def _build_toolset(
        self, config: AgentConfig
    ) -> RemoteToolset[PersistableStreamingAgentDepsSpec]:
        build_tool_defs_result = await workflow.execute_activity_method(
            AgentActivities.build_tool_definitions,
            arg=BuildToolDefsArgs(
                tool_filters=ToolFilters(
                    namespaces=config.namespaces,
                    actions=config.actions,
                ),
                tool_approvals=config.tool_approvals,
            ),
            start_to_close_timeout=timedelta(seconds=120),
        )
        return RemoteToolset(build_tool_defs_result.tool_definitions, role=self.role)

    async def _event_stream_handler(
        self,
        ctx: RunContext[PersistableStreamingAgentDepsSpec],
        events: AsyncIterable[AgentStreamEvent],
    ) -> None:
        logger.info(
            "WF Event stream handler",
            in_activity=activity.in_activity(),
            events_type=type(events),
        )
        serialized_run_context = self.run_ctx_type.serialize_run_context(ctx)
        async for event in events:
            try:
                await workflow.execute_activity_method(
                    AgentActivities.event_stream_handler,
                    args=(
                        EventStreamHandlerArgs(
                            event=event,
                            serialized_run_context=serialized_run_context,
                        ),
                        ctx.deps,
                    ),
                    start_to_close_timeout=timedelta(seconds=60),
                )
            except CancelledError:
                # Re-raise cancellation to allow workflow to terminate properly
                raise
            except Exception as e:
                # Streaming is non-critical - log the error but continue processing
                # This ensures agent execution completes even if streaming fails
                logger.warning(
                    "Failed to stream event, continuing agent execution",
                    error=str(e),
                    error_type=type(e).__name__,
                    event_type=type(event).__name__,
                )
