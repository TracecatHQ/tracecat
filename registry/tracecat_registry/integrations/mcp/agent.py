from abc import ABC
import hashlib
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
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
from typing import Any, Literal
from pydantic_ai.agent import ModelRequestNode, AgentRun, CallToolsNode

from tracecat_registry.integrations.mcp.memory import ShortTermMemory
from tracecat_registry import RegistrySecret


mcp_secret = RegistrySecret(
    name="mcp",
    optional_keys=["MCP_HTTP_HEADERS"],
    optional=True,
)
"""MCP headers.

- name: `mcp`
- optional_keys:
    - `MCP_HTTP_HEADERS`: Optional HTTP headers to send to the MCP server.
"""

anthropic_secret = RegistrySecret(
    name="anthropic",
    optional_keys=["ANTHROPIC_API_KEY"],
    optional=True,
)
"""Anthropic API key.

- name: `anthropic`
- optional_keys:
    - `ANTHROPIC_API_KEY`: Optional Anthropic API key.
"""

openai_secret = RegistrySecret(
    name="openai",
    optional_keys=["OPENAI_API_KEY"],
    optional=True,
)
"""OpenAI API key.

- name: `openai`
- optional_keys:
    - `OPENAI_API_KEY`: Optional OpenAI API key.
"""

gemini_secret = RegistrySecret(
    name="gemini",
    optional_keys=["GEMINI_API_KEY"],
    optional=True,
)
"""Gemini API key.

- name: `gemini`
- optional_keys:
    - `GEMINI_API_KEY`: Optional Gemini API key.
"""


bedrock_secret = RegistrySecret(
    name="amazon_bedrock",
    optional_keys=[
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
    ],
    optional=True,
)
"""Bedrock API key.

- name: `amazon_bedrock`
- optional_keys:
    - `AWS_ACCESS_KEY_ID`: Optional AWS access key ID.
    - `AWS_SECRET_ACCESS_KEY`: Optional AWS secret access key.
    - `AWS_SESSION_TOKEN`: Optional AWS session token.
    - `AWS_REGION`: Optional AWS region.
"""


class EmptyNodeResult(BaseModel):
    node_type: Literal["model_request", "tool_call", "tool_result", "end"]


class ModelRequestNodeResult(BaseModel):
    text_parts: list[str]
    tool_call_parts: list[str]


class ToolCallNodeResult(BaseModel):
    name: str
    args: str | dict[str, Any]

    @computed_field(return_type=str)
    @property
    def hash(self) -> str:
        return hashlib.md5(f"{self.name}:{self.args}".encode()).hexdigest()


class ToolResultNodeResult(BaseModel):
    name: str
    content: str | dict[str, Any]


class MCPHost(ABC):
    def __init__(self, agent: Agent, memory: ShortTermMemory):
        self.agent = agent
        self.memory = memory

    async def _process_model_request_node(
        self,
        node: ModelRequestNode[None, str],
        run: AgentRun,
    ) -> ModelRequestNodeResult | EmptyNodeResult:
        text_parts = []
        tool_call_parts = []
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
            return ModelRequestNodeResult(
                text_parts=text_parts,
                tool_call_parts=tool_call_parts,
            )
        return EmptyNodeResult(node_type="model_request")

    async def _process_call_tools_node(
        self, node: CallToolsNode[None, str], run: AgentRun
    ) -> ToolCallNodeResult | ToolResultNodeResult | EmptyNodeResult:
        async with node.stream(run.ctx) as handle_stream:
            async for event in handle_stream:
                if isinstance(event, FunctionToolCallEvent):
                    return ToolCallNodeResult(
                        name=event.part.tool_name,
                        args=event.part.args,
                    )
                elif isinstance(event, FunctionToolResultEvent) and isinstance(
                    event.result, ToolReturnPart
                ):
                    return ToolResultNodeResult(
                        name=event.result.tool_name,
                        content=event.result.content,
                    )
        return EmptyNodeResult(node_type="tool_call")

    async def run(
        self, user_prompt: str, message_history: list[ModelMessage] | None = None
    ):
        async with self.agent.run_mcp_servers():
            async with self.agent.iter(
                user_prompt=user_prompt, message_history=message_history
            ) as run:
                async for node in run:
                    if Agent.is_model_request_node(node):
                        result = await self._process_model_request_node(node, run)
                    elif Agent.is_call_tools_node(node):
                        result = await self._process_call_tools_node(node, run)
                    elif Agent.is_end_node(node):
                        result = EmptyNodeResult(node_type="end")
                    else:
                        raise ValueError(f"Unknown node type: {node}")

                    if isinstance(result, ModelRequestNodeResult):
                        pass
                    elif isinstance(result, ToolCallNodeResult):
                        pass
