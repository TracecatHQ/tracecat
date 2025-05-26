import json
import uuid
from typing import Any
from dataclasses import dataclass

import diskcache as dc
from pydantic import BaseModel, Field, computed_field, model_validator
from typing_extensions import Self

from tracecat.logger import logger
from tracecat_registry.integrations.mcp.agent import (
    MCPHost,
    MCPHostDeps,
    ModelRequestNodeResult,
    ToolCallRequestResult,
    ToolResultNodeResult,
    MessageStartResult,
)
from tracecat_registry.integrations.mcp.memory import FanoutCacheMemory
from tracecat_registry.integrations.slack_sdk import call_method, format_buttons


@dataclass
class SlackMCPHostDeps:
    """Slack-specific dependencies extending MCPHostDeps."""

    conversation_id: str
    """`thread_ts` thread timestamp in Slack."""
    user_id: str
    """Slack user ID who initiated the conversation."""
    channel_id: str
    """Slack channel ID where the conversation is happening."""
    message_id: str | None = None
    """`ts` message timestamp in Slack (None for conversation start)."""


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


class SlackMCPHost(MCPHost):
    """Slack implementation of MCPHost."""

    def __init__(
        self,
        model_name: str,
        model_provider: str,
        mcp_servers: list,
        approved_tool_calls: list[str] | None = None,
        agent_settings: dict[str, Any] | None = None,
    ):
        # Initialize memory
        memory = FanoutCacheMemory()

        super().__init__(
            model_name=model_name,
            model_provider=model_provider,
            memory=memory,
            mcp_servers=mcp_servers,
            approved_tool_calls=approved_tool_calls,
            agent_settings=agent_settings,
        )

        # Slack-specific caches
        self.blocks_cache = dc.FanoutCache(
            directory=".cache/blocks", shards=8, timeout=0.05
        )  # key=ts
        # TODO: Implement this as an ABC class to interface with a global interactions table in the future
        # Should be managed directly by the MCPHost class
        self.tool_results_cache = dc.FanoutCache(
            directory=".cache/tool_results", shards=8, timeout=0.05
        )  # key=tool_call_id

    async def post_message_start(self, deps: MCPHostDeps) -> MessageStartResult:
        """Called when a new model request / assistant message starts."""
        # Cast to SlackMCPHostDeps for Slack-specific fields
        slack_deps = deps if isinstance(deps, SlackMCPHostDeps) else None
        if slack_deps is None:
            raise ValueError("SlackMCPHost requires SlackMCPHostDeps")

        thread_ts = slack_deps.conversation_id

        # Get bot and user info to avoid triggering notifications
        bot_info = await call_method("auth_test")
        bot_id = bot_info["user_id"]

        # Get user info for display name
        user_info = await call_method("users_info", params={"user": slack_deps.user_id})
        user_name = user_info["user"]["name"]

        # Get bot info for display name
        bot_user_info = await call_method("users_info", params={"user": bot_id})
        bot_name = bot_user_info["user"]["name"]

        # Create initial context message (no notifications)
        msg = f"@{bot_name} is conversing with @{user_name}"

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
                "channel": slack_deps.channel_id,
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
        self, result: ModelRequestNodeResult, deps: MCPHostDeps
    ) -> None:
        """Update an existing message in Slack."""
        slack_deps = deps if isinstance(deps, SlackMCPHostDeps) else None
        if slack_deps is None:
            raise ValueError("SlackMCPHost requires SlackMCPHostDeps")

        message = "".join(result.text_parts)
        blocks = self.blocks_cache.get(slack_deps.message_id, [])
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
                "channel": slack_deps.channel_id,
                "ts": slack_deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(slack_deps.message_id, updated_blocks)

        logger.info(
            "Updated message",
            thread_ts=slack_deps.conversation_id,
            ts=slack_deps.message_id,
            message=message,
        )

    async def request_tool_approval(
        self, result: ToolCallRequestResult, deps: MCPHostDeps
    ) -> None:
        """Request approval for a tool call via Slack buttons."""
        slack_deps = deps if isinstance(deps, SlackMCPHostDeps) else None
        if slack_deps is None:
            raise ValueError("SlackMCPHost requires SlackMCPHostDeps")

        blocks = self.blocks_cache.get(slack_deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []

        # Generate a tool call ID for this request
        tool_call_id = str(uuid.uuid4())

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
                "channel": slack_deps.channel_id,
                "ts": slack_deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(slack_deps.message_id, updated_blocks)

        logger.info(
            "Requested tool approval",
            thread_ts=slack_deps.conversation_id,
            ts=slack_deps.message_id,
            tool_name=result.name,
            tool_call_id=tool_call_id,
        )

    async def post_tool_approval(
        self, result: ToolCallRequestResult, approved: bool, deps: MCPHostDeps
    ) -> None:
        """Update the message after tool approval/rejection."""
        slack_deps = deps if isinstance(deps, SlackMCPHostDeps) else None
        if slack_deps is None:
            raise ValueError("SlackMCPHost requires SlackMCPHostDeps")

        blocks = self.blocks_cache.get(slack_deps.message_id, [])
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
                "channel": slack_deps.channel_id,
                "ts": slack_deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(slack_deps.message_id, updated_blocks)

        logger.info(
            "Posted tool approval",
            thread_ts=slack_deps.conversation_id,
            ts=slack_deps.message_id,
            tool_name=result.name,
            approved=approved,
        )

    async def post_tool_result(
        self, result: ToolResultNodeResult, deps: MCPHostDeps
    ) -> None:
        """Post the result of a tool call."""
        slack_deps = deps if isinstance(deps, SlackMCPHostDeps) else None
        if slack_deps is None:
            raise ValueError("SlackMCPHost requires SlackMCPHostDeps")

        blocks = self.blocks_cache.get(slack_deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []
        updated_blocks = [*blocks]

        # Store the result in cache for modal display
        result_id = (
            f"{result.call_id}_{uuid.uuid4().hex[:8]}"
            if result.call_id
            else str(uuid.uuid4())
        )
        self.tool_results_cache.set(result_id, result.content)

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

        # Add view result button
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
                            "value": result_id,
                            "action_id": f"view_result:{result.call_id}",
                        }
                    ],
                }
            )

        # Update the Slack message
        await call_method(
            "chat_update",
            params={
                "channel": slack_deps.channel_id,
                "ts": slack_deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(slack_deps.message_id, updated_blocks)

        logger.info(
            "Posted tool result",
            thread_ts=slack_deps.conversation_id,
            ts=slack_deps.message_id,
            tool_name=result.name,
            result_id=result_id,
        )

    async def post_message_end(self, deps: MCPHostDeps) -> None:
        """Post a final message when the conversation ends."""
        slack_deps = deps if isinstance(deps, SlackMCPHostDeps) else None
        if slack_deps is None:
            raise ValueError("SlackMCPHost requires SlackMCPHostDeps")

        blocks = self.blocks_cache.get(slack_deps.message_id, [])
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
                "channel": slack_deps.channel_id,
                "ts": slack_deps.message_id,
                "blocks": updated_blocks,
            },
        )

        self.blocks_cache.set(slack_deps.message_id, updated_blocks)

        logger.info(
            "Posted message end",
            thread_ts=slack_deps.conversation_id,
            ts=slack_deps.message_id,
        )

    async def post_error_message(self, exc: Exception, deps: MCPHostDeps) -> None:
        """Post an error message to Slack."""
        slack_deps = deps if isinstance(deps, SlackMCPHostDeps) else None
        if slack_deps is None:
            raise ValueError("SlackMCPHost requires SlackMCPHostDeps")

        blocks = self.blocks_cache.get(slack_deps.message_id, [])
        if not isinstance(blocks, list):
            blocks = []

        msg = (
            "‼️ Unexpected error occurred. "
            "Please wait a moment and mention the bot in the thread to continue the conversation."
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
                        "channel": slack_deps.channel_id,
                        "ts": slack_deps.message_id,
                        "blocks": updated_blocks,
                    },
                )

                self.blocks_cache.set(slack_deps.message_id, updated_blocks)

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
                "channel": slack_deps.channel_id,
                "thread_ts": slack_deps.conversation_id,
                "blocks": error_blocks,
            },
        )

        logger.error(
            "Posted error message",
            thread_ts=slack_deps.conversation_id,
            ts=slack_deps.message_id,
            error=str(exc),
        )

    async def handle_view_result_modal(self, payload: SlackInteractionPayload) -> None:
        """Handle opening a modal to display tool results."""
        result_id = payload.result_id
        tool_call_id = payload.tool_call_id

        if result_id is None or tool_call_id is None:
            raise ValueError(
                "Missing result_id or tool_call_id from interaction payload"
            )

        trigger_id = payload.trigger_id
        if trigger_id is None:
            raise ValueError("Cannot open modal: missing trigger_id")

        # Load result from cache
        tool_result = self.tool_results_cache.get(result_id)
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
