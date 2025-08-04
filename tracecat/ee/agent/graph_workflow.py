from __future__ import annotations as _annotations

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    # NOTE: These imports are required to avoid workflow sandbox restrictions.
    import httpcore  # noqa: F401
    import httpx  # noqa: F401
    import pydantic_ai  # noqa: F401

    # Rest of imports
    from pydantic import BaseModel, Field
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        ModelMessage,
        ModelResponse,
        TextPart,
        ToolReturnPart,
    )
    from pydantic_ai.models import Model
    from pydantic_core import to_json
    from tracecat_registry.integrations.agents.tools import (
        create_tool_call,
        create_tool_return,
    )
    from tracecat_registry.integrations.pydantic_ai import build_agent

    from tracecat.contexts import ctx_role
    from tracecat.ee.agent.custom_model import DurableModel
    from tracecat.ee.agent.models import ModelInfo, ToolFilters
    from tracecat.logger import logger
    from tracecat.types.auth import system_role


class AgenticGraphWorkflowArgs(BaseModel):
    user_prompt: str
    tool_filters: ToolFilters = Field(default_factory=ToolFilters.default)
    """This is static over the lifetime of the workflow, as it's for 1 turn."""


@workflow.defn
class AgenticGraphWorkflow:
    """Executes an agentic chat turn using pydantic-ai Agent."""

    @workflow.run
    async def run(self, args: AgenticGraphWorkflowArgs) -> str:
        ctx_role.set(system_role())
        model_info = ModelInfo(
            name="gpt-4o-mini",
            provider="openai",
            base_url=None,
        )
        # XXX: This might make an api call to get the model
        # when using badrock, Deal with this later

        agent = build_agent(
            model=DurableModel(model_info),
            instructions="You are a helpful assistant.",
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
