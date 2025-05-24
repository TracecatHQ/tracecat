import orjson
from pydantic import BaseModel, model_validator, Field, computed_field
from pydantic_core import to_jsonable_python

import httpx
from tracecat_registry.integrations.pydantic_ai import build_agent
from tracecat_registry.integrations.slack_sdk import call_method

import diskcache as dc
import json

from typing import Annotated, Any, Self
from typing_extensions import Doc

import hashlib

from pydantic_ai.mcp import MCPServerHTTP
from pydantic_ai.agent import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartStartEvent,
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
from tracecat_registry.integrations.slack_sdk import format_buttons, slack_secret


from tracecat_registry import registry, RegistrySecret, secrets

from tracecat.logger import logger
import uuid


BLOCKS_CACHE = dc.FanoutCache(
    directory=".cache/blocks", shards=8, timeout=0.05
)  # key=ts
MESSAGE_CACHE = dc.FanoutCache(
    directory=".cache/messages", shards=8, timeout=0.05
)  # key=thread_ts
TOOL_CALLS_CACHE = dc.FanoutCache(
    directory=".cache/tool_calls", shards=8, timeout=0.05
)  # key=thread_ts
APPROVED_TOOLS_CACHE = dc.FanoutCache(
    directory=".cache/approved_tools", shards=8, timeout=0.05
)  # key=tool_name
TOOL_RESULTS_CACHE = dc.FanoutCache(
    directory=".cache/tool_results", shards=8, timeout=0.05
)  # key=tool_call_id


def _hash_tool_call(name: str, args: str | dict[str, Any]) -> str:
    """Hash a tool call.

    Note: we cannot use the tool call ID because it is not stored in
    the message history the first time it is requested (i.e. because of HiTL).
    The second time it is requested, a new tool call ID is generated even
    though the tool call name and arguments are the same.
    """
    return hashlib.md5(f"{name}:{args}".encode()).hexdigest()


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


class SlackMessage(BaseModel):
    """Slack message model for caching."""

    ts: str
    blocks: list[dict[str, Any]]


class SlackEventData(BaseModel):
    """Structured Slack event data model."""

    text: str
    user: str
    type: str
    blocks: list[dict[str, Any]]
    channel: str
    ts: str
    thread_ts: str | None = Field(default=None)


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

    @property
    def user_prompt(self) -> str:
        """Get the user prompt."""
        return self.event.text

    @property
    def blocks(self) -> list[dict[str, Any]]:
        """Get the message blocks."""
        return self.event.blocks


class SlackInteractionPayload(BaseModel):
    """Slack interaction payload model for extracting essential fields."""

    user: dict[str, Any]
    message: dict[str, Any]
    actions: list[dict[str, Any]]
    trigger_id: str | None = None

    @computed_field
    @property
    def user_id(self) -> str:
        """Get the ID of the user who interacted."""
        return self.user["id"]

    @computed_field
    @property
    def thread_ts(self) -> str:
        """Get the thread timestamp."""
        return self.message.get("thread_ts") or self.message["ts"]

    @computed_field
    @property
    def ts(self) -> str:
        """Get the timestamp of the action block that was clicked."""
        return self.message["ts"]

    @computed_field
    @property
    def blocks(self) -> list[dict[str, Any]]:
        """Get the message blocks."""
        return self.message["blocks"]

    @computed_field
    @property
    def action_id(self) -> str:
        """Get the action ID of the interaction."""
        if len(self.actions) != 1:
            raise ValueError(
                "Expected one action in Slack interaction payload."
                f"Got {len(self.actions)} actions: {self.actions}."
            )
        return self.actions[0]["action_id"]

    @computed_field
    @property
    def action_value(self) -> str:
        """Get the action value of the interaction."""
        if len(self.actions) != 1:
            raise ValueError(
                "Expected one action in Slack interaction payload."
                f"Got {len(self.actions)} actions: {self.actions}."
            )
        action_id = self.actions[0]["action_id"]
        value = self.actions[0]["value"]

        # Handle view_result action differently
        if action_id.startswith("view_result:"):
            return "view_result"

        if value not in ("run", "skip"):
            raise ValueError(
                f"Invalid action value. Expected 'run' or 'skip'. Got {value!r}."
            )
        return value

    @computed_field
    @property
    def tool_call_id(self) -> str | None:
        """Extract tool call ID from action_id if it's a view_result action."""
        action_id = self.action_id
        if action_id.startswith("view_result:"):
            return action_id.split(":", 1)[1]
        return None

    @computed_field
    @property
    def result_id(self) -> str | None:
        """Extract result ID from value if it's a view_result action."""
        if len(self.actions) != 1:
            raise ValueError(
                "Expected one action in Slack interaction payload."
                f"Got {len(self.actions)} actions: {self.actions}."
            )
        if self.action_value == "view_result":
            return self.actions[0]["value"]
        return None


