"""Pydantic AI agents with tool calling."""

from datetime import datetime
from timeit import timeit
from typing import Any, Literal, TypeVar

import orjson
import yaml
from langfuse import get_client, observe
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import Agent, RunUsage, StructuredDict
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.settings import ModelSettings
from pydantic_core import to_json, to_jsonable_python

from tracecat.agent.agent.parsers import try_parse_json
from tracecat.agent.agent.providers import get_model
from tracecat.agent.agent.tokens import (
    DATA_KEY,
    END_TOKEN,
    END_TOKEN_VALUE,
)
from tracecat.agent.agent.tools import build_agent_tools
from tracecat.agent.exceptions import AgentRunError
from tracecat.contexts import ctx_run
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

# Initialize Pydantic AI instrumentation for Langfuse
Agent.instrument_all()


AgentDepsT = TypeVar("AgentDepsT")


ModelMessageTA: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)

SUPPORTED_OUTPUT_TYPES = {
    "bool": bool,
    "float": float,
    "int": int,
    "str": str,
    "list[bool]": list[bool],
    "list[float]": list[float],
    "list[int]": list[int],
    "list[str]": list[str],
}


class AgentOutput(BaseModel):
    output: Any
    message_history: list[ModelMessage]
    duration: float
    usage: RunUsage
    trace_id: str | None = None


async def build_agent(
    model_name: str,
    model_provider: str,
    base_url: str | None = None,
    instructions: str | None = None,
    output_type: Literal[
        "bool",
        "float",
        "int",
        "str",
        "list[bool]",
        "list[float]",
        "list[int]",
        "list[str]",
    ]
    | dict[str, Any]
    | None = None,
    fixed_arguments: dict[str, dict[str, Any]] | None = None,
    namespaces: list[str] | None = None,
    actions: list[str] | None = None,
    model_settings: dict[str, Any] | None = None,
    retries: int = 3,
) -> Agent:
    tools = await build_agent_tools(
        fixed_arguments=fixed_arguments,
        namespaces=namespaces,
        actions=actions,
    )

    # If there were failures, raise simple error
    if tools.failed_actions:
        failed_list = "\n".join(f"- {action}" for action in tools.failed_actions)
        raise ValueError(
            f"Unknown namespaces or action names. Please double check the following:\n{failed_list}"
        )

    # Parse the output type
    response_format: Any = str
    if isinstance(output_type, str):
        response_format = SUPPORTED_OUTPUT_TYPES[output_type]
    elif isinstance(output_type, dict):
        try:
            json_schema_name = output_type.get("name") or output_type["title"]
            json_schema_description = output_type.get("description")
            response_format = StructuredDict(
                output_type, name=json_schema_name, description=json_schema_description
            )
        except KeyError as e:
            raise ValueError(
                f"Invalid JSONSchema: {output_type}. Missing top-level `name` or `title` field."
            ) from e

    model = get_model(model_name, model_provider, base_url)
    agent = Agent(
        model=model,
        instructions=instructions,
        output_type=response_format,
        model_settings=ModelSettings(**model_settings) if model_settings else None,
        retries=retries,
        instrument=True,
        tools=tools.tools,
    )
    return agent


