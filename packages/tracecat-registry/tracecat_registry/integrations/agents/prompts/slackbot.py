import yaml
from datetime import UTC, datetime
import textwrap
from typing import Any, Iterable

from pydantic import BaseModel


class SlackPromptBase(BaseModel):
    """Base prompt builder for Slack agents."""

    channel_id: str
    response_instructions: str

    def _now_iso(self) -> str:
        return datetime.now(tz=UTC).isoformat()

    def _ts_to_datetime(self, ts: str | None) -> str:
        if not ts:
            return "unknown"
        timestamp_float = float(ts)
        return datetime.fromtimestamp(timestamp_float, tz=UTC).isoformat()

    def _format_messages(self, messages: Iterable[dict[str, Any]]) -> str:
        formatted: list[str] = []
        for message in messages:
            ts = self._ts_to_datetime(message.get("ts"))
            user = message.get("user") or message.get("username") or "unknown"
            text = message.get("text") or ""
            formatted.append(f"{user} [{ts}]: {text}".strip())
        return "\n".join(reversed([line for line in formatted if line]))

    @property
    def instructions(
        self,
    ) -> str:  # pragma: no cover - abstract pattern enforced in subclasses
        raise NotImplementedError

    @property
    def user_prompt(
        self,
    ) -> str:  # pragma: no cover - abstract pattern enforced in subclasses
        raise NotImplementedError


class SlackNoEventPrompts(SlackPromptBase):
    """Prompts when proactively posting to a channel with no triggering event."""

    initial_prompt: str

    @property
    def instructions(self) -> str:
        return textwrap.dedent(
            f"""
            You are preparing a proactive Slack message.
            Post a single update to channel <ChannelID>{self.channel_id}</ChannelID>.

            <TimeRightNow>{self._now_iso()}</TimeRightNow>

            <instructions>
            {self.response_instructions}
            </instructions>
        """
        )

    @property
    def user_prompt(self) -> str:
        return self.initial_prompt


class SlackAppMentionPrompts(SlackPromptBase):
    """Prompts when responding to an app mention inside Slack."""

    messages: list[dict[str, Any]]
    thread_ts: str
    trigger_ts: str
    trigger_user_id: str | None = None

    @property
    def instructions(self) -> str:
        trigger_user = self.trigger_user_id or "unknown"
        return textwrap.dedent(
            f"""
            You are replying to an app mention.
            Work strictly inside thread <ThreadTS>{self.thread_ts}</ThreadTS> in channel <ChannelID>{self.channel_id}</ChannelID>.

            <IMPORTANT>
            - Send exactly one reply using `tools.slack.post_message` with `thread_ts` set to {self.thread_ts}.
            - Do not send additional posts or duplicate replies unless the injected instructions explicitly require it.
            - Keep your tone helpful and concise; avoid echoing metadata from the block below unless requested.
            </IMPORTANT>

            Mention metadata (for your awareness):
            - user: {trigger_user}

            <instructions>
            {self.response_instructions}
            </instructions>
        """
        )

    @property
    def user_prompt(self) -> str:
        if not self.messages:
            raise ValueError("App mention prompts require message history.")
        return self._format_messages(self.messages)


class SlackInteractionPrompts(SlackPromptBase):
    """Prompts when handling interaction payloads (e.g., button presses)."""

    messages: list[dict[str, Any]]
    thread_ts: str
    trigger_ts: str
    actions: list[dict[str, Any]]
    acting_user_id: str | None = None
    callback_id: str | None = None
    response_url: str

    def _actions_yaml(self) -> str:
        return yaml.safe_dump(self.actions or [], sort_keys=False).strip()

    @property
    def instructions(self) -> str:
        trigger_time = self._ts_to_datetime(self.trigger_ts)
        acting_user = self.acting_user_id or "unknown"
        callback = self.callback_id or "n/a"
        actions_yaml = self._actions_yaml()
        response_url = self.response_url
        return textwrap.dedent(
            f"""
            You are replying to an interaction payload.
            Send your response in thread <ThreadTS>{self.thread_ts}</ThreadTS> for channel <ChannelID>{self.channel_id}</ChannelID>.

            Interaction details:
            - callback_id: {callback}
            - user: {acting_user}
            - triggered_at: {trigger_time}
            - response_url: {response_url}

            Actions YAML:
            ```yaml
            {actions_yaml}
            ```

            Use `tools.slack_sdk.post_response` to send the reply via the provided response_url and set `replace_original` to true so the original interactive message is replaced.
            The updated message MUST restate the original question and present the available options (as plain text or disabled buttons) so context is preserved for the channel.
            After replacing the original message, you MUST send a confirmation reply in thread <ThreadTS>{self.thread_ts}</ThreadTS> via `tools.slack.post_message` (include `thread_ts` in the tool call) to explain the outcome or next steps.
            Only call `tools.slack.update_message` if explicitly directed within the injected instructions block.

            <instructions>
            {self.response_instructions}
            </instructions>
        """
        )

    @property
    def user_prompt(self) -> str:
        if not self.messages:
            raise ValueError("Interaction prompts require message history.")
        return self._format_messages(self.messages)


SlackPrompts = (
    SlackPromptBase
    | SlackNoEventPrompts
    | SlackAppMentionPrompts
    | SlackInteractionPrompts
)

__all__ = [
    "SlackPromptBase",
    "SlackNoEventPrompts",
    "SlackAppMentionPrompts",
    "SlackInteractionPrompts",
    "SlackPrompts",
]
