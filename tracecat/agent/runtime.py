"""Pydantic AI agents with tool calling."""

from timeit import timeit
from typing import Any, Literal, TypeVar

import orjson
from langfuse import observe
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
from pydantic_core import to_jsonable_python

from tracecat.agent.exceptions import AgentRunError
from tracecat.agent.observability import init_langfuse
from tracecat.agent.parsers import try_parse_json
from tracecat.agent.prompts import MessageHistoryPrompt, ToolCallPrompt
from tracecat.agent.providers import get_model
from tracecat.agent.tokens import (
    DATA_KEY,
    END_TOKEN,
    END_TOKEN_VALUE,
)
from tracecat.agent.tools import build_agent_tools
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client

# Initialize Pydantic AI instrumentation for Langfuse
Agent.instrument_all()


AgentDepsT = TypeVar("AgentDepsT")


ModelMessageTA: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)

SUPPORTED_OUTPUT_TYPES: dict[str, type[Any]] = {
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


def _parse_output_type(
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
    | None,
) -> type[Any]:
    if isinstance(output_type, str):
        try:
            return SUPPORTED_OUTPUT_TYPES[output_type]
        except KeyError as e:
            raise ValueError(
                f"Unknown output type: {output_type}. Expected one of: {', '.join(SUPPORTED_OUTPUT_TYPES.keys())}"
            ) from e
    elif isinstance(output_type, dict):
        schema_name = output_type.get("name") or output_type.get("title")
        schema_description = output_type.get("description")
        return StructuredDict(
            output_type, name=schema_name, description=schema_description
        )
    else:
        return str


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
    _output_type = _parse_output_type(output_type)
    _model_settings = ModelSettings(**model_settings) if model_settings else None
    model = get_model(model_name, model_provider, base_url)

    if actions:
        tool_calling_prompt = ToolCallPrompt(
            tools=tools.tools,
            fixed_arguments=fixed_arguments,
        )
        instruction_parts = [instructions, tool_calling_prompt.prompt]
        instructions = "\n\n".join(part for part in instruction_parts if part)

    agent = Agent(
        model=model,
        instructions=instructions,
        output_type=_output_type,
        model_settings=_model_settings,
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

    trace_id = init_langfuse(model_name, model_provider)

    # Create the agent with enhanced instructions
    agent = await build_agent(
        model_name=model_name,
        model_provider=model_provider,
        actions=actions,
        fixed_arguments=fixed_arguments,
        instructions=instructions,
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
        message_history_prompt = MessageHistoryPrompt(
            message_history=conversation_history
        )
        async with agent.iter(
            user_prompt=user_prompt,
            message_history=message_history_prompt.to_message_history(),
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
                                tool_fixed_args = {}
                                if fixed_arguments:
                                    tool_fixed_args = fixed_arguments.get(
                                        denorm_tool_name
                                    )
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