def _get_message_history(thread_ts: str) -> list[ModelMessage]:
    """Get the message history for a thread."""
    return ModelMessagesTypeAdapter.validate_python(MESSAGE_CACHE.get(thread_ts, []))


# TODO: Move into tracecat_registry.integrations.pydantic_ai
# Consider updating _parse_message_history to handle tool calls


def _add_user_message(thread_ts: str, message: str) -> list[dict[str, Any]]:
    """Add a user message to the message history."""
    messages: list[dict[str, Any]] = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    user_prompt = ModelRequest.user_text_prompt(user_prompt=message)
    messages.append(to_jsonable_python(user_prompt))
    MESSAGE_CACHE.set(thread_ts, messages)
    logger.info(
        "Added user message to message history",
        thread_ts=thread_ts,
        messages=messages,
        num_messages=len(messages),
    )
    return messages


def _add_assistant_message(thread_ts: str, message: str) -> list[dict[str, Any]]:
    """Add an assistant message to the message history."""
    messages: list[dict[str, Any]] = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    assistant_response = ModelResponse(parts=[TextPart(content=message)])
    messages.append(to_jsonable_python(assistant_response))
    MESSAGE_CACHE.set(thread_ts, messages)
    logger.info(
        "Added assistant message to message history",
        thread_ts=thread_ts,
        messages=messages,
        num_messages=len(messages),
    )
    return messages


def _add_tool_call_request(
    thread_ts: str, tool_name: str, tool_args: str | dict[str, Any], tool_call_id: str
) -> list[dict[str, Any]]:
    """Add a tool call request to the message history."""
    messages: list[dict[str, Any]] = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    parts = [
        ToolCallPart(tool_name=tool_name, args=tool_args, tool_call_id=tool_call_id)
    ]
    tool_call = ModelResponse(parts=parts)  # type: ignore
    messages.append(to_jsonable_python(tool_call))
    MESSAGE_CACHE.set(thread_ts, messages)
    logger.info(
        "Added tool call request to message history",
        thread_ts=thread_ts,
        messages=messages,
        num_messages=len(messages),
    )
    return messages


def _add_tool_call_result(
    thread_ts: str, tool_name: str, tool_result: str, tool_call_id: str
) -> list[dict[str, Any]]:
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
    logger.info(
        "Added tool call result to message history",
        thread_ts=thread_ts,
        messages=messages,
        num_messages=len(messages),
    )
    return messages


async def _process_model_request_node(
    node,
    run,
    ts: str,
    blocks: list[dict[str, Any]],
    channel_id: str,
    thread_ts: str,
) -> tuple[PartStartEvent | PartDeltaEvent, str, list[dict[str, Any]]]:
    """Process a model request node."""
    log = logger.bind(
        ts=ts,
        thread_ts=thread_ts,
        channel_id=channel_id,
    )
    log.info("Processing model request node", node=node)
    message_parts = []
    async with node.stream(run.ctx) as handle_stream:
        async for event in handle_stream:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                message_parts.append(event.part.content)
            elif isinstance(event, PartDeltaEvent) and isinstance(
                event.delta, TextPartDelta
            ):
                message_parts.append(event.delta.content_delta)

    if ts is not None and len(blocks) > 0:
        # Update message (identify by ts) directly
        log.info("Updating existing message", message_parts=message_parts)
        msg = await _update_message(
            blocks=blocks,
            ts=ts,
            message_parts=message_parts,
            channel_id=channel_id,
        )
    else:
        # Post new message to thread or start a new conversation in channel
        log.info("Posting new message", message_parts=message_parts)
        msg = await _post_message(
            message_parts=message_parts,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )

    # Checkpoint
    ts, blocks = msg.ts, msg.blocks
    log.info(
        "Caching Slack blocks",
        blocks=blocks,
        num_blocks=len(blocks),
    )
    BLOCKS_CACHE.set(ts, blocks)
    _add_assistant_message(thread_ts, "".join(message_parts))

    if event is None:
        log.warning("No event was returned from the model request node.")

    return event, ts, blocks


