from abc import ABC, abstractmethod
import hashlib
import httpx
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
from typing import Any, Literal, NoReturn
from pydantic_ai.agent import ModelRequestNode, AgentRun, CallToolsNode

from tracecat_registry.integrations.pydantic_ai import build_agent
from tracecat_registry.integrations.mcp.exceptions import AgentRunError
from tracecat_registry.integrations.mcp.memory import ShortTermMemory


def hash_tool_call(tool_name: str, tool_args: str | dict[str, Any]) -> str:
    return hashlib.md5(f"{tool_name}:{tool_args}".encode()).hexdigest()


class EmptyNodeResult(BaseModel):
    node_type: Literal["model_request", "tool_call", "tool_result", "end"]


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


@dataclass
class MCPHostDeps:
    conversation_id: str  # e.g. `thread_ts` in Slack
    message_id: str  # e.g. `ts` in Slack


class MCPHostResult(BaseModel):
    conversation_id: str
    message_id: str
    message_history: list[ModelMessage]


class MCPHost(ABC):
    def __init__(
        self,
        model_name: str,
        model_provider: str,
        memory: ShortTermMemory,
        mcp_servers: list[MCPServerHTTP],
        approved_tool_calls: list[str] | None = None,
        agent_settings: dict[str, Any] | None = None,
    ):
        self.agent = build_agent(
            model_name=model_name,
            model_provider=model_provider,
            model_settings=agent_settings,
            mcp_servers=mcp_servers,
            dep_type=MCPHostDeps,
        )
        self.memory = memory
        self._approved_tool_calls = approved_tool_calls

    @abstractmethod
    def post_conversation_start(self, deps: MCPHostDeps) -> None:
        pass

    @abstractmethod
    def post_message(self, result: ModelRequestNodeResult, deps: MCPHostDeps) -> None:
        pass

    @abstractmethod
    def update_message(self, result: ModelRequestNodeResult, deps: MCPHostDeps) -> None:
        pass

    @abstractmethod
    def request_tool_approval(
        self, result: ToolCallRequestResult, deps: MCPHostDeps
    ) -> None:
        pass

    @abstractmethod
    def post_tool_approval(
        self, result: ToolCallRequestResult, approved: bool, deps: MCPHostDeps
    ) -> None:
        pass

    @abstractmethod
    def post_tool_result(self, result: ToolResultNodeResult, deps: MCPHostDeps) -> None:
        pass

    @abstractmethod
    def post_conversation_end(self, deps: MCPHostDeps) -> None:
        pass

    @abstractmethod
    def post_error_message(self, exc: Exception, deps: MCPHostDeps) -> None:
        pass

    def is_approved_tool_call(
        self, tool_name: str, tool_args: str | dict[str, Any]
    ) -> bool:
        return (
            self._approved_tool_calls is not None
            and hash_tool_call(tool_name, tool_args) in self._approved_tool_calls
        )

    def is_new_conversation(self, conversation_id: str) -> bool:
        return len(self.memory.get_messages(conversation_id)) == 0

    async def _process_model_request_node(
        self,
        node: ModelRequestNode[None, str],
        run: AgentRun,
    ) -> ModelRequestNodeResult | EmptyNodeResult:
        text_parts = []
        tool_call_parts = []
        conversation_id = run.ctx.deps.conversation_id  # type: ignore
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
    ) -> ToolCallRequestResult | ToolResultNodeResult | EmptyNodeResult:
        conversation_id = run.ctx.deps.conversation_id  # type: ignore

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
                    )
                    self.memory.add_tool_result(
                        conversation_id=conversation_id,
                        tool_name=event.result.tool_name,
                        tool_result=event.result.content,
                        tool_call_id=event.tool_call_id,
                    )
                    return result

        return EmptyNodeResult(node_type="tool_call")

    def _post_error_message_safe(
        self, exc: Exception, deps: MCPHostDeps
    ) -> Exception | None:
        """Safely post error message and return any exception that occurred during posting."""
        try:
            self.post_error_message(exc, deps)
            return None
        except Exception as post_exc:
            return post_exc

    def _handle_exception_with_error_posting(
        self, exc: Exception, deps: MCPHostDeps
    ) -> NoReturn:
        """Handle exception by posting error message safely and raising appropriate chained exception."""
        post_error_exc = self._post_error_message_safe(exc, deps)

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

    async def run(
        self,
        user_prompt: str,
        deps: MCPHostDeps,
        message_history: list[ModelMessage] | None = None,
    ) -> MCPHostResult:
        try:
            conversation_id = deps.conversation_id
            async with self.agent.run_mcp_servers():
                async with self.agent.iter(
                    user_prompt=user_prompt,
                    message_history=message_history,
                    deps=deps,  # type: ignore
                ) as run:
                    if self.is_new_conversation(conversation_id):
                        self.post_conversation_start(deps)

                    async for node in run:
                        if Agent.is_model_request_node(node):
                            result = await self._process_model_request_node(node, run)
                            if isinstance(result, ModelRequestNodeResult):
                                if self.is_new_conversation(conversation_id):
                                    self.post_message(result, deps)
                                else:
                                    self.update_message(result, deps)
                        elif Agent.is_call_tools_node(node):
                            result = await self._process_call_tools_node(node, run)
                        elif Agent.is_end_node(node):
                            result = EmptyNodeResult(node_type="end")
                            self.post_conversation_end(deps)
                        else:
                            raise ValueError(f"Unknown node type: {node}")
        except ExceptionGroup as e:
            self._handle_exception_with_error_posting(e.exceptions[0], deps)
        except Exception as e:
            self._handle_exception_with_error_posting(e, deps)
        else:
            return MCPHostResult(
                conversation_id=deps.conversation_id,
                message_id=deps.message_id,
                message_history=to_jsonable_python(
                    self.memory.get_messages(deps.conversation_id)
                ),
            )
