from abc import ABC, abstractmethod
import hashlib
import httpx
import json
from dataclasses import dataclass
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerHTTP
from pydantic_ai.messages import ModelMessage
from pydantic_core import to_jsonable_python
from pydantic import computed_field
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartStartEvent,
    PartDeltaEvent,
    TextPartDelta,
    ToolCallPartDelta,
    TextPart,
    ToolReturnPart,
)
from typing import Any, Literal, NoReturn, TypeVar, Generic
from typing_extensions import Self
from pydantic_ai.agent import ModelRequestNode, AgentRun, CallToolsNode
import diskcache as dc

from tracecat_registry.integrations.pydantic_ai import build_agent
from tracecat_registry.integrations.mcp.exceptions import AgentRunError
from tracecat_registry.integrations.mcp.memory import ShortTermMemory


# Generic type variable for tool result content
T = TypeVar("T", bound=str | dict[str, Any])
# Generic type variable for dependency types
DepsT = TypeVar("DepsT", bound="MCPHostDeps")


def hash_tool_call(tool_name: str, tool_args: str | dict[str, Any]) -> str:
    """Generate a consistent hash for a tool call.

    Args:
        tool_name: Name of the tool
        tool_args: Arguments for the tool call (string or dict)

    Returns:
        MD5 hash of the tool call
    """
    if isinstance(tool_args, dict):
        # Convert to JSON-serializable format and sort keys for consistency
        serializable_args = to_jsonable_python(tool_args)
        args_str = json.dumps(serializable_args, sort_keys=True, separators=(",", ":"))
    else:
        args_str = str(tool_args)

    return hashlib.md5(f"{tool_name}:{args_str}".encode()).hexdigest()


class EmptyNodeResult(BaseModel):
    node_type: Literal["model_request", "tool_call", "tool_result", "end"]


class UserPromptNodeResult(BaseModel):
    user_prompt: str


class ModelRequestNodeResult(BaseModel):
    text_parts: list[str]
    tool_call_parts: list[str]


class ToolCallRequestResult(BaseModel):
    name: str
    args: str | dict[str, Any]

    @computed_field(return_type=str)
    @property
    def hash(self) -> str:
        return hash_tool_call(self.name, self.args)


class ToolResultNodeResult(BaseModel):
    name: str
    content: str | dict[str, Any]
    call_id: str | None = None  # Only specified if tool call is approved


class EndNodeResult(BaseModel):
    output: str


@dataclass
class MCPHostDeps:
    conversation_id: str
    """Conversation ID refers to a full message history (short term memory).

    For example, in Slack, this is the `thread_ts`.
    """
    message_id: str | None
    """Message ID refers to a set of messages that are part of an assistant's response.

    We use this ID to stream updates to the user in a single "block" of messages.
    For example, in Slack, this is the `ts` of a single message in a thread.
    """


class MCPHostResult(BaseModel):
    conversation_id: str
    message_id: str
    message_history: list[ModelMessage]
    last_result: (
        EmptyNodeResult
        | UserPromptNodeResult
        | ModelRequestNodeResult
        | ToolCallRequestResult
        | ToolResultNodeResult
        | EndNodeResult
    )


class MessageStartResult(BaseModel):
    message_id: str


