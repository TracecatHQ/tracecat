from __future__ import annotations as _annotations

import textwrap
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    # NOTE: These imports are required to avoid workflow sandbox restrictions.
    import httpcore  # noqa: F401
    import httpx  # noqa: F401
    import pydantic_ai  # noqa: F401
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.agent import AgentRunResult
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        ModelMessage,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models import Model
    from pydantic_ai.tools import (
        AgentDepsT,
    )
    from tracecat_registry.integrations.agents.tools import (
        create_tool_call,
        create_tool_return,
    )
    from tracecat_registry.integrations.pydantic_ai import build_agent

    from tracecat.contexts import ctx_role
    from tracecat.ee.agent.activities import (
        AgentActivities,
        BuildToolDefinitionsArgs,
        WriteModelRequestArgs,
        build_tool_definitions,
    )

    # Rest of imports
    from tracecat.ee.agent.approvals import (
        ApprovalAction,
        ApprovalRequest,
        ApprovalResponse,
        ApprovalState,
        ApprovalStatus,
    )
    from tracecat.ee.agent.context import AgentContext
    from tracecat.ee.agent.core import DurableGatedTool, DurableModel
    from tracecat.ee.agent.models import (
        GraphAgentWorkflowArgs,
        GraphAgentWorkflowResult,
        ToolFilters,
    )
    from tracecat.logger import logger


