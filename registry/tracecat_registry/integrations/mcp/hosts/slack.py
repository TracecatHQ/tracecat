import json
import uuid
from typing import Any, Annotated
from dataclasses import dataclass
from typing_extensions import Self, Doc

import diskcache as dc
import orjson
from pydantic import BaseModel, Field, computed_field, model_validator
from pydantic_ai.mcp import MCPServerHTTP

from tracecat.logger import logger
from tracecat_registry import registry, secrets
from tracecat_registry.integrations.mcp.agent import (
    MCPHost,
    MCPHostDeps,
    ModelRequestNodeResult,
    ToolCallRequestResult,
    ToolResultNodeResult,
    MessageStartResult,
)
from tracecat_registry.integrations.mcp.memory import FanoutCacheMemory
from tracecat_registry.integrations.slack_sdk import (
    call_method,
    format_buttons,
    slack_secret,
)
from tracecat_registry.integrations.pydantic_ai import PYDANTIC_AI_REGISTRY_SECRETS


# Global cache for Slack UI state (button interactions)
INTERACTION_CACHE = dc.FanoutCache(
    directory=".cache/slack_interactions", shards=8, timeout=0.05
)  # key=thread_ts, stores tool call info for button interactions


@dataclass
class SlackMCPHostDeps(MCPHostDeps):
    """Slack-specific dependencies extending MCPHostDeps."""

    user_id: str
    """Slack user ID who initiated the conversation."""
    channel_id: str
    """Slack channel ID where the conversation is happening."""

    @property
    def thread_ts(self) -> str:
        """Convenience property for conversation_id using Slack terminology."""
        return self.conversation_id

    @thread_ts.setter
    def thread_ts(self, value: str) -> None:
        """Set conversation_id using Slack terminology."""
        self.conversation_id = value

    @property
    def ts(self) -> str | None:
        """Convenience property for message_id using Slack terminology."""
        return self.message_id

    @ts.setter
    def ts(self, value: str | None) -> None:
        """Set message_id using Slack terminology."""
        self.message_id = value


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


class SlackHandlerResult(BaseModel):
    """Result from Slack event handlers that continue to agent processing."""

    deps: SlackMCPHostDeps
    user_prompt: str
    message_history: list[Any]


class SlackViewResultResponse(BaseModel):
    """Response from view result interactions that return immediately."""

    thread_ts: str
    ts: str | None
    action: str


