import orjson
from pydantic import BaseModel, model_validator
from pydantic_core import to_jsonable_python

from tracecat_registry.integrations.pydantic_ai import build_agent
from tracecat_registry.integrations.slack_sdk import call_method

import diskcache as dc

from typing import Annotated, Any, Self
from typing_extensions import Doc

from pydantic_ai.mcp import MCPServerHTTP
from pydantic_ai.agent import Agent
from pydantic_graph.nodes import End
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ModelMessagesTypeAdapter,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.result import FinalResult
from tracecat_registry.integrations.slack_sdk import format_buttons


from tracecat_registry import registry, RegistrySecret, secrets


BLOCKS_CACHE = dc.FanoutCache(shards=8, timeout=0.05)  # key=ts
MESSAGE_CACHE = dc.FanoutCache(shards=8, timeout=0.05)  # key=thread_ts
TOOL_CALLS_CACHE = dc.FanoutCache(shards=8, timeout=0.05)  # key=thread_ts


mcp_secret = RegistrySecret(name="mcp", optional_keys=["MCP_HTTP_HEADERS"])
"""MCP headers.

- name: `mcp`
- optional_keys:
    - `MCP_HTTP_HEADERS`: Optional HTTP headers to send to the MCP server.
"""


class SlackMessage(BaseModel):
    """Slack message model for caching."""

    ts: str
    blocks: list[dict[str, Any]]


class SlackEventData(BaseModel):
    """Structured Slack event data model."""

    text: str
    user: str
    type: str
    ts: str
    channel: str
    thread_ts: str


class SlackEventPayload(BaseModel):
    """Slack event payload model."""

    type: str
    event: SlackEventData

    @model_validator(mode="after")
    def validate_event_type(self) -> Self:
        """Validate this is a mention event."""
        if self.type != "event_callback" or self.event.type != "app_mention":
            raise ValueError(
                "Expected `type=event_callback` and `event.type=app_mention` in Slack event payload. "
                f"Got `type={self.type!r}` and `event.type={self.event.type!r}`."
            )
        return self

    @property
    def thread_ts(self) -> str:
        """Get the thread timestamp."""
        return self.event.thread_ts or self.event.ts

    @property
    def ts(self) -> str:
        """Get the message timestamp."""
        return self.event.ts


class SlackInteractionPayload(BaseModel):
    """Slack interaction payload model for extracting essential fields."""

    user: dict[str, Any]
    message: dict[str, Any]
    actions: list[dict[str, Any]]

    @property
    def user_id(self) -> str:
        """Get the ID of the user who interacted."""
        return self.user["id"]

    @property
    def thread_ts(self) -> str:
        """Get the thread timestamp."""
        return self.message.get("thread_ts") or self.message["ts"]

    @property
    def ts(self) -> str:
        """Get the message timestamp."""
        return self.message["ts"]

    @property
    def blocks(self) -> list[dict[str, Any]]:
        """Get the message blocks."""
        return self.message["blocks"]

    @property
    def action_value(self) -> str:
        """Get the action value of the interaction."""
        if len(self.actions) != 1:
            raise ValueError(
                "Expected one action in Slack interaction payload."
                f"Got {len(self.actions)} actions: {self.actions}."
            )
        value = self.actions[0]["value"]
        if value not in ("run", "skip"):
            raise ValueError(
                f"Invalid action value. Expected 'run' or 'skip'. Got {value!r}."
            )
        return value


def _get_message_history(thread_ts: str) -> list[ModelMessage]:
    """Get the message history for a thread."""
    return ModelMessagesTypeAdapter.validate_python(MESSAGE_CACHE.get(thread_ts, []))


# TODO: Move into tracecat_registry.integrations.pydantic_ai
# Consider updating _parse_message_history to handle tool calls


def _add_user_message(thread_ts: str, message: str) -> None:
    """Add a user message to the message history."""
    messages: list[dict[str, Any]] = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    user_prompt = ModelRequest.user_text_prompt(user_prompt=message)
    messages.append(to_jsonable_python(user_prompt))
    MESSAGE_CACHE.set(thread_ts, messages)