async def _process_call_tools_node(
    node,
    run,
    ts: str,
    blocks: list[dict[str, Any]],
    channel_id: str,
    thread_ts: str,
) -> tuple[
    FunctionToolCallEvent | FunctionToolResultEvent | None, str, list[dict[str, Any]]
]:
    log = logger.bind(
        ts=ts,
        thread_ts=thread_ts,
        channel_id=channel_id,
    )
    log.info("Processing call tools node", node=node)
    event = None
    async with node.stream(run.ctx) as handle_stream:
        async for event in handle_stream:
            if isinstance(event, FunctionToolCallEvent):
                log.info("Requesting tool call", event=event)
                # Request approval for tool call
                tool_name = event.part.tool_name
                tool_args = event.part.args
                tool_call_id = event.call_id
                tool_call_hash = _hash_tool_call(tool_name, tool_args)
                is_approved = APPROVED_TOOLS_CACHE.get(tool_call_hash)
                if is_approved:
                    log.info(
                        "Approved tool call. Resetting cache for tool call request.",
                        tool_call_hash=tool_call_hash,
                    )
                    APPROVED_TOOLS_CACHE.delete(tool_call_hash)
                    _add_tool_call_request(
                        thread_ts,
                        tool_name,
                        tool_args,
                        tool_call_id,
                    )
                else:
                    msg = await _request_tool_approval(
                        blocks=blocks,
                        ts=ts,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_call_id=tool_call_id,
                        channel_id=channel_id,
                    )
                    ts, blocks = msg.ts, msg.blocks

                    # Checkpoint
                    logger.info(
                        "Caching Slack blocks",
                        ts=ts,
                        blocks=blocks,
                        num_blocks=len(blocks),
                    )
                    BLOCKS_CACHE.set(ts, blocks)
                    TOOL_CALLS_CACHE.set(
                        thread_ts,
                        {
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "tool_args": tool_args,
                        },
                    )
                    return event, ts, blocks

            elif isinstance(event, FunctionToolResultEvent) and isinstance(
                event.result, ToolReturnPart
            ):
                log.info("Processing tool call result", event=event)
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
                logger.info(
                    "Caching Slack blocks",
                    ts=ts,
                    blocks=blocks,
                    num_blocks=len(blocks),
                )
                BLOCKS_CACHE.set(ts, blocks)
                _add_tool_call_result(
                    thread_ts,
                    tool_name,
                    tool_result,
                    tool_call_id,
                )

    if event is None:
        log.warning("No event was returned from the call tools node.")

    return event, ts, blocks


async def _run_agent(
    agent: Agent,
    *,
    ts: str,
    user_prompt: str,
    message_history: list[ModelMessage] | None,
    channel_id: str,
    thread_ts: str,
) -> dict[str, Any]:
    log = logger.bind(
        ts=ts,
        thread_ts=thread_ts,
        channel_id=channel_id,
    )
    log.info(
        "🤖 Starting agent run",
        user_prompt=user_prompt,
        message_history=message_history,
    )
    async with agent.run_mcp_servers():
        async with agent.iter(
            user_prompt=user_prompt, message_history=message_history
        ) as run:
            async for node in run:
                blocks: list[dict[str, Any]] = BLOCKS_CACHE.get(ts, [])  # type: ignore
                log.info(
                    "Retrieved blocks from cache", blocks=blocks, num_blocks=len(blocks)
                )
                if Agent.is_model_request_node(node):
                    log.info("Processing model request node", node=node)
                    event, ts, blocks = await _process_model_request_node(
                        node=node,
                        run=run,
                        ts=ts,
                        blocks=blocks,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                    )

                elif Agent.is_call_tools_node(node):
                    log.info("Processing call tools node", node=node)
                    event, ts, blocks = await _process_call_tools_node(
                        node=node,
                        run=run,
                        ts=ts,
                        blocks=blocks,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                    )
                    if isinstance(event, FunctionToolCallEvent):
                        log.info("Request human-in-the-loop for tool call", event=event)
                        break

                elif Agent.is_end_node(node):
                    blocks = BLOCKS_CACHE.get(ts, [])  # type: ignore
                    await _send_final_message(
                        blocks=blocks,
                        ts=ts,
                        channel_id=channel_id,
                    )

    blocks = BLOCKS_CACHE.get(ts, [])  # type: ignore
    all_messages = MESSAGE_CACHE.get(thread_ts, [])  # type: ignore
    result = {
        "blocks": blocks,
        "all_messages": all_messages,
        "thread_ts": thread_ts,
        "ts": ts,
    }
    return result