class MCPHost(ABC, Generic[DepsT]):
    def __init__(
        self,
        model_name: str,
        model_provider: str,
        memory: ShortTermMemory,
        mcp_servers: list[MCPServerHTTP],
        model_settings: dict[str, Any] | None = None,
        approved_tool_calls: list[str] | None = None,
        deps_type: type[DepsT] | None = None,
    ) -> None:
        self.agent = build_agent(
            model_name=model_name,
            model_provider=model_provider,
            model_settings=model_settings,
            mcp_servers=mcp_servers,
            deps_type=deps_type or MCPHostDeps,
        )
        self.memory = memory
        # Tool results cache - TODO: Make this an ABC interface in the future
        self.tool_results_cache = dc.FanoutCache(
            directory=".cache/tool_results", shards=8, timeout=0.05
        )
        self._approved_tool_calls = approved_tool_calls

    @abstractmethod
    async def post_message_start(self, deps: DepsT) -> MessageStartResult:
        pass

    @abstractmethod
    async def update_message(self, result: ModelRequestNodeResult, deps: DepsT) -> Self:
        pass

    @abstractmethod
    async def request_tool_approval(
        self, result: ToolCallRequestResult, deps: DepsT
    ) -> Self:
        pass

    @abstractmethod
    async def post_tool_approval(
        self, result: ToolCallRequestResult, approved: bool, deps: DepsT
    ) -> Self:
        pass

    @abstractmethod
    async def post_tool_result(self, result: ToolResultNodeResult, deps: DepsT) -> Self:
        pass

    @abstractmethod
    async def post_message_end(self, deps: DepsT) -> Self:
        pass

    @abstractmethod
    async def post_error_message(self, exc: Exception, deps: DepsT) -> Self:
        pass

    def is_approved_tool_call(
        self, tool_name: str, tool_args: str | dict[str, Any]
    ) -> bool:
        return (
            self._approved_tool_calls is not None
            and hash_tool_call(tool_name, tool_args) in self._approved_tool_calls
        )

    def add_approved_tool_call(
        self, tool_name: str, tool_args: str | dict[str, Any]
    ) -> Self:
        """Add a tool call to the approved list for this agent run.

        Args:
            tool_name: Name of the tool
            tool_args: Arguments for the tool call

        Returns:
            Self for method chaining
        """
        if self._approved_tool_calls is None:
            self._approved_tool_calls = []

        tool_hash = hash_tool_call(tool_name, tool_args)
        if tool_hash not in self._approved_tool_calls:
            self._approved_tool_calls.append(tool_hash)

        return self

    def remove_approved_tool_call(
        self, tool_name: str, tool_args: str | dict[str, Any]
    ) -> Self:
        """Remove a tool call from the approved list.

        Args:
            tool_name: Name of the tool
            tool_args: Arguments for the tool call

        Returns:
            Self for method chaining
        """
        if self._approved_tool_calls is not None:
            tool_hash = hash_tool_call(tool_name, tool_args)
            if tool_hash in self._approved_tool_calls:
                self._approved_tool_calls.remove(tool_hash)

        return self

    def clear_approved_tool_calls(self) -> Self:
        """Clear all approved tool calls.

        Returns:
            Self for method chaining
        """
        self._approved_tool_calls = []
        return self

    def get_approved_tool_calls(self) -> list[str]:
        """Get a copy of the approved tool calls list.

        Returns:
            List of approved tool call hashes
        """
        return (
            list(self._approved_tool_calls)
            if self._approved_tool_calls is not None
            else []
        )

    def has_approved_tool_calls(self) -> bool:
        """Check if there are any approved tool calls.

        Returns:
            True if there are approved tool calls, False otherwise
        """
        return (
            self._approved_tool_calls is not None and len(self._approved_tool_calls) > 0
        )

    def is_new_conversation(self, conversation_id: str) -> bool:
        messages = self.memory.get_messages(conversation_id)
        # A conversation is new if:
        # 1. No messages at all, OR
        # 2. Only one message and it's a user prompt (the current mention)
        if len(messages) == 0:
            return True
        elif len(messages) == 1:
            # Check if the single message is a user prompt
            message = messages[0]
            if hasattr(message, "parts") and len(message.parts) == 1:
                part = message.parts[0]
                return hasattr(part, "part_kind") and part.part_kind == "user-prompt"
        return False

    def store_tool_result(self, call_id: str, content: str | dict[str, Any]) -> Self:
        """Store a tool result for later retrieval.

        Returns self for method chaining.
        """
        self.tool_results_cache.set(call_id, content)
        return self

    def get_tool_result(self, call_id: str) -> Any:
        """Retrieve a stored tool result by call_id.

        Returns the stored content or None if not found.
        """
        return self.tool_results_cache.get(call_id)

    async def _process_model_request_node(
        self,
        node: ModelRequestNode[None, str],
        run: AgentRun,
        deps: DepsT,  # TODO: Revisit typing - pass deps directly for now due to PydanticAI GraphAgentDeps wrapper complexity
    ) -> ModelRequestNodeResult | EmptyNodeResult:
        text_parts = []
        tool_call_parts = []
        conversation_id = deps.conversation_id
        async with node.stream(run.ctx) as handle_stream:
            async for event in handle_stream:
                if isinstance(event, PartStartEvent) and isinstance(
                    event.part, TextPart
                ):
                    text_parts.append(event.part.content)
                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, TextPartDelta):
                        text_parts.append(event.delta.content_delta)
                    elif isinstance(event.delta, ToolCallPartDelta):
                        # If the LLM is starting a tool call stream,
                        # we need to break the "context" block
                        tool_call_parts.append(event.delta.args_delta)

        if len(text_parts) > 0 or len(tool_call_parts) > 0:
            self.memory.add_assistant_message(
                conversation_id=conversation_id,
                content="".join(text_parts),
            )

            return ModelRequestNodeResult(
                text_parts=text_parts,
                tool_call_parts=tool_call_parts,
            )

        return EmptyNodeResult(node_type="model_request")

    async def _process_call_tools_node(
        self,
        node: CallToolsNode[None, str],
        run: AgentRun,
        deps: DepsT,  # TODO: Revisit typing - pass deps directly for now due to PydanticAI GraphAgentDeps wrapper complexity
    ) -> ToolCallRequestResult | ToolResultNodeResult | EmptyNodeResult:
        conversation_id = deps.conversation_id

        async with node.stream(run.ctx) as handle_stream:
            async for event in handle_stream:
                if isinstance(event, FunctionToolCallEvent):
                    tool_name = event.part.tool_name
                    tool_args = event.part.args
                    tool_call_id = event.part.tool_call_id

                    if not self.is_approved_tool_call(tool_name, tool_args):
                        return ToolCallRequestResult(
                            name=tool_name,
                            args=tool_args,
                        )

                    self.memory.add_tool_call(
                        conversation_id=conversation_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_call_id=tool_call_id,
                    )

                elif isinstance(event, FunctionToolResultEvent) and isinstance(
                    event.result, ToolReturnPart
                ):
                    result = ToolResultNodeResult(
                        name=event.result.tool_name,
                        content=event.result.content,
                        call_id=event.tool_call_id,
                    )

                    # Cache the tool result for platform-specific access (e.g., modals)
                    if event.tool_call_id:
                        self.store_tool_result(event.tool_call_id, event.result.content)

                    self.memory.add_tool_result(
                        conversation_id=conversation_id,
                        tool_name=event.result.tool_name,
                        tool_result=event.result.content,
                        tool_call_id=event.tool_call_id,
                    )
                    return result

        return EmptyNodeResult(node_type="tool_call")

    async def _post_error_message_safe(
        self, exc: Exception, deps: DepsT
    ) -> Exception | None:
        """Safely post error message and return any exception that occurred during posting."""
        try:
            await self.post_error_message(exc, deps)
            return None
        except Exception as post_exc:
            return post_exc

    async def _handle_exception_with_error_posting(
        self, exc: Exception, deps: DepsT
    ) -> NoReturn:
        """Handle exception by posting error message safely and raising appropriate chained exception."""
        post_error_exc = await self._post_error_message_safe(exc, deps)

        # Create the main exception
        main_exc = (
            ConnectionError(f"Failed to connect to MCP server: {exc!s}")
            if isinstance(exc, httpx.ConnectError)
            else AgentRunError(
                exc_cls=type(exc),
                exc_msg=str(exc),
                message_history=to_jsonable_python(
                    self.memory.get_messages(deps.conversation_id)
                ),
            )
        )

        # Chain with post error if it occurred
        if post_error_exc is not None:
            main_exc = AgentRunError(
                exc_cls=type(main_exc),
                exc_msg=f"Original error: {main_exc}. Additionally, failed to post error message: {post_error_exc}",
                message_history=to_jsonable_python(
                    self.memory.get_messages(deps.conversation_id)
                ),
            )

        raise main_exc from exc

    async def _run_agent(
        self,
        user_prompt: str,
        deps: DepsT,
        message_history: list[ModelMessage] | None = None,
    ):
        result = EmptyNodeResult(node_type="model_request")
        async with self.agent.run_mcp_servers():
            async with self.agent.iter(
                user_prompt=user_prompt,
                message_history=message_history,
                deps=deps,  # type: ignore  # TODO: Revisit typing - PydanticAI GraphAgentDeps wrapper complexity
            ) as run:
                async for node in run:
                    if Agent.is_user_prompt_node(node):
                        result = UserPromptNodeResult(user_prompt=user_prompt)
                    elif Agent.is_model_request_node(node):
                        result = await self._process_model_request_node(node, run, deps)
                        if isinstance(result, ModelRequestNodeResult):
                            await self.update_message(result, deps)
                    elif Agent.is_call_tools_node(node):
                        result = await self._process_call_tools_node(node, run, deps)
                    elif Agent.is_end_node(node):
                        output = node.data.output
                        result = EndNodeResult(output=output)
                        await self.post_message_end(deps)
                    else:
                        raise ValueError(f"Unknown node type: {node}")
        return result

    async def run(
        self,
        user_prompt: str,
        deps: DepsT,
        message_history: list[ModelMessage] | None = None,
    ) -> MCPHostResult:
        try:
            conversation_id = deps.conversation_id
            message_id = deps.message_id

            # Ensure we have a valid message_id before proceeding
            if not message_id:
                if self.is_new_conversation(conversation_id):
                    start_message = await self.post_message_start(deps)
                    message_id = start_message.message_id
                    # Update deps with the new message_id
                    deps.message_id = message_id
                else:
                    raise ValueError(
                        "`message_id` is required for non-new conversations"
                    )

            if not deps.message_id:
                raise ValueError("Failed to obtain a valid message_id")

            result = await self._run_agent(
                user_prompt=user_prompt, deps=deps, message_history=message_history
            )

            return MCPHostResult(
                conversation_id=deps.conversation_id,
                last_result=result,
                message_id=message_id,
                message_history=self.memory.get_messages(deps.conversation_id),
            )

        except ExceptionGroup as e:
            await self._handle_exception_with_error_posting(e.exceptions[0], deps)
        except Exception as e:
            await self._handle_exception_with_error_posting(e, deps)