def _add_assistant_message(thread_ts: str, message: str) -> None:
    """Add an assistant message to the message history."""
    messages: list[dict[str, Any]] = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    assistant_response = ModelResponse(parts=[TextPart(content=message)])
    messages.append(to_jsonable_python(assistant_response))
    MESSAGE_CACHE.set(thread_ts, messages)


def _add_tool_call_request(
    thread_ts: str, tool_name: str, tool_args: str | dict[str, Any], tool_call_id: str
) -> None:
    """Add a tool call request to the message history."""
    messages: list[dict[str, Any]] = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    parts = [
        ToolCallPart(tool_name=tool_name, args=tool_args, tool_call_id=tool_call_id)
    ]
    tool_call = ModelResponse(parts=parts)  # type: ignore
    messages.append(to_jsonable_python(tool_call))
    MESSAGE_CACHE.set(thread_ts, messages)


def _add_tool_call_result(
    thread_ts: str, tool_name: str, tool_result: str, tool_call_id: str
) -> None:
    """Add a tool call result to the message history."""
    messages: list[dict[str, Any]] = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    parts = [
        ToolReturnPart(
            tool_name=tool_name, content=tool_result, tool_call_id=tool_call_id
        )
    ]
    tool_result = ModelRequest(parts=parts)  # type: ignore
    messages.append(to_jsonable_python(tool_result))
    MESSAGE_CACHE.set(thread_ts, messages)


async def _run_agent(
    agent: Agent,
    *,
    ts: str,
    user_id: str,
    user_prompt: str | None,
    message_history: list[ModelMessage] | None,
    channel_id: str,
    thread_ts: str,
) -> End[FinalResult[str]] | FunctionToolCallEvent | FunctionToolResultEvent:
    async with agent.run_mcp_servers():
        async with agent.iter(
            user_prompt=user_prompt, message_history=message_history
        ) as run:
            async for node in run:
                blocks: list[dict[str, Any]] = BLOCKS_CACHE.get(ts, [])  # type: ignore
                if Agent.is_model_request_node(node):
                    message_parts = []
                    async with node.stream(run.ctx) as handle_stream:
                        async for event in handle_stream:
                            if isinstance(event, PartDeltaEvent) and isinstance(
                                event.delta, TextPartDelta
                            ):
                                message_parts.append(event.delta.content_delta)

                    if ts is not None and len(blocks) > 0:
                        # Update message (identify by ts) directly
                        msg = await _update_message(
                            blocks=blocks,
                            ts=ts,
                            message_parts=message_parts,
                            channel_id=channel_id,
                        )
                    else:
                        # Post new message to thread or start a new conversation in channel
                        msg = await _post_message(
                            message_parts=message_parts,
                            channel_id=channel_id,
                            thread_ts=thread_ts,
                        )

                    # Checkpoint
                    BLOCKS_CACHE.set(msg.ts, msg.blocks)
                    _add_assistant_message(thread_ts, "".join(message_parts))

                elif Agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as handle_stream:
                        async for event in handle_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                # Request approval for tool call
                                tool_name = event.part.tool_name
                                tool_args = event.part.args
                                tool_call_id = (
                                    event.call_id
                                )  # Also used to identify the Slack block
                                msg = await _request_tool_approval(
                                    blocks=blocks,
                                    ts=ts,
                                    tool_name=tool_name,
                                    tool_args=tool_args,
                                    tool_call_id=tool_call_id,
                                    user_id=user_id,
                                    channel_id=channel_id,
                                )
                                ts, blocks = msg.ts, msg.blocks

                                # Checkpoint
                                BLOCKS_CACHE.set(ts, blocks)
                                TOOL_CALLS_CACHE.set(thread_ts, tool_call_id)
                                _add_tool_call_request(
                                    thread_ts, tool_name, tool_args, tool_call_id
                                )

                                # Exit the stream and wait for user response
                                return event

                            elif isinstance(
                                event, FunctionToolResultEvent
                            ) and isinstance(event.result, ToolReturnPart):
                                # Update message with the result of the tool call
                                tool_result = event.result.content
                                tool_name = event.result.tool_name
                                tool_call_id = event.tool_call_id
                                msg = await _update_tool_approval(
                                    blocks=blocks,
                                    ts=ts,
                                    tool_result=tool_result,
                                    tool_call_id=tool_call_id,
                                    channel_id=channel_id,
                                )
                                ts, blocks = msg.ts, msg.blocks
                                BLOCKS_CACHE.set(ts, blocks)
                                TOOL_CALLS_CACHE.delete(thread_ts)
                                _add_tool_call_result(
                                    thread_ts, tool_name, tool_result, tool_call_id
                                )

                elif Agent.is_end_node(node):
                    # Send final message
                    tip = "Tip: Mention `@Tracecat Bot` in the thread to continue the conversation."
                    blocks = BLOCKS_CACHE.get(ts, [])  # type: ignore
                    if len(blocks) == 0:
                        raise ValueError(
                            "Unexpected end of conversation. No message response from agent."
                        )

                    blocks.append(
                        {"type": "section", "text": {"type": "mrkdwn", "text": tip}}
                    )
                    await call_method(
                        "chat_update",
                        params={
                            "channel": channel_id,
                            "ts": ts,
                            "blocks": blocks,
                        },
                    )
                    return node

    if not isinstance(node, End):
        raise ValueError(
            f"Unexpected end of conversation. Agent stream ended with non-final result: {to_jsonable_python(node)!r}."
        )

    return node