@registry.register(
    default_title="(Experimental) MCP Slack chatbot",
    description="Chat with a MCP server using Slack.",
    display_group="MCP",
    doc_url="https://docs.pydantic.ai/mcp/server/http/",
    secrets=[
        mcp_secret,
        anthropic_secret,
        openai_secret,
        gemini_secret,
        bedrock_secret,
        slack_secret,
    ],
    namespace="experimental.mcp",
)
async def chat_slack(
    trigger: Annotated[dict[str, Any] | None, Doc("Webhook trigger payload")],
    channel_id: Annotated[
        str,
        Doc(
            "Slack channel ID of the channel where the user is interacting with the bot."
        ),
    ],
    agent_settings: Annotated[dict[str, Any], Doc("Agent settings")],
    base_url: Annotated[str, Doc("Base URL of the MCP server.")],
    timeout: Annotated[int, Doc("Initial connection timeout in seconds.")] = 10,
):
    log = logger.bind(
        channel_id=channel_id,
        event_type="slack_chat",
    )
    log.info("Starting Slack chat handler")

    headers = secrets.get("MCP_HTTP_HEADERS")
    if headers is not None:
        headers = orjson.loads(headers)
    server = MCPServerHTTP(base_url, headers=headers, timeout=timeout)
    agent = build_agent(**agent_settings, mcp_servers=[server])

    slack_event = None
    slack_payload = None
    if isinstance(trigger, dict):
        if "payload" in trigger:
            slack_payload = orjson.loads(trigger["payload"])
            log.info("Received Slack interaction payload")
        else:
            slack_event = trigger
            log.info("Received Slack event")
    else:
        raise ValueError(f"Invalid trigger type. Expected JSON object. Got {trigger!r}")

    if slack_event is not None:
        # App mentions (can either be a new conversation or a continuation)
        slack_event_payload = SlackEventPayload.model_validate(slack_event)
        thread_ts = slack_event_payload.thread_ts
        ts = slack_event_payload.ts
        user_prompt = slack_event_payload.user_prompt
        message_history = _get_message_history(thread_ts)

        log = log.bind(
            thread_ts=thread_ts,
            ts=ts,
            event_type="app_mention",
        )
        log.info("Processing app mention")

        # Add user message to message history
        _add_user_message(thread_ts, user_prompt)

    elif slack_payload is not None:
        # Approval buttons (assume the user has already started a conversation)
        slack_interaction_payload = SlackInteractionPayload.model_validate(
            slack_payload
        )
        thread_ts = slack_interaction_payload.thread_ts
        ts = slack_interaction_payload.ts

        # Check if this is a view_result action
        if slack_interaction_payload.action_value == "view_result":
            log = log.bind(
                thread_ts=thread_ts,
                ts=ts,
                event_type="view_result",
            )
            log.info("Processing view_result interaction")
            await _handle_view_result_modal(slack_interaction_payload)

            # Return early, as we don't need to run the agent for this interaction
            return {
                "thread_ts": thread_ts,
                "ts": ts,
                "action": "view_result",
            }

        user_prompt = "Run the tool."
        message_history = _get_message_history(thread_ts)

        # Get cached blocks and tool call ID
        blocks: list[dict[str, Any]] | None = BLOCKS_CACHE.get(ts)  # type: ignore
        tool_call: dict[str, str] | None = TOOL_CALLS_CACHE.get(thread_ts)  # type: ignore
        if blocks is None:
            raise ValueError(f"No cached blocks found for timestamp {ts}")
        if tool_call is None:
            raise ValueError(
                f"No cached tool call ID found for thread timestamp {thread_ts}"
            )
        log.info("Retrieved cached blocks", blocks=blocks, num_blocks=len(blocks))
        log.info("Retrieved cached tool call ID", tool_call=tool_call)

        tool_call_id = tool_call["tool_call_id"]
        tool_name = tool_call["tool_name"]
        tool_args = tool_call["tool_args"]

        log = log.bind(
            thread_ts=thread_ts,
            ts=ts,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_args=tool_args,
            event_type="interaction",
        )
        log.info("Processing interaction")

        msg = await _receive_approval(
            blocks=blocks,
            ts=slack_interaction_payload.ts,
            action_value=slack_interaction_payload.action_value,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            channel_id=channel_id,
        )
        ts, blocks = msg.ts, msg.blocks
        log.info("Updated blocks", blocks=blocks, num_blocks=len(blocks))
        BLOCKS_CACHE.set(ts, blocks)

    else:
        raise ValueError(
            "Either `slack_event` or `slack_payload` must be provided. Got null values for both."
        )

    # Run the agent and handle its response
    try:
        result = await _run_agent(
            agent=agent,
            ts=ts,
            user_prompt=user_prompt,
            message_history=message_history,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
    except ExceptionGroup as e:
        # Extract the first error from the exception group
        exc = e.exceptions[0]
        if isinstance(exc, httpx.ConnectError):
            raise ConnectionError(f"Failed to connect to MCP server: {exc!s}") from exc
        else:
            raise e

    return result


def _update_last_tool_block(last_block: dict[str, Any]) -> dict[str, Any]:
    updated_block = last_block.copy()  # Create a copy to avoid side effects
    # Drop block ID
    updated_block.pop("block_id", None)
    # Replace hourglass emoji with ok emoji
    if updated_block.get("type") == "context":
        updated_block["elements"][0]["text"] = (
            updated_block["elements"][0]["text"]
            .replace("⏳", "🆗")
            .replace(":hourglass_flowing_sand:", ":ok:")
        )
    return updated_block


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
            "text": msg,
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

    # Check if most recent block is a tool call
    updated_blocks = [*blocks]
    block_id = blocks[-1].get("block_id", "")
    if block_id.startswith("tool_call:"):
        # Replace hourglass emoji with ok emoji
        last_block = updated_blocks[-1]
        updated_block = _update_last_tool_block(last_block)
        updated_blocks[-1] = updated_block
        updated_blocks.append(
            {
                "type": "context",
                "block_id": f"tool_call:{uuid.uuid1()}",
                "elements": [{"type": "mrkdwn", "text": f"⏳ {message}"}],
            }
        )

    else:
        updated_blocks.append(
            {
                "type": "section",
                "block_id": f"message:{uuid.uuid1()}",
                "text": {"type": "mrkdwn", "text": message},
            }
        )

    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "text": message,
            "blocks": updated_blocks,
        },
    )
    return SlackMessage(ts=response["ts"], blocks=updated_blocks)


