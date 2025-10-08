"""Pydantic AI agents with tool calling."""

import uuid
from timeit import default_timer
from typing import Any, Literal

from langfuse import observe
from pydantic import BaseModel
from pydantic_ai import Agent, RunUsage, StructuredDict, Tool, UsageLimits
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.messages import ModelMessage
from pydantic_ai.settings import ModelSettings
from pydantic_core import to_jsonable_python

from tracecat.agent.exceptions import AgentRunError
from tracecat.agent.executor.aio import AioStreamingAgentExecutor
from tracecat.agent.models import ModelInfo, RunAgentArgs, ToolFilters
from tracecat.agent.observability import init_langfuse
from tracecat.agent.parsers import try_parse_json
from tracecat.agent.prompts import ToolCallPrompt, VerbosityPrompt
from tracecat.agent.providers import get_model
from tracecat.agent.stream.writers import AgentNodeStreamWriter
from tracecat.agent.tools import build_agent_tools
from tracecat.config import TRACECAT__AGENT_MAX_REQUESTS, TRACECAT__AGENT_MAX_TOOL_CALLS
from tracecat.contexts import ctx_session_id
from tracecat.logger import logger

# Initialize Pydantic AI instrumentation for Langfuse
Agent.instrument_all()


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
    session_id: str
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
    actions: list[str] | None = None,
    namespaces: list[str] | None = None,
    fixed_arguments: dict[str, dict[str, Any]] | None = None,
    mcp_server_url: str | None = None,
    mcp_server_headers: dict[str, str] | None = None,
    model_settings: dict[str, Any] | None = None,
    retries: int = 3,
    event_stream_handler: EventStreamHandler | None = None,
) -> Agent:
    agent_tools: list[Tool] = []
    if actions:
        tools = await build_agent_tools(
            fixed_arguments=fixed_arguments,
            namespaces=namespaces,
            actions=actions,
        )
        agent_tools = tools.tools
    _output_type = _parse_output_type(output_type)
    _model_settings = ModelSettings(**model_settings) if model_settings else None
    model = get_model(model_name, model_provider, base_url)

    # Add verbosity prompt
    verbosity_prompt = VerbosityPrompt()
    instructions = f"{instructions}\n{verbosity_prompt.prompt}"

    if actions:
        tool_calling_prompt = ToolCallPrompt(
            tools=tools.tools,
            fixed_arguments=fixed_arguments,
        )
        instruction_parts = [instructions, tool_calling_prompt.prompt]
        instructions = "\n".join(part for part in instruction_parts if part)

    toolsets = None
    if mcp_server_url:
        mcp_server = MCPServerStreamableHTTP(
            url=mcp_server_url,
            headers=mcp_server_headers,
        )
        toolsets = [mcp_server]

    agent = Agent(
        model=model,
        instructions=instructions,
        output_type=_output_type,
        model_settings=_model_settings,
        retries=retries,
        instrument=True,
        tools=agent_tools,
        toolsets=toolsets,
        event_stream_handler=event_stream_handler,
    )
    return agent


async def run_agent_sync(
    agent: Agent,
    user_prompt: str,
    max_requests: int,
    max_tools_calls: int | None = None,
) -> AgentOutput:
    """Run an agent synchronously."""

    if max_tools_calls and max_tools_calls > TRACECAT__AGENT_MAX_TOOL_CALLS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_TOOL_CALLS} tool calls"
        )
    if max_requests > TRACECAT__AGENT_MAX_REQUESTS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_REQUESTS} requests"
        )

    start_time = default_timer()
    usage = UsageLimits(request_limit=max_requests, tool_calls_limit=max_tools_calls)
    result = await agent.run(user_prompt, usage_limits=usage)
    end_time = default_timer()
    return AgentOutput(
        output=try_parse_json(result.output),
        message_history=result.all_messages(),
        duration=end_time - start_time,
        usage=result.usage(),
        session_id=str(uuid.uuid4()),
    )


@observe()
async def run_agent(
    user_prompt: str,
    model_name: str,
    model_provider: str,
    actions: list[str] | None = None,
    fixed_arguments: dict[str, dict[str, Any]] | None = None,
    mcp_server_url: str | None = None,
    mcp_server_headers: dict[str, str] | None = None,
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
    max_tools_calls: int = 5,
    max_requests: int = 20,
    retries: int = 3,
    base_url: str | None = None,
) -> AgentOutput:
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
        mcp_server_url: Optional URL of the MCP server to use.
        mcp_server_headers: Optional headers for the MCP server.
        output_type: Optional specification for the agent's output format.
                    Can be a string type name or a structured dictionary schema.
                    Supported types: bool, float, int, str, list[bool], list[float], list[int], list[str]
        model_settings: Optional model-specific configuration parameters
                       (temperature, max_tokens, etc.).
        max_tools_calls: Maximum number of tool calls to make per agent run (default: 5).
        max_requests: Maximum number of requests to make per agent run (default: 20).
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

    if max_tools_calls > TRACECAT__AGENT_MAX_TOOL_CALLS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_TOOL_CALLS} tool calls"
        )

    if max_requests > TRACECAT__AGENT_MAX_REQUESTS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_REQUESTS} requests"
        )

    start_time = default_timer()

    session_id = ctx_session_id.get() or str(uuid.uuid4())
    message_nodes: list[ModelMessage] = []
    executor = AioStreamingAgentExecutor(writer_cls=AgentNodeStreamWriter)
    try:
        model_info = ModelInfo(
            name=model_name,
            provider=model_provider,
            base_url=base_url,
        )
        args = RunAgentArgs(
            user_prompt=user_prompt,
            tool_filters=ToolFilters(actions=actions),
            session_id=session_id,
            instructions=instructions,
            model_info=model_info,
        )
        handle = await executor.start(args)
        result = await handle.result()
        if result is None:
            raise RuntimeError(
                "Action: Streaming agent run did not complete successfully."
            )
        end_time = default_timer()
        return AgentOutput(
            output=result,
            message_history=message_nodes,
            duration=end_time - start_time,
            usage=result.usage(),
            trace_id=trace_id,
            session_id=session_id,
        )

    except Exception as e:
        logger.exception("Error in agent run", error=e)
        raise AgentRunError(
            exc_cls=type(e),
            exc_msg=str(e),
            message_history=to_jsonable_python(message_nodes),
        ) from e
