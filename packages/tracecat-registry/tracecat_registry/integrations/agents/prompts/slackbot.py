from typing import Any
from pydantic import BaseModel
import textwrap
from datetime import UTC, datetime


class SlackbotPrompts(BaseModel):
    """Prompts for the Slackbot."""

    channel_id: str
    messages: list[dict[str, Any]]
    user_instructions: str
    thread_ts: str

    def _ts_to_datetime(self, ts: str) -> str:
        """Convert a Slack timestamp to a datetime string."""
        # Slack timestamps are Unix timestamps as strings (e.g., '1758823640.279089')
        timestamp_float = float(ts)
        return datetime.fromtimestamp(timestamp_float, tz=UTC).isoformat()

    def _parse_blocks(self) -> str:
        """Parse the blocks into a list of text messages."""
        return "\n".join(
            reversed(
                [
                    f"{msg['user']} [{self._ts_to_datetime(msg['ts'])}]: {msg['text']}"
                    for msg in self.messages
                ]
            )
        )

    @property
    def instructions(self) -> str:
        """Build the instructions for the Slackbot."""
        return textwrap.dedent(f"""
            You are an expert Slackbot. You will be given a list of messages from a Slack channel or thread.
            You will need to:
            1. Analyze the messages to understand the context and purpose of the conversation.
            2. Use the tools__slack__post_message tool to respond to the last message where you were mentioned in the channel.

            Note: ALWAYS respond in a thread (i.e. you must define `thread_ts` in the tools__slack__post_message tool call).

            <ChannelID>{self.channel_id}</ChannelID>
            <ThreadTS>{self.thread_ts}</ThreadTS>
            <TimeRightNow>{datetime.now().isoformat()}</TimeRightNow>

            <instructions>
            {self.user_instructions}
            </instructions>
        """)

    @property
    def user_prompt(self) -> str:
        """Build the user prompt for the Slackbot."""
        return self._parse_blocks()