async def _request_tool_approval(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    tool_name: str,
    tool_args: str | dict[str, Any],
    tool_call_id: str,
    channel_id: str,
) -> SlackMessage:
    """Request approval from the user on Slack.

    Returns the thread timestamp of the interactive message that was posted.
    """
    buttons = format_buttons(
        [
            {
                "text": "➡️ Run tool",
                "action_id": f"run:{tool_call_id}",
                "value": "run",
                "style": "primary",
            },
            {
                "text": "Skip",
                "action_id": f"skip:{tool_call_id}",
                "value": "skip",
            },
        ],
        block_id=f"tool_call:{tool_call_id}",
    )

    updated_blocks = [*blocks]
    updated_blocks.extend(
        [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"> *⚙️ {tool_name}*\n> ```\n{json.dumps(tool_args, indent=2)}\n```",
                },
            },
            buttons,
        ]
    )
    logger.info("Posting tool call approval", blocks=updated_blocks)
    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "text": "Requesting tool call approval",
            "blocks": updated_blocks,
        },
    )
    interaction_ts = response["ts"]
    return SlackMessage(ts=interaction_ts, blocks=updated_blocks)


async def _receive_approval(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    action_value: str,
    tool_name: str,
    tool_args: str | dict[str, Any],
    tool_call_id: str,
    channel_id: str,
) -> SlackMessage:
    """Disable the buttons for a tool call."""
    msg = None
    updated_blocks = []
    tool_call_hash = _hash_tool_call(tool_name, tool_args)
    for block in blocks:
        if block.get("block_id") == f"tool_call:{tool_call_id}":
            if action_value == "run":
                msg = "⏳ Running tool..."
                block = {
                    "type": "context",
                    "block_id": f"tool_call:{tool_call_id}",
                    "elements": [{"type": "mrkdwn", "text": msg}],
                }
                APPROVED_TOOLS_CACHE.set(tool_call_hash, True)
            elif action_value == "skip":
                msg = "⏭️ Skipped tool."
                block = {
                    "type": "context",
                    "block_id": f"tool_call:{tool_call_id}",
                    "elements": [{"type": "mrkdwn", "text": msg}],
                }
                APPROVED_TOOLS_CACHE.set(tool_call_hash, False)
            else:
                raise ValueError(
                    f"Invalid Slack action value. Got {action_value!r}. Expected 'run' or 'skip'."
                )
        updated_blocks.append(block)

    if msg is None:
        raise ValueError(
            f"Expected Slack block with block_id {f'tool_call:{tool_call_id}'}. Got {blocks!r}."
        )

    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "text": msg,
            "blocks": updated_blocks,
        },
    )
    return SlackMessage(ts=response["ts"], blocks=updated_blocks)