@registry.register(
    default_title="(Experimental) MCP Slack chatbot",
    description="Chat with a MCP server using Slack.",
    display_group="MCP",
    doc_url="https://docs.pydantic.ai/mcp/server/http/",
    secrets=[mcp_secret],
    namespace="experimental.mcp",
)
async def chat_slack(
    slack_event: Annotated[dict[str, Any] | None, Doc("Slack event (app mentions)")],
    slack_payload: Annotated[
        dict[str, Any] | None, Doc("Slack interaction payload (approval buttons)")
    ],
    user_id: Annotated[
        str, Doc("Slack user ID of the user who is interacting with the bot.")
    ],
    channel_id: Annotated[
        str,
        Doc(
            "Slack channel ID of the channel where the user is interacting with the bot."
        ),
    ],
    url: Annotated[str, Doc("URL of the MCP server.")],
    timeout: Annotated[int, Doc("Initial connection timeout in seconds.")],
    agent_settings: Annotated[dict[str, Any], Doc("Agent settings")],
) -> list[dict[str, Any]]:
    headers = orjson.loads(secrets.get("MCP_HTTP_HEADERS"))
    server = MCPServerHTTP(url, headers=headers, timeout=timeout)
    agent = build_agent(**agent_settings, mcp_servers=[server])

    if slack_event is not None:
        # App mentions (can either be a new conversation or a continuation)
        slack_event_payload = SlackEventPayload.model_validate(slack_event)
        thread_ts = slack_event_payload.event.thread_ts
        ts = slack_event_payload.event.ts
        user_prompt = slack_event_payload.event.text
        message_history = _get_message_history(thread_ts)

    elif slack_payload is not None:
        # Approval buttons (assume the user has already started a conversation)
        slack_interaction_payload = SlackInteractionPayload.model_validate(
            slack_payload
        )
        thread_ts = slack_interaction_payload.thread_ts
        ts = slack_interaction_payload.ts
        user_prompt = None
        message_history = _get_message_history(thread_ts)
        blocks: list[dict[str, Any]] = BLOCKS_CACHE.get(ts, [])  # type: ignore
        if len(blocks) == 0:
            raise ValueError(f"No message history found for timestamp: {ts}.")

        tool_call_id: str | None = TOOL_CALLS_CACHE.get(thread_ts)  # type: ignore
        if tool_call_id is None:
            raise ValueError(f"No tool call request found for thread: {thread_ts}.")

        await _disable_buttons(
            blocks=blocks,
            ts=slack_interaction_payload.ts,
            action_value=slack_interaction_payload.action_value,
            tool_call_id=tool_call_id,
            channel_id=channel_id,
        )

    else:
        raise ValueError(
            "Either `slack_event` or `slack_payload` must be provided. Got null values for both."
        )

    # Run the agent and handle its response
    result = await _run_agent(
        agent=agent,
        ts=ts,
        user_id=user_id,
        user_prompt=user_prompt,
        message_history=message_history,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )

    return to_jsonable_python(result)