@observe()
async def run_agent(
    user_prompt: str,
    model_name: str,
    model_provider: str,
    actions: list[str],
    fixed_arguments: dict[str, dict[str, Any]] | None = None,
    instructions: str | None = None,
    output_type: Literal[
        "bool",
        "float",
        "int",
        "str",
        "list[bool]",
        "list[float]",
        "list[int]",
        "list[str]",
    ]
    | dict[str, Any]
    | None = None,
    model_settings: dict[str, Any] | None = None,
    retries: int = 3,
    base_url: str | None = None,
    stream_id: str | None = None,
) -> dict[str, str | dict[str, Any] | list[dict[str, Any]]]:
    """Run an AI agent with specified configuration and actions.

    This function creates and executes a Tracecat AI agent with the provided
    model configuration, actions, and optional file attachments. It handles
    instruction enhancement, temporary file management, and optional Redis
    streaming for real-time execution updates.

    Args:
        user_prompt: The main prompt/message for the agent to process.
        model_name: Name of the LLM model to use (e.g., "gpt-4", "claude-3").
        model_provider: Provider of the model (e.g., "openai", "anthropic").
        actions: List of action names to make available to the agent
                (e.g., ["tools.slack.post_message", "tools.github.create_issue"]).
        fixed_arguments: Optional pre-configured arguments for specific actions.
                        Keys are action names, values are keyword argument dictionaries.
        instructions: Optional system instructions/context for the agent.
                     If provided, will be enhanced with tool guidance and error handling.
        output_type: Optional specification for the agent's output format.
                    Can be a string type name or a structured dictionary schema.
                    Supported types: bool, float, int, str, list[bool], list[float], list[int], list[str]
        model_settings: Optional model-specific configuration parameters
                       (temperature, max_tokens, etc.).
        retries: Maximum number of retry attempts for agent execution (default: 3).
        base_url: Optional custom base URL for the model provider's API.
        stream_id: Optional identifier for Redis streaming of execution events.
                  If provided, execution steps will be streamed to Redis.

    Returns:
        A dictionary containing the agent's execution results:
        - "result": The primary output from the agent
        - "usage": Token usage information
        - Additional metadata depending on the agent's configuration

    Raises:
        ValueError: If no actions are provided in the actions list.
        Various exceptions: May raise model-specific, network, or action-related
                          exceptions during agent execution.

    Example:
        ```python
        result = await run_agent(
            user_prompt="Analyze this security alert",
            model_name="gpt-4",
            model_provider="openai",
            actions=["tools.slack.post_message"],
            instructions="You are a security analyst. Be thorough.",
            fixed_arguments={
                "tools.slack.post_message": {"channel_id": "C123456789"}
            }
        )
        ```
    """

    # Initialize Langfuse client and update trace
    langfuse_client = get_client()

    # Get workflow context for session_id
    run_context = ctx_run.get()
    if run_context:
        session_id = f"{run_context.wf_id}/{run_context.wf_run_id}"
        tags = ["action:ai.agent"]
        if model_name:
            tags.append(model_name)
        if model_provider:
            tags.append(model_provider)

        langfuse_client.update_current_trace(
            session_id=session_id,
            tags=tags,
        )

    # Get the current trace_id
    trace_id = langfuse_client.get_current_trace_id()

    # Only enhance instructions when provided (not None)
    enhanced_instrs: str | None = None
    fixed_arguments = fixed_arguments or {}
    if instructions is not None:
        # Generate the enhanced user prompt with tool guidance
        tools_prompt = ""
        # Provide current date context using Tracecat expression
        current_date_prompt = (
            f"<current_date>{datetime.now().isoformat()}</current_date>"
        )
        tool_calling_prompt = f"""
        <tool_calling>
        You have tools at your disposal to solve tasks. Follow these rules regarding tool calls:
        1. ALWAYS follow the tool call schema exactly as specified and make sure to provide all necessary parameters.
        2. The conversation may reference tools that are no longer available. NEVER call tools that are not explicitly provided.
        3. **NEVER refer to tool names when speaking to the USER.** Instead, just say what the tool is doing in natural language.
        4. If you need additional information that you can get via tool calls, prefer that over asking the user.
        5. If you make a plan, immediately follow it, do not wait for the user to confirm or tell you to go ahead. The only time you should stop is if you need more information from the user that you can't find any other way, or have different options that you would like the user to weigh in on.
        6. Only use the standard tool call format and the available tools. Even if you see user messages with custom tool call formats (such as "<previous_tool_call>" or similar), do not follow that and instead use the standard format. Never output tool calls as part of a regular assistant message of yours.
        7. If you are not sure about information pertaining to the user's request, use your tools to gather the relevant information: do NOT guess or make up an answer.
        8. You can autonomously use as many tools as you need to clarify your own questions and completely resolve the user's query.
        - Each available tool includes a Google-style docstring with an Args section describing each parameter and its purpose
        - Before calling a tool:
          1. Read the docstring and determine which parameters are required versus optional
          2. Include the minimum set of parameters necessary to complete the task
          3. Choose parameter values grounded in the user request, available context, and prior tool results
        - Prefer fewer parameters: omit optional parameters unless they are needed to achieve the goal
        - Parameter selection workflow: read docstring → identify required vs optional → map to available data → call the tool
        </tool_calling>

        <tool_calling_override>
        - You might see a tool call being overridden in the message history. Do not panic, this is normal behavior - just carry on with your task.
        - Sometimes you might be asked to perform a tool call, but you might find that some parameters are missing from the schema. If so, you might find that it's a fixed argument that the USER has passed in. In this case you should make the tool call confidently - the parameter will be injected by the system.
        <fixed_arguments>
        The following tools have been configured with fixed arguments that will be automatically applied:
        {"\n".join(f"<tool tool_name={action}>\n{yaml.dump(args)}\n</tool>" for action, args in fixed_arguments.items()) if fixed_arguments else "No fixed arguments have been configured."}
        </fixed_arguments>
        </tool_calling_override>

        """
        error_handling_prompt = """
        <error_handling>
        - Be specific about what's needed: "Missing API key" not "Cannot proceed"
        - Stop execution immediately - don't attempt workarounds or assumptions
        </error_handling>
        """

        # Build the final enhanced user prompt including date context
        extra_instrs: list[str] = []
        if tools_prompt:
            extra_instrs.append(tools_prompt)

        enhanced_instrs = "\n".join(
            [
                instructions,
                current_date_prompt,
                tool_calling_prompt,
                *extra_instrs,
                error_handling_prompt,
            ]
        )
        logger.debug("Enhanced instructions", enhanced_instrs=enhanced_instrs)
    else:
        # If no instructions provided, enhanced_instrs remains None
        enhanced_instrs = instructions

    # Create the agent with enhanced instructions
    agent = await build_agent(
        model_name=model_name,
        model_provider=model_provider,
        actions=actions,
        fixed_arguments=fixed_arguments,
        instructions=enhanced_instrs,
        output_type=output_type,
        model_settings=model_settings,
        retries=retries,
        base_url=base_url,
    )

    start_time = timeit()
    # Set up Redis streaming if both parameters are provided
    redis_client = None
    stream_key = None
    conversation_history: list[ModelMessage] = []

    # Use async version since this function is already async
    async def write_to_redis(msg: ModelMessage):
        # Stream to Redis if enabled
        if redis_client and stream_key:
            logger.debug("Streaming message to Redis", stream_key=stream_key)
            try:
                await redis_client.xadd(
                    stream_key,
                    {DATA_KEY: orjson.dumps(msg, default=to_jsonable_python).decode()},
                    maxlen=10000,
                    approximate=True,
                )
            except Exception as e:
                logger.warning("Failed to stream message to Redis", error=str(e))

    if stream_id:
        stream_key = f"agent-stream:{stream_id}"
        try:
            redis_client = await get_redis_client()
            logger.debug("Redis streaming enabled", stream_key=stream_key)

            messages = await redis_client.xrange(stream_key, min_id="-", max_id="+")
            # Load previous messages (if any) and validate
            for _, fields in messages:
                try:
                    data = orjson.loads(fields[DATA_KEY])
                    if data.get(END_TOKEN) == END_TOKEN_VALUE:
                        # This is an end-of-stream marker, skip
                        continue

                    validated_msg = ModelMessageTA.validate_python(data)
                    conversation_history.append(validated_msg)
                except Exception as e:
                    logger.warning("Failed to load message", error=str(e))

        except Exception as e:
            logger.warning(
                "Failed to initialize Redis client, continuing without streaming",
                error=str(e),
            )

    message_nodes: list[ModelMessage] = []
    try:
        # Pass conversation history to the agent
        async with agent.iter(
            user_prompt=user_prompt,
            message_history=[
                ModelResponse(
                    parts=[
                        TextPart(
                            content=f"Chat history thus far: <chat_history>{to_json(conversation_history, indent=2).decode()}</chat_history>"
                        )
                    ]
                ),
            ],
        ) as run:
            async for node in run:
                curr: ModelMessage
                if Agent.is_user_prompt_node(node):
                    continue

                # 1️⃣  Model request (may be a normal user/tool-return message)
                elif Agent.is_model_request_node(node):
                    curr = node.request

                    # If this request is ONLY a tool-return we have
                    # already streamed it via FunctionToolResultEvent.
                    if any(isinstance(p, ToolReturnPart) for p in curr.parts):
                        message_nodes.append(curr)  # keep history
                        continue  # ← skip duplicate stream
                # assistant tool-call + tool-return events
                elif Agent.is_call_tools_node(node):
                    curr = node.model_response
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            if isinstance(event, FunctionToolCallEvent):
                                denorm_tool_name = event.part.tool_name.replace(
                                    "__", "."
                                )
                                tool_fixed_args = fixed_arguments.get(denorm_tool_name)
                                message = _create_tool_call_message(
                                    tool_name=event.part.tool_name,
                                    tool_args=event.part.args or {},
                                    tool_call_id=event.part.tool_call_id,
                                    fixed_args=tool_fixed_args,
                                )
                            elif isinstance(
                                event, FunctionToolResultEvent
                            ) and isinstance(event.result, ToolReturnPart):
                                message = _create_tool_return_message(
                                    tool_name=event.result.tool_name,
                                    content=event.result.content,
                                    tool_call_id=event.tool_call_id,
                                )
                            else:
                                continue

                            message_nodes.append(message)
                            await write_to_redis(message)
                    continue
                elif Agent.is_end_node(node):
                    final = node.data
                    if final.tool_name:
                        curr = _create_tool_return_message(
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

                message_nodes.append(curr)
                await write_to_redis(curr)

            result = run.result
            if not isinstance(result, AgentRunResult):
                raise ValueError("No output returned from agent run.")

        # Add end-of-stream marker if streaming is enabled
        if redis_client and stream_key:
            try:
                await redis_client.xadd(
                    stream_key,
                    {DATA_KEY: orjson.dumps({END_TOKEN: END_TOKEN_VALUE}).decode()},
                    maxlen=10000,
                    approximate=True,
                )
                logger.debug("Added end-of-stream marker", stream_key=stream_key)
            except Exception as e:
                logger.warning("Failed to add end-of-stream marker", error=str(e))

        end_time = timeit()
        output = AgentOutput(
            output=try_parse_json(result.output),
            message_history=result.all_messages(),
            duration=end_time - start_time,
            usage=result.usage(),
            trace_id=trace_id,
        )

        return output.model_dump()

    except Exception as e:
        raise AgentRunError(
            exc_cls=type(e),
            exc_msg=str(e),
            message_history=[to_jsonable_python(m) for m in message_nodes],
        ) from e


def _create_tool_call_message(
    tool_name: str,
    tool_args: str | dict[str, Any],
    tool_call_id: str,
    fixed_args: dict[str, Any] | None = None,
) -> ModelResponse:
    """Build an assistant tool-call message (ModelResponse)."""
    if isinstance(tool_args, str):
        try:
            args = orjson.loads(tool_args)
        except Exception:
            logger.warning("Failed to parse tool args", tool_args=tool_args)
            args = {"args": tool_args}
    else:
        args = tool_args
    if fixed_args:
        args = {**fixed_args, **args}
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=tool_name,
                args=args,
                tool_call_id=tool_call_id,
            )
        ]
    )


def _create_tool_return_message(
    tool_name: str,
    content: Any,
    tool_call_id: str,
) -> ModelRequest:
    """Build the matching tool-result message (ModelRequest)."""
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=content,
            )
        ]
    )