async def _update_tool_approval(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    tool_result: str,
    tool_call_id: str,
    channel_id: str,
):
    """Update the message with the result of the tool call."""
    updated_blocks = [*blocks]

    # Store the result in cache instead of the button
    result_id = f"{tool_call_id}_{uuid.uuid4().hex[:8]}"
    TOOL_RESULTS_CACHE.set(result_id, tool_result)

    # Replace hourglass emoji with ok emoji
    last_block = updated_blocks[-1]
    updated_block = _update_last_tool_block(last_block)
    updated_blocks[-1] = updated_block

    # Add view result button
    updated_blocks.append(
        {
            "type": "actions",
            "block_id": f"tool_result:{tool_call_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "✅ View tool result",
                        "emoji": True,
                    },
                    "value": result_id,
                    "action_id": f"view_result:{tool_call_id}",
                }
            ],
        }
    )

    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "text": "Tool call completed",
            "blocks": updated_blocks,
        },
    )
    return SlackMessage(ts=response["ts"], blocks=updated_blocks)


async def _open_tool_result_modal(
    trigger_id: str,
    tool_result: str,
    tool_call_id: str,
):
    """Open a modal with the tool result."""
    # Create modal view with a limit on text length to prevent Slack errors
    # Slack has various limits including 3000 chars for text blocks
    max_length = 2900  # Safe limit for modal text blocks
    is_truncated = len(tool_result) > max_length
    if is_truncated:
        tool_result = tool_result[:max_length] + "..."

    modal_blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Tool Call ID:* `{tool_call_id}`"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{tool_result}\n```"},
        },
    ]

    modal_view = {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Tool Result"},
        "blocks": modal_blocks,
    }
    await call_method(
        "views_open",
        params={
            "trigger_id": trigger_id,
            "view": modal_view,
        },
    )


async def _send_final_message(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    channel_id: str,
) -> SlackMessage:
    """Send the final message to Slack."""
    # Get name of bot
    bot_id = (await call_method("auth_test"))["user_id"]
    bot_name = (await call_method("users_info", params={"user": bot_id}))["user"][
        "name"
    ]
    updated_blocks = [*blocks]
    updated_blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"💡 Tip: Mention `@{bot_name}` in the thread to continue the conversation.",
            },
        }
    )
    response = await call_method(
        "chat_update",
        params={"channel": channel_id, "ts": ts, "blocks": updated_blocks},
    )
    return SlackMessage(ts=response["ts"], blocks=updated_blocks)


async def _handle_view_result_modal(payload: SlackInteractionPayload) -> None:
    """Handle opening a modal to display tool results.

    Args:
        slack_interaction_payload: The validated Slack interaction payload

    Raises:
        ValueError: If required data is missing
        RuntimeError: If there's an error opening the modal
    """
    # Use computed properties instead of direct data manipulation
    result_id = payload.result_id
    tool_call_id = payload.tool_call_id

    if result_id is None or tool_call_id is None:
        error_msg = "Missing result_id or tool_call_id from interaction payload"
        raise ValueError(error_msg)

    # Process trigger_id immediately to avoid expiration
    # Trigger IDs expire within 3 seconds of the user action
    trigger_id = payload.trigger_id
    if trigger_id is None:
        error_msg = "Cannot open modal: missing trigger_id"
        raise ValueError(error_msg)

    # Load result from cache first, before any other async operations
    tool_result = None
    if result_id.startswith(tool_call_id + "_"):
        cached_result = TOOL_RESULTS_CACHE.get(result_id)
        if cached_result is not None:
            tool_result = str(cached_result)

    # If we couldn't get a result from cache, use a placeholder
    if tool_result is None:
        tool_result = "Unable to retrieve tool result from cache."

    # Open modal with minimal delay
    await _open_tool_result_modal(
        trigger_id=trigger_id,
        tool_result=tool_result,
        tool_call_id=tool_call_id,
    )
