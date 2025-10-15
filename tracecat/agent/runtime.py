"""Pydantic AI agents with tool calling."""

import uuid
from timeit import default_timer
from typing import Any

from langfuse import observe
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import ModelMessage
from pydantic_core import to_jsonable_python

from tracecat.agent.exceptions import AgentRunError
from tracecat.agent.executor.aio import AioStreamingAgentExecutor
from tracecat.agent.models import (
    AgentConfig,
    AgentOutput,
    OutputType,
    RunAgentArgs,
)
from tracecat.agent.observability import init_langfuse
from tracecat.agent.parsers import try_parse_json
from tracecat.agent.stream.common import PersistableStreamingAgentDeps
from tracecat.config import TRACECAT__AGENT_MAX_REQUESTS, TRACECAT__AGENT_MAX_TOOL_CALLS
from tracecat.contexts import ctx_role, ctx_session_id
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatAuthorizationError

# Initialize Pydantic AI instrumentation for Langfuse
Agent.instrument_all()


async def run_agent_sync(
    agent: Agent[Any, Any],
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
        session_id=uuid.uuid4(),
    )


@observe()
async def run_agent(
    user_prompt: str,
    model_name: str,
    model_provider: str,
    actions: list[str] | None = None,
    mcp_server_url: str | None = None,
    mcp_server_headers: dict[str, str] | None = None,
    instructions: str | None = None,
    output_type: OutputType | None = None,
    model_settings: dict[str, Any] | None = None,
    max_tool_calls: int = 5,
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

    if max_tool_calls > TRACECAT__AGENT_MAX_TOOL_CALLS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_TOOL_CALLS} tool calls"
        )

    if max_requests > TRACECAT__AGENT_MAX_REQUESTS:
        raise ValueError(
            f"Cannot request more than {TRACECAT__AGENT_MAX_REQUESTS} requests"
        )

    start_time = default_timer()

    session_id = ctx_session_id.get() or uuid.uuid4()
    message_nodes: list[ModelMessage] = []

    role = ctx_role.get()
    if role is None or role.workspace_id is None:
        raise TracecatAuthorizationError("Workspace context required for agent run")

    deps = await PersistableStreamingAgentDeps.new(
        session_id, role.workspace_id, persistent=False
    )
    executor = AioStreamingAgentExecutor(deps=deps, role=role)
    try:
        args = RunAgentArgs(
            user_prompt=user_prompt,
            session_id=session_id,
            config=AgentConfig(
                model_name=model_name,
                model_provider=model_provider,
                base_url=base_url,
                instructions=instructions,
                output_type=output_type,
                model_settings=model_settings,
                retries=retries,
                deps_type=type(deps),
                mcp_server_url=mcp_server_url,
                mcp_server_headers=mcp_server_headers,
                actions=actions,
            ),
            max_requests=max_requests,
            max_tool_calls=max_tool_calls,
        )
        handle = await executor.start(args)
        result = await handle.result()
        if result is None:
            raise RuntimeError(
                "Action: Streaming agent run did not complete successfully. The "
                "selected model may not support streaming responses. Try switching "
                "to a model with streaming support or disable streaming."
            )
        end_time = default_timer()
        return AgentOutput(
            output=try_parse_json(result.output),
            message_history=result.all_messages(),
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