class SlackMCPHost(MCPHost[SlackMCPHostDeps]):
    """Slack implementation of MCPHost."""

    def __init__(
        self,
        model_name: str,
        model_provider: str,
        mcp_servers: list,
        model_settings: dict[str, Any] | None = None,
        approved_tool_calls: list[str] | None = None,
    ) -> None:
        # Initialize memory
        memory = FanoutCacheMemory()
        super().__init__(
            model_name=model_name,
            model_provider=model_provider,
            memory=memory,
            mcp_servers=mcp_servers,
            model_settings=model_settings,
            approved_tool_calls=approved_tool_calls,
            deps_type=SlackMCPHostDeps,
        )

        # Slack-specific caches
        self.blocks_cache = dc.FanoutCache(
            directory=".cache/blocks", shards=8, timeout=0.05
        )  # key=ts

    async def post_message_start(self, deps: SlackMCPHostDeps) -> MessageStartResult:
        """Called when a new model request / assistant message starts."""
        thread_ts = deps.conversation_id

        # Get bot and user info to avoid triggering notifications
        bot_info = await call_method("auth_test")
        bot_id = bot_info["user_id"]

        # Get user info for display name
        user_info = await call_method("users_info", params={"user": deps.user_id})
        user_name = user_info["user"]["name"]

        # Get bot info for display name
        bot_user_info = await call_method("users_info", params={"user": bot_id})
        bot_name = bot_user_info["user"]["name"]

        # Create initial context message (no notifications)
        msg = f"_@{user_name} requested a conversation with <@{bot_id}|{bot_name}>_"

        blocks = [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": msg}],
            }
        ]

        # Post the initial message
        response = await call_method(
            "chat_postMessage",
            params={
                "channel": deps.channel_id,
                "thread_ts": thread_ts,
                "blocks": blocks,
            },
        )

        message_id = response["ts"]
        self.blocks_cache.set(message_id, blocks)

        logger.info(
            "Posted message start",
            thread_ts=thread_ts,
            ts=message_id,
            bot_name=bot_name,
            user_name=user_name,
        )

        return MessageStartResult(message_id=message_id)

    async def update_message(
        self, result: ModelRequestNodeResult, deps: SlackMCPHostDeps
    ) -> Self:
        """Update an existing message in Slack."""
        message = "".join(result.text_parts)
        blocks = self.blocks_cache.get(deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []

        # Check if most recent block is a tool call
        updated_blocks = [*blocks]
        if blocks and blocks[-1].get("block_id", "").startswith("tool_call:"):
            # Replace hourglass emoji with ok emoji
            last_block = blocks[-1].copy()
            last_block.pop("block_id", None)
            if last_block.get("type") == "context":
                last_block["elements"][0]["text"] = (
                    last_block["elements"][0]["text"]
                    .replace("⏳", "🆗")
                    .replace(":hourglass_flowing_sand:", ":ok:")
                )
            updated_blocks[-1] = last_block

            # Check if this is a new tool call
            new_tool_call = len(result.tool_call_parts) > 0
            if not new_tool_call:
                # Add new context block
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

        # Update the Slack message
        await call_method(
            "chat_update",
            params={
                "channel": deps.channel_id,
                "ts": deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(deps.message_id, updated_blocks)

        logger.info(
            "Updated message",
            thread_ts=deps.conversation_id,
            ts=deps.message_id,
            message=message,
        )

        return self

    async def request_tool_approval(
        self, result: ToolCallRequestResult, deps: SlackMCPHostDeps
    ) -> Self:
        """Request approval for a tool call via Slack buttons."""
        blocks = self.blocks_cache.get(deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []

        # Generate a tool call ID for this request
        tool_call_id = str(uuid.uuid4())

        # Cache the tool call info for later retrieval
        INTERACTION_CACHE.set(
            deps.conversation_id,
            {
                "tool_call_id": tool_call_id,
                "tool_name": result.name,
                "tool_args": result.args,
            },
        )

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
                        "text": f"> *⚙️ {result.name}*\n> ```\n{json.dumps(result.args, indent=2)}\n```",
                    },
                },
                buttons,
            ]
        )

        # Update the Slack message
        await call_method(
            "chat_update",
            params={
                "channel": deps.channel_id,
                "ts": deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(deps.message_id, updated_blocks)

        logger.info(
            "Requested tool approval",
            thread_ts=deps.conversation_id,
            ts=deps.message_id,
            tool_name=result.name,
            tool_call_id=tool_call_id,
        )

        return self

    async def post_tool_approval(
        self, result: ToolCallRequestResult, approved: bool, deps: SlackMCPHostDeps
    ) -> Self:
        """Update the message after tool approval/rejection."""
        blocks = self.blocks_cache.get(deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []
        updated_blocks = []

        # Find and update the tool call block
        for block in blocks:
            if block.get("block_id", "").startswith("tool_call:"):
                if approved:
                    msg = "⏳ Running tool..."
                    block = {
                        "type": "context",
                        "block_id": block["block_id"],
                        "elements": [{"type": "mrkdwn", "text": msg}],
                    }
                else:
                    msg = "⏭️ Skipped tool."
                    block = {
                        "type": "context",
                        "block_id": block["block_id"],
                        "elements": [{"type": "mrkdwn", "text": msg}],
                    }
            updated_blocks.append(block)

        # Update the Slack message
        await call_method(
            "chat_update",
            params={
                "channel": deps.channel_id,
                "ts": deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(deps.message_id, updated_blocks)

        logger.info(
            "Posted tool approval",
            thread_ts=deps.conversation_id,
            ts=deps.message_id,
            tool_name=result.name,
            approved=approved,
        )

        return self

    async def post_tool_result(
        self, result: ToolResultNodeResult, deps: SlackMCPHostDeps
    ) -> Self:
        """Post the result of a tool call."""
        blocks = self.blocks_cache.get(deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []
        updated_blocks = [*blocks]

        # Replace hourglass emoji with ok emoji in the last block
        if updated_blocks:
            last_block = updated_blocks[-1].copy()
            last_block.pop("block_id", None)
            if last_block.get("type") == "context":
                last_block["elements"][0]["text"] = (
                    last_block["elements"][0]["text"]
                    .replace("⏳", "🆗")
                    .replace(":hourglass_flowing_sand:", ":ok:")
                )
            updated_blocks[-1] = last_block

        # Add view result button (tool result is already cached in base class)
        if result.call_id:
            updated_blocks.append(
                {
                    "type": "actions",
                    "block_id": f"tool_result:{result.call_id}",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "✅ View tool result",
                                "emoji": True,
                            },
                            "value": result.call_id,  # Use call_id directly
                            "action_id": f"view_result:{result.call_id}",
                        }
                    ],
                }
            )

        # Update the Slack message
        await call_method(
            "chat_update",
            params={
                "channel": deps.channel_id,
                "ts": deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(deps.message_id, updated_blocks)

        logger.info(
            "Posted tool result",
            thread_ts=deps.conversation_id,
            ts=deps.message_id,
            tool_name=result.name,
            result_id=result.call_id,
        )

        return self

    async def post_message_end(self, deps: SlackMCPHostDeps) -> Self:
        """Post a final message when the conversation ends."""
        blocks = self.blocks_cache.get(deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []
        updated_blocks = [*blocks]

        # Add tip message
        updated_blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "💡 Tip: Mention the bot in the thread to continue the conversation.",
                },
            }
        )

        # Update the Slack message
        await call_method(
            "chat_update",
            params={
                "channel": deps.channel_id,
                "ts": deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(deps.message_id, updated_blocks)

        logger.info(
            "Posted message end",
            thread_ts=deps.conversation_id,
            ts=deps.message_id,
        )

        return self

    async def post_error_message(self, exc: Exception, deps: SlackMCPHostDeps) -> Self:
        """Post an error message to Slack."""
        blocks = self.blocks_cache.get(deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []

        msg = (
            "‼️ Unexpected error occurred. "
            "Please wait a moment then restart the conversation by mentioning the bot in the thread."
        )

        if blocks:
            # Check if the last block is a tool call and update it
            updated_blocks = [*blocks]
            last_block = blocks[-1]
            if last_block.get("block_id", "").startswith("tool_call:"):
                # Replace hourglass emoji with red cross emoji
                last_block["elements"][0]["text"] = (
                    last_block["elements"][0]["text"]
                    .replace("⏳", "❌")
                    .replace(":hourglass_flowing_sand:", "x")
                )
                updated_blocks[-1] = last_block

                # Update the Slack message
                await call_method(
                    "chat_update",
                    params={
                        "channel": deps.channel_id,
                        "ts": deps.message_id,
                        "blocks": updated_blocks,
                    },
                )

                self.blocks_cache.set(deps.message_id, updated_blocks)

        # Post error context block as a separate message in the thread
        error_blocks = [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": msg}],
            }
        ]

        await call_method(
            "chat_postMessage",
            params={
                "channel": deps.channel_id,
                "thread_ts": deps.conversation_id,
                "blocks": error_blocks,
            },
        )

        logger.error(
            "Posted error message",
            thread_ts=deps.conversation_id,
            ts=deps.message_id,
            error=str(exc),
        )

        return self

    async def handle_view_result_modal(self, payload: SlackInteractionPayload) -> None:
        """Handle opening a modal to display tool results."""
        # For view_result actions, the value is the call_id
        call_id = payload.result_id  # This is actually the call_id now
        tool_call_id = payload.tool_call_id

        if call_id is None or tool_call_id is None:
            raise ValueError("Missing call_id or tool_call_id from interaction payload")

        trigger_id = payload.trigger_id
        if trigger_id is None:
            raise ValueError("Cannot open modal: missing trigger_id")

        # Load result from base class cache using call_id
        tool_result = self.get_tool_result(call_id)
        if tool_result is None:
            tool_result = "Unable to retrieve tool result from cache."
        else:
            tool_result = str(tool_result)

        # Open modal with the tool result
        await self._open_tool_result_modal(
            trigger_id=trigger_id,
            tool_result=tool_result,
            tool_call_id=tool_call_id,
        )

    async def _open_tool_result_modal(
        self,
        trigger_id: str,
        tool_result: str,
        tool_call_id: str,
    ) -> None:
        """Open a modal with the tool result."""
        # Create modal view with a limit on text length to prevent Slack errors
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


def _setup_mcp_server(base_url: str, timeout: int) -> MCPServerHTTP:
    """Setup and configure the MCP server."""
    headers = secrets.get("MCP_HTTP_HEADERS")
    if headers is not None:
        headers = orjson.loads(headers)
    return MCPServerHTTP(base_url, headers=headers, timeout=timeout)


def _parse_trigger_payload(
    trigger: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Parse the trigger payload to extract Slack event or interaction payload."""
    if not isinstance(trigger, dict):
        raise ValueError(f"Invalid trigger type. Expected JSON object. Got {trigger!r}")

    if "payload" in trigger:
        slack_payload = orjson.loads(trigger["payload"])
        return None, slack_payload
    else:
        return trigger, None


async def _handle_app_mention(
    slack_event: dict[str, Any], slack_host: SlackMCPHost, log: Any
) -> SlackHandlerResult:
    """Handle Slack app mention events."""
    slack_event_payload = SlackEventPayload.model_validate(slack_event)

    # Create SlackMCPHostDeps
    deps = SlackMCPHostDeps(
        conversation_id=slack_event_payload.thread_ts,
        message_id=None,  # Will be set by post_message_start
        user_id=slack_event_payload.event.user,
        channel_id=slack_event_payload.event.channel,
    )

    user_prompt = slack_event_payload.user_prompt
    message_history = slack_host.memory.get_messages(deps.conversation_id)

    log = log.bind(
        thread_ts=deps.conversation_id,
        ts=slack_event_payload.ts,
        event_type="app_mention",
    )
    log.info("Processing app mention")

    # Add user message to memory
    slack_host.memory.add_user_message(deps.conversation_id, user_prompt)

    return SlackHandlerResult(
        deps=deps,
        user_prompt=user_prompt,
        message_history=message_history,
    )


async def _handle_view_result_interaction(
    slack_interaction_payload: SlackInteractionPayload,
    slack_host: SlackMCPHost,
    channel_id: str,
    log: Any,
) -> SlackViewResultResponse:
    """Handle view result button interactions."""
    deps = SlackMCPHostDeps(
        conversation_id=slack_interaction_payload.thread_ts,
        message_id=slack_interaction_payload.ts,
        user_id=slack_interaction_payload.user_id,
        channel_id=channel_id,
    )

    log = log.bind(
        thread_ts=deps.conversation_id,
        ts=deps.message_id,
        event_type="view_result",
    )
    log.info("Processing view_result interaction")

    await slack_host.handle_view_result_modal(slack_interaction_payload)

    return SlackViewResultResponse(
        thread_ts=deps.conversation_id,
        ts=deps.message_id,
        action="view_result",
    )


async def _handle_tool_approval_interaction(
    slack_interaction_payload: SlackInteractionPayload,
    slack_host: SlackMCPHost,
    channel_id: str,
    log: Any,
) -> SlackHandlerResult:
    """Handle tool approval/rejection button interactions."""
    deps = SlackMCPHostDeps(
        conversation_id=slack_interaction_payload.thread_ts,
        message_id=slack_interaction_payload.ts,
        user_id=slack_interaction_payload.user_id,
        channel_id=channel_id,
    )

    message_history = slack_host.memory.get_messages(deps.conversation_id)

    # Get cached tool call info
    cached_value = INTERACTION_CACHE.get(deps.conversation_id)
    tool_call: dict[str, Any] | None = (
        cached_value if isinstance(cached_value, dict) else None
    )
    if tool_call is None:
        raise ValueError(
            f"No cached tool call ID found for thread timestamp {deps.conversation_id}"
        )

    tool_name = tool_call["tool_name"]
    tool_args = tool_call["tool_args"]

    # Update the UI to show approval status
    await slack_host.post_tool_approval(
        result=ToolCallRequestResult(name=tool_name, args=tool_args),
        approved=slack_interaction_payload.action_value == "run",
        deps=deps,
    )

    # If approved, add to the approved tool calls for this agent run
    if slack_interaction_payload.action_value == "run":
        slack_host.add_approved_tool_call(tool_name, tool_args)

    log = log.bind(
        thread_ts=deps.conversation_id,
        ts=deps.message_id,
        tool_name=tool_name,
        event_type="interaction",
    )
    log.info("Processing tool approval interaction")

    # Generate appropriate user prompt based on action
    if slack_interaction_payload.action_value == "run":
        user_prompt = (
            "Run the previously approved tool call now. "
            "Return the complete result in a structured format. "
            "Summarize key findings briefly."
        )
    else:
        user_prompt = (
            "The user declined the tool call. "
            "Acknowledge this politely and continue helping with their original request. "
            "Only suggest alternative approaches if you cannot answer their question without tools."
        )

    return SlackHandlerResult(
        deps=deps,
        user_prompt=user_prompt,
        message_history=message_history,
    )


async def _handle_slack_interaction(
    slack_payload: dict[str, Any], slack_host: SlackMCPHost, channel_id: str, log: Any
) -> SlackHandlerResult | SlackViewResultResponse:
    """Handle Slack button interactions (view result or tool approval)."""
    slack_interaction_payload = SlackInteractionPayload.model_validate(slack_payload)

    # Check if this is a view_result action
    if slack_interaction_payload.action_value == "view_result":
        return await _handle_view_result_interaction(
            slack_interaction_payload, slack_host, channel_id, log
        )
    else:
        return await _handle_tool_approval_interaction(
            slack_interaction_payload, slack_host, channel_id, log
        )


@registry.register(
    default_title="MCP Slackbot",
    description="Chat with a MCP server using Slack.",
    display_group="MCP",
    doc_url="https://ai.pydantic.dev/mcp/client/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS, slack_secret],
    namespace="mcp.host",
)
async def slackbot(
    trigger: Annotated[dict[str, Any] | None, Doc("Webhook trigger payload")],
    channel_id: Annotated[
        str,
        Doc(
            "Slack channel ID of the channel where the user is interacting with the bot."
        ),
    ],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    base_url: Annotated[str, Doc("Base URL of the MCP server.")],
    timeout: Annotated[int, Doc("Initial connection timeout in seconds.")] = 10,
) -> dict[str, Any]:
    """MCP Slack chatbot using the new SlackMCPHost architecture."""
    log = logger.bind(
        channel_id=channel_id,
        event_type="slack_chat",
    )
    log.info("Starting Slack chat handler")

    # Setup MCP server and host
    server = _setup_mcp_server(base_url, timeout)
    slack_host = SlackMCPHost(
        mcp_servers=[server], model_name=model_name, model_provider=model_provider
    )

    # Parse trigger payload
    slack_event, slack_payload = _parse_trigger_payload(trigger)

    # Handle different types of Slack events
    if slack_event is not None:
        log.info("Received Slack event")
        handler_result = await _handle_app_mention(slack_event, slack_host, log)
        deps = handler_result.deps
        user_prompt = handler_result.user_prompt
        message_history = handler_result.message_history
        new_message = True

    elif slack_payload is not None:
        log.info("Received Slack interaction payload")
        interaction_result = await _handle_slack_interaction(
            slack_payload, slack_host, channel_id, log
        )
        new_message = False

        # If it's a view_result interaction, return early
        if isinstance(interaction_result, SlackViewResultResponse):
            return {
                "thread_ts": interaction_result.thread_ts,
                "ts": interaction_result.ts,
                "action": interaction_result.action,
            }

        deps = interaction_result.deps
        user_prompt = interaction_result.user_prompt
        message_history = interaction_result.message_history

    else:
        raise ValueError(
            "Either `slack_event` or `slack_payload` must be provided. Got null values for both."
        )

    # Run the agent
    result = await slack_host.run(
        user_prompt=user_prompt,
        new_message=new_message,
        deps=deps,
        message_history=message_history,
    )

    return result.model_dump()
