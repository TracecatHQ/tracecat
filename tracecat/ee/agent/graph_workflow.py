from __future__ import annotations as _annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Generic

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    # NOTE: These imports are required to avoid workflow sandbox restrictions.
    import httpcore  # noqa: F401
    import httpx  # noqa: F401
    import pydantic_ai  # noqa: F401

    # Rest of imports
    from pydantic import BaseModel
    from pydantic_ai import Agent, RunContext, Tool, messages
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
        ToolDefinition,
    )
    from pydantic_core import to_json
    from tracecat_registry.integrations.agents.tools import (
        create_tool_call,
        create_tool_return,
    )
    from tracecat_registry.integrations.pydantic_ai import build_agent

    from tracecat.contexts import ctx_role
    from tracecat.ee.agent.activities import (
        BuildToolDefinitionsArgs,
        build_tool_definitions,
        execute_tool_call,
    )
    from tracecat.ee.agent.custom_model import DurableModel
    from tracecat.ee.agent.models import ExecuteToolCallArgs, ModelInfo, ToolFilters
    from tracecat.logger import logger
    from tracecat.types.auth import Role


class DurableGatedTool(Generic[AgentDepsT]):
    """
    Proxy around a real Tool that pauses for human approval
    before letting the underlying tool run.
    """

    def __init__(
        self,
        tool: ToolDefinition,
        gate: Callable[
            [ToolCallPart, RunContext[AgentDepsT]],  # (tool_name, args, context)
            Awaitable[bool],  # approve = True / False
        ],
    ) -> None:
        self._tool = tool
        self._gate = gate

        # Re-export the wrapped tool’s public definition so that the
        # LLM still “sees” the same schema.
        self.tool = Tool[AgentDepsT](
            self._call_gated_tool,
            name=tool.name,
            description=tool.description,
            takes_ctx=True,  # we need ctx for audit trail
            strict=tool.strict,
        )

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    async def _call_gated_tool(
        self,
        ctx: RunContext[AgentDepsT],
        **tool_args: Any,
    ) -> messages.ToolReturnPart | messages.RetryPromptPart:
        logger.info("HITLTool", tool_name=self._tool.name, tool_args=tool_args, ctx=ctx)
        approved = await self._gate(
            ToolCallPart(
                tool_name=self._tool.name,
                tool_call_id=ctx.tool_call_id or "",
                args=tool_args,
            ),
            ctx,
        )

        if not approved:
            # Tell the model to try again or do something else
            return messages.RetryPromptPart(
                tool_name=self._tool.name,
                tool_call_id=ctx.tool_call_id
                or "",  # echo back whatever id pydantic-ai gave
                content="Call rejected by human reviewer. Try another approach.",
            )

        # Human said yes – delegate to the real implementation
        return await self._call_tool(ctx, **tool_args)

    async def _call_tool(self, ctx: RunContext[AgentDepsT], **tool_args: Any) -> Any:
        # This function represents the actual tool call
        # We proxy it to temporal
        args = ExecuteToolCallArgs(
            tool_name=self._tool.name,
            tool_args=tool_args,
            tool_call_id=ctx.tool_call_id or "",
        )
        role = ctx_role.get()
        result = await workflow.execute_activity(
            execute_tool_call,
            args=(args, role),
            start_to_close_timeout=timedelta(seconds=60),
        )
        logger.info("Tool call result", result=result)
        return result.tool_return


class AgenticGraphWorkflowArgs(BaseModel):
    role: Role
    user_prompt: str
    tool_filters: ToolFilters | None = None
    """This is static over the lifetime of the workflow, as it's for 1 turn."""


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ApprovalState:
    status: ApprovalStatus
    tool_call_part: ToolCallPart


@workflow.defn
class AgenticGraphWorkflow:
    """Executes an agentic chat turn using pydantic-ai Agent."""

    pending_calls: dict[str, ApprovalState]

    @workflow.init
    def __init__(self, args: AgenticGraphWorkflowArgs) -> None:
        self.pending_calls = {}

    @workflow.signal
    def approve_call(self, call_id: str) -> None:
        if call_id not in self.pending_calls:
            raise ApplicationError(f"Call ID {call_id} not found")
        self.pending_calls[call_id].status = ApprovalStatus.APPROVED

    @workflow.signal
    def reject_call(self, call_id: str) -> None:
        if call_id not in self.pending_calls:
            raise ApplicationError(f"Call ID {call_id} not found")
        self.pending_calls[call_id].status = ApprovalStatus.REJECTED

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
        self.pending_calls[call_id] = ApprovalState(
            status=ApprovalStatus.PENDING,
            tool_call_part=message,
        )
        # expose to UI via workflow state or signal
        await workflow.wait_condition(
            lambda: self.pending_calls[call_id].status == ApprovalStatus.APPROVED
        )
        return True  # proceed

    @workflow.run
    async def run(self, args: AgenticGraphWorkflowArgs) -> str:
        ctx_role.set(args.role)
        model_info = ModelInfo(
            name="gpt-4o-mini",
            provider="openai",
            base_url=None,
        )
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

        agent = build_agent(
            model=DurableModel(model_info),
            instructions="You are a helpful assistant.",
            tools=[t.tool for t in self.tools],
        )
        if not isinstance(agent.model, Model):
            raise ApplicationError("Model is None")
        logger.info("Agent built", agent=agent)
        messages: list[ModelMessage] = []
        async with agent.iter(user_prompt=args.user_prompt) as run:
            async for node in run:
                curr: ModelMessage
                if Agent.is_user_prompt_node(node):
                    continue

                # 1️⃣  Model request (may be a normal user/tool-return message)
                elif Agent.is_model_request_node(node):
                    curr = node.request
                    logger.info("Model request", curr=curr)

                    # If this request is ONLY a tool-return we have
                    # already streamed it via FunctionToolResultEvent.
                    if any(isinstance(p, ToolReturnPart) for p in curr.parts):
                        messages.append(curr)  # keep history
                        continue  # ← skip duplicate stream
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
                        curr = ModelResponse(
                            parts=[
                                TextPart(content=final.output),
                            ]
                        )
                else:
                    raise ValueError(f"Unknown node type: {node}")

                messages.append(curr)
        return to_json(messages).decode()
