import orjson
from pydantic import BaseModel, model_validator, Field
from pydantic_core import to_jsonable_python

import httpx
from tracecat_registry.integrations.pydantic_ai import build_agent
from tracecat_registry.integrations.slack_sdk import call_method

import diskcache as dc

from typing import Annotated, Any, Self
from typing_extensions import Doc

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


BLOCKS_CACHE = dc.FanoutCache(
    directory=".cache/blocks", shards=8, timeout=0.05
)  # key=ts
MESSAGE_CACHE = dc.FanoutCache(
    directory=".cache/messages", shards=8, timeout=0.05
)  # key=thread_ts
TOOL_CALLS_CACHE = dc.FanoutCache(
    directory=".cache/tool_calls", shards=8, timeout=0.05
)  # key=thread_ts


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
    name="bedrock",
    optional_keys=["BEDROCK_API_KEY"],
    optional=True,
)
"""Bedrock API key.

- name: `bedrock`
- optional_keys:
    - `BEDROCK_API_KEY`: Optional Bedrock API key.
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
        """Get the timestamp of the action block that was clicked."""
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
) -> tuple[PartStartEvent | PartDeltaEvent | None, str, list[dict[str, Any]]]:
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
    return event, ts, blocks


async def _process_call_tools_node(
    node,
    run,
    is_approved: bool,
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
    async with node.stream(run.ctx) as handle_stream:
        async for event in handle_stream:
            if isinstance(event, FunctionToolCallEvent):
                log.info("Requesting tool call", event=event)
                # Request approval for tool call
                tool_name = event.part.tool_name
                tool_args = event.part.args
                tool_call_id = event.call_id
                if not is_approved:
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
                    TOOL_CALLS_CACHE.set(thread_ts, tool_call_id)
                    return event, ts, blocks

                _add_tool_call_request(
                    thread_ts,
                    tool_name,
                    tool_args,
                    tool_call_id,
                )

            elif isinstance(event, FunctionToolResultEvent) and isinstance(
                event.result, ToolReturnPart
            ):
                # Update message with the result of the tool call
                tool_result = event.result.content
                tool_name = event.result.tool_name
                tool_call_id = event.tool_call_id
                log.info(
                    "Processing tool call result",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                )
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
                is_approved = False

    return event, ts, blocks


async def _run_agent(
    agent: Agent,
    *,
    ts: str,
    is_approved: bool,
    user_prompt: str,
    message_history: list[ModelMessage] | None,
    channel_id: str,
    thread_ts: str,
):
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
                        is_approved=is_approved,
                        ts=ts,
                        blocks=blocks,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                    )
                    is_approved = False
                    if isinstance(event, FunctionToolCallEvent):
                        log.info("Request human-in-the-loop for tool call", event=event)
                        return

                elif Agent.is_end_node(node):
                    blocks = BLOCKS_CACHE.get(ts, [])  # type: ignore
                    await _send_final_message(
                        blocks=blocks,
                        ts=ts,
                        channel_id=channel_id,
                    )
    return


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
        is_approved = False

    elif slack_payload is not None:
        # Approval buttons (assume the user has already started a conversation)
        slack_interaction_payload = SlackInteractionPayload.model_validate(
            slack_payload
        )
        thread_ts = slack_interaction_payload.thread_ts
        ts = slack_interaction_payload.ts
        user_prompt = "Run the tool."
        message_history = _get_message_history(thread_ts)

        # Get cached blocks and tool call ID
        blocks: list[dict[str, Any]] | None = BLOCKS_CACHE.get(ts)  # type: ignore
        tool_call_id: str | None = TOOL_CALLS_CACHE.get(thread_ts)  # type: ignore
        if blocks is None:
            raise ValueError(f"No cached blocks found for timestamp {ts}")
        if tool_call_id is None:
            raise ValueError(
                f"No cached tool call ID found for thread timestamp {thread_ts}"
            )
        log.info("Retrieved cached blocks", blocks=blocks, num_blocks=len(blocks))
        log.info("Retrieved cached tool call ID", tool_call_id=tool_call_id)

        log = log.bind(
            thread_ts=thread_ts,
            ts=ts,
            tool_call_id=tool_call_id,
            event_type="interaction",
        )
        log.info("Processing interaction")

        msg = await _disable_buttons(
            blocks=blocks,
            ts=slack_interaction_payload.ts,
            action_value=slack_interaction_payload.action_value,
            tool_call_id=tool_call_id,
            channel_id=channel_id,
        )
        ts, blocks = msg.ts, msg.blocks
        log.info("Updated blocks", blocks=blocks, num_blocks=len(blocks))
        BLOCKS_CACHE.set(ts, blocks)
        is_approved = slack_interaction_payload.action_value == "run"

    else:
        raise ValueError(
            "Either `slack_event` or `slack_payload` must be provided. Got null values for both."
        )

    # Run the agent and handle its response
    try:
        await _run_agent(
            agent=agent,
            ts=ts,
            is_approved=is_approved,
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

    return


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
    blocks.append(
        {
            "type": "section",
            "block_id": "message",
            "text": {"type": "mrkdwn", "text": message},
        }
    )

    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "text": message,
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
    blocks.extend(
        [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"> *⚙️ {tool_name}*\n> ```\n{orjson.dumps(tool_args, option=orjson.OPT_INDENT_2).decode()}\n```",
                },
            },
            buttons,
        ]
    )
    logger.info("Posting tool call approval", blocks=blocks)
    response = await call_method(
        "chat_update",
        params={
            "channel": channel_id,
            "ts": ts,
            "text": "Requesting tool call approval",
            "blocks": blocks,
        },
    )
    interaction_ts = response["ts"]
    return SlackMessage(ts=interaction_ts, blocks=blocks)


async def _disable_buttons(
    blocks: list[dict[str, Any]],
    ts: str,
    *,
    action_value: str,
    tool_call_id: str,
    channel_id: str,
) -> SlackMessage:
    """Disable the buttons for a tool call."""
    msg = None
    updated_blocks = []
    for block in blocks:
        if block.get("block_id") == f"tool_call:{tool_call_id}":
            if action_value == "run":
                msg = "⏳ Running tool..."
                block = {
                    "type": "context",
                    "block_id": f"tool_call:{tool_call_id}",
                    "elements": [{"type": "mrkdwn", "text": msg}],
                }
            elif action_value == "skip":
                msg = "⏭️ Skipped tool."
                block = {
                    "type": "context",
                    "block_id": f"tool_call:{tool_call_id}",
                    "elements": [{"type": "mrkdwn", "text": msg}],
                }
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
    updated_blocks = []
    for block in blocks:
        if block.get("block_id") == f"tool_call:{tool_call_id}":
            block = {
                "type": "section",
                "block_id": f"tool_call:{tool_call_id}",
                "text": {"type": "mrkdwn", "text": f"✅ {tool_result}"},
            }
        updated_blocks.append(block)

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
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"💡 Tip: Mention `@{bot_name}` in the thread to continue the conversation.",
            },
        }
    )
    response = await call_method(
        "chat_update", params={"channel": channel_id, "ts": ts, "blocks": blocks}
    )
    return SlackMessage(ts=response["ts"], blocks=blocks)