async def _post_message(
    message_parts: list[str],
    channel_id: str,
    thread_ts: str,
) -> SlackMessage:
    """Post a message to Slack."""
    msg = "".join(message_parts)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": msg}}]
    response = await call_method(
        "chat_postMessage",
        params={
            "channel": channel_id,
            "thread_ts": thread_ts,
            "blocks": blocks,
        },
    )
    return SlackMessage(ts=response["ts"], blocks=blocks)


async def _update_message(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    message_parts: list[str],
    channel_id: str,
) -> SlackMessage:
    """Send message parts to Slack."""
    message = "".join(message_parts)
    # Get most recent non-tool call block
    for block in reversed(blocks):
        if block["type"] == "section":
            break
    else:
        raise ValueError("No section block found.")

    blocks = [
        {
            "type": "section",
            "block_id": "message",
            "text": {"type": "mrkdwn", "text": message},
        }
    ]
    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "blocks": blocks,
        },
    )
    return SlackMessage(ts=response["ts"], blocks=blocks)


async def _disable_buttons(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    action_value: str,
    tool_call_id: str,
    channel_id: str,
) -> SlackMessage:
    """Disable the buttons for a tool call."""
    blocks = []
    for block in blocks:
        if block["block_id"] == f"tool_call:{tool_call_id}":
            if action_value == "run":
                block = {
                    "type": "section",
                    "block_id": f"tool_call:{tool_call_id}",
                    "text": {"type": "mrkdwn", "text": "⏳ Running tool..."},
                }
            elif action_value == "skip":
                block = {
                    "type": "section",
                    "block_id": f"tool_call:{tool_call_id}",
                    "text": {"type": "mrkdwn", "text": "⏭️ Skipped tool."},
                }
            else:
                raise ValueError(f"Invalid action value: {action_value}")
        blocks.append(block)

    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "blocks": blocks,
        },
    )
    return SlackMessage(ts=response["ts"], blocks=blocks)


async def _request_tool_approval(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    tool_name: str,
    tool_args: str | dict[str, Any],
    tool_call_id: str,
    user_id: str,
    channel_id: str,
) -> SlackMessage:
    """Request approval from the user on Slack.

    Returns the thread timestamp of the interactive message that was posted.
    """
    buttons = format_buttons(
        [
            {
                "text": "➡️ Run tool",
                "action_id": f"run:{user_id}",
                "value": "run",
                "style": "primary",
            },
            {
                "text": "Skip",
                "action_id": f"skip:{user_id}",
                "value": "skip",
            },
        ]
    )
    blocks.extend(
        [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"> *⚙️ {tool_name}*\n> ```\n{orjson.dumps(tool_args, option=orjson.OPT_INDENT_2).decode()}\n```",
                },
            },
            {
                "type": "actions",
                "block_id": f"tool_call:{tool_call_id}",
                "elements": buttons,
            },
        ]
    )
    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "blocks": blocks,
        },
    )
    interaction_ts = response["ts"]
    return SlackMessage(ts=interaction_ts, blocks=blocks)


async def _update_tool_approval(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    tool_result: str,
    tool_call_id: str,
    channel_id: str,
):
    """Update the message with the result of the tool call."""
    blocks = []
    for block in blocks:
        if block["block_id"] == f"tool_call:{tool_call_id}":
            block = {
                "type": "section",
                "block_id": f"tool_call:{tool_call_id}",
                "text": {"type": "mrkdwn", "text": f"✅ {tool_result}"},
            }
        blocks.append(block)

    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "blocks": blocks,
        },
    )
    return SlackMessage(ts=response["ts"], blocks=blocks)