@workflow.defn
class GraphAgentWorkflow:
    """Executes an agentic chat turn using pydantic-ai Agent."""

    @workflow.init
    def __init__(self, args: GraphAgentWorkflowArgs) -> None:
        self.pending_approvals: dict[str, ApprovalState] = {}

    @workflow.update
    async def approval_handler(self, input: ApprovalRequest) -> ApprovalResponse:
        """Handle interactions from the workflow and return a result."""
        status_map = {
            ApprovalAction.APPROVE: ApprovalStatus.APPROVED,
            ApprovalAction.REJECT: ApprovalStatus.REJECTED,
        }
        status = status_map[input.action]
        self.pending_approvals[input.call_id].status = status
        return ApprovalResponse(status=status)

    @approval_handler.validator
    def validate_approval_handler(self, input: ApprovalRequest) -> None:
        """Validate the approval handler."""
        if input.call_id not in self.pending_approvals:
            raise RuntimeError(f"Call ID {input.call_id} not found")

    # Define how the workflow waits for human approval
    async def approval_gate(
        self,
        message: ToolCallPart,
        run_context: RunContext[AgentDepsT],
    ) -> bool:
        """
        Hook that gets called before the tool call.

        This gate function is invoked prior to executing any tool call,
        allowing for human-in-the-loop approval workflow.

        Args:
            message: The tool call message containing tool name and arguments
            run_context: The current run context with HITLDeps

        Returns:
            bool: True if the tool call should proceed, False otherwise
        """
        tool_name = message.tool_name
        tool_args = message.args
        call_id = f"{tool_name}_{workflow.uuid4().hex}"

        logger.info(
            "Creating approval state",
            tool_name=tool_name,
            tool_args=tool_args,
            call_id=call_id,
        )
        self.pending_approvals[call_id] = ApprovalState(
            status=ApprovalStatus.PENDING,
            tool_call_part=message,
        )
        # expose to UI via workflow state or signal
        await workflow.wait_condition(
            lambda: self.pending_approvals[call_id].status == ApprovalStatus.APPROVED
        )
        return True  # proceed

    @workflow.run
    async def run(self, args: GraphAgentWorkflowArgs) -> GraphAgentWorkflowResult:
        ctx_role.set(args.role)
        AgentContext.set(session_id=args.session_id)

        tool_filters = args.tool_filters or ToolFilters.default()
        build_res = await workflow.execute_activity(
            build_tool_definitions,
            BuildToolDefinitionsArgs(tool_filters=tool_filters),
            start_to_close_timeout=timedelta(seconds=10),
        )

        # Wrap every tool with HITLTool
        # NOTE: These should just be stubs, not actual callable tools
        self.tools = [
            DurableGatedTool(defn, self.approval_gate)
            for defn in build_res.tool_definitions
        ]

        # Build agent instructions with defaults and enhancements
        default_instructions = "You are a helpful assistant."

        if args.instructions:
            # Enhance provided instructions with context
            current_date_prompt = (
                f"<current_date>{datetime.now().strftime('%Y-%m-%d')}</current_date>"
            )
            error_handling_prompt = textwrap.dedent("""
                <error_handling>
                - When you encounter errors or missing information, be specific about what's needed
                - Stop execution immediately if critical information is missing
                - Don't attempt workarounds or make assumptions without user confirmation
                </error_handling>
            """).strip()

            instructions = (
                f"{args.instructions}\n{current_date_prompt}\n{error_handling_prompt}"
            )
        else:
            instructions = default_instructions

        agent = build_agent(
            model=DurableModel(args.model_info),
            instructions=instructions,
            tools=[t.tool for t in self.tools],
        )
        if not isinstance(agent.model, Model):
            raise ApplicationError("Model is None")
        agent_ctx = AgentContext.get()
        logger.info("Agent built", agent=agent, agent_ctx=agent_ctx)

        # Track messages in-process for diagnostics
        messages: list[ModelMessage] = []
        async with agent.iter(user_prompt=args.user_prompt) as run:
            async for node in run:
                curr: ModelMessage
                if Agent.is_user_prompt_node(node):
                    continue  # handle this in the next node

                # 1️⃣  Model request (may be a normal user/tool-return message)
                elif Agent.is_model_request_node(node):
                    curr = node.request
                    logger.info("Model request", curr=curr)

                    # If this request is ONLY a tool-return we have
                    # already streamed it via FunctionToolResultEvent.
                    # if any(isinstance(p, ToolReturnPart) for p in curr.parts):
                    #     messages.append(curr)  # keep history
                    #     continue  # ← skip duplicate stream

                    await workflow.execute_activity_method(
                        AgentActivities.write_model_request,
                        args=(WriteModelRequestArgs(request=curr), agent_ctx),
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                # assistant tool-call + tool-return events
                elif Agent.is_call_tools_node(node):
                    logger.info("Call tools node", node=node)
                    # Probably add HITL here
                    curr = node.model_response
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            if isinstance(event, FunctionToolCallEvent):
                                logger.info("Function tool call event", event=event)
                                message = create_tool_call(
                                    tool_name=event.part.tool_name,
                                    tool_args=event.part.args,
                                    tool_call_id=event.part.tool_call_id,
                                )
                            elif isinstance(
                                event, FunctionToolResultEvent
                            ) and isinstance(event.result, ToolReturnPart):
                                logger.info("Function tool result event", event=event)
                                message = create_tool_return(
                                    tool_name=event.result.tool_name,
                                    content=event.result.content,
                                    tool_call_id=event.tool_call_id,
                                )
                            else:
                                logger.info("Other event", event=event)
                                continue

                            messages.append(message)
                    continue
                elif Agent.is_end_node(node):
                    final = node.data
                    if final.tool_name:
                        curr = create_tool_return(
                            tool_name=final.tool_name,
                            content=final.output,
                            tool_call_id=final.tool_call_id or "",
                        )
                    else:
                        # Plain text output
                        curr = ModelResponse(parts=[TextPart(content=final.output)])
                    messages.append(curr)
                    await workflow.execute_activity_method(
                        AgentActivities.write_end_token,
                        args=(agent_ctx,),
                        start_to_close_timeout=timedelta(seconds=10),
                    )
                else:
                    raise ValueError(f"Unknown node type: {node}")

        result = run.result
        if not isinstance(result, AgentRunResult):
            raise ValueError("No output returned from agent run.")
        return GraphAgentWorkflowResult(messages=messages)
