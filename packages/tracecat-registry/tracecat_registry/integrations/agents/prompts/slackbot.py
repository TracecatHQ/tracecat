import textwrap
import yaml
from datetime import UTC, datetime
from typing import Any, Iterable

from pydantic import BaseModel


class SlackPromptBase(BaseModel):
    """Shared helpers for building Slack agent prompts."""

    channel_id: str
    response_instructions: str

    def _now_iso(self) -> str:
        return datetime.now(tz=UTC).isoformat()

    def _ts_to_datetime(self, ts: str | None) -> str:
        if not ts:
            return "unknown"
        try:
            timestamp_float = float(ts)
        except (TypeError, ValueError):
            return "unknown"
        return datetime.fromtimestamp(timestamp_float, tz=UTC).isoformat()

    def _format_messages(self, messages: Iterable[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            ts_iso = self._ts_to_datetime(message.get("ts"))
            user = (
                message.get("user")
                or message.get("username")
                or message.get("bot_id")
                or "unknown"
            )
            text = message.get("text") or ""
            line = f"{user} [{ts_iso}]: {text}".strip()
            if line:
                lines.append(line)
        if not lines:
            return ""
        return "\n".join(reversed(lines))

    @property
    def instructions(
        self,
    ) -> str:  # pragma: no cover - subclasses must implement
        raise NotImplementedError

    @property
    def user_prompt(
        self,
    ) -> str:  # pragma: no cover - subclasses must implement
        raise NotImplementedError


class SlackNoEventPrompts(SlackPromptBase):
    """Prompt configuration when proactively posting to a channel."""

    initial_prompt: str

    @property
    def instructions(self) -> str:
        return textwrap.dedent(
            f"""
            You are an expert Slackbot preparing a proactive update for channel <ChannelID>{self.channel_id}</ChannelID>.

            Steps:
            1. Study the planning instructions block.
            2. Check if you're already responded to the most recent message in the thread. If you have, stop and summarize what you've posted as the final agent output.
            3. Compose exactly one Slack message that satisfies those instructions.
            4. Call `tools.slack.post_message` once with `channel` set to `{self.channel_id}` and do not provide `thread_ts`.
            5. Immediately after the tool call, end the run by emitting the literal word `DONE` as your final assistant message (do not send it to Slack).

            <TimeRightNow>{self._now_iso()}</TimeRightNow>

            <IMPORTANT>
            - Post only once; after the tool call, stop.
            - Do not fabricate additional context beyond what the instructions provide.
            - Keep the tone clear, professional, and tailored to the audience described.
            - Never call any additional tools after you've posted once; the closing `DONE` text completes the run.
            </IMPORTANT>

            <TaggingUsers>
            You can tag users in the channel by using their user ID in the format `<@some-user-id>`.
            </TaggingUsers>

            <instructions>
            {self.response_instructions}
            </instructions>
        """
        )

    @property
    def user_prompt(self) -> str:
        return self.initial_prompt


class SlackAppMentionPrompts(SlackPromptBase):
    """Prompt configuration when replying to an app mention."""

    messages: list[dict[str, Any]]
    thread_ts: str
    trigger_ts: str
    trigger_user_id: str | None = None

    @property
    def instructions(self) -> str:
        trigger_user = self.trigger_user_id or "unknown"
        trigger_time = self._ts_to_datetime(self.trigger_ts)
        return textwrap.dedent(
            f"""
            You are an expert Slackbot responding to a user who mentioned you in channel <ChannelID>{self.channel_id}</ChannelID>.
            The conversation transcript is attached for context.

            Steps:
            1. Review the transcript to understand the situation and the latest app mention that triggered you at <TriggerTS>{trigger_time}</TriggerTS>.
            2. Check if you're already responded to the most recent message in the thread. If you have, stop and summarize what you've posted as the final agent output.
            3. Formulate a concise, helpful reply that resolves the user's request.
            4. Call `tools.slack.post_message` exactly once with `channel` set to `{self.channel_id}` and `thread_ts` set to `{self.thread_ts}` so your reply stays in the thread.
            5. Immediately after the tool call, output the single token `DONE` as your final assistant message (not via a Slack tool) and halt.

            <ThreadTS>{self.thread_ts}</ThreadTS>
            <TriggerTS>{self.trigger_ts}</TriggerTS>
            <MentionedUser>{trigger_user}</MentionedUser>
            <TimeRightNow>{self._now_iso()}</TimeRightNow>

            <IMPORTANT>
            - Respond only once and stop immediately after the tool call.
            - Address the newest user message directly; do not repeat earlier bot output or loop on prior prompts.
            - Keep the tone friendly, confident, and appropriately brief.
            - After posting, do not call any other tools; the `DONE` message ends the run.
            </IMPORTANT>

            <TaggingUsers>
            You can tag users in the channel by using their user ID in the format `<@some-user-id>`.
            </TaggingUsers>

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
    """Prompt configuration when handling interactive payloads (buttons, menus, etc.)."""

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
        acting_user = self.acting_user_id or "unknown"
        callback = self.callback_id or "n/a"
        actions_yaml = self._actions_yaml()
        return textwrap.dedent(
            f"""
            You are handling a Slack interaction payload inside channel <ChannelID>{self.channel_id}</ChannelID>.
            The current conversation context and action summary are provided.

            Steps:
            1. Review the transcript and payload details to understand what the user needs.
            2. Check if you've already responded to the most recent message in the thread. If you have, stop and summarize what you've posted as the final agent output.
            3. Use `tools.slack_sdk.post_response` once to update the interactive message via <ResponseURL>{self.response_url}</ResponseURL>. Set `replace_original` to true. Update the message to show who performed the action and what action they took.
            4. After updating the interactive message, call `tools.slack.post_message` exactly once with `thread_ts` set to `{self.thread_ts}` and `channel` set to `{self.channel_id}` to provide a follow-up response that explains:
               - Who did what (reference the acting user and their specific action)
               - What happens next (clear next steps or outcomes)
            5. Immediately after the confirmation reply, output the literal word `DONE` as your final assistant message (not via Slack) to finish the run.

            <ThreadTS>{self.thread_ts}</ThreadTS>
            <TriggerTS>{self.trigger_ts}</TriggerTS>
            <CallbackID>{callback}</CallbackID>
            <ActingUser>{acting_user}</ActingUser>
            <TimeRightNow>{self._now_iso()}</TimeRightNow>

            Interaction actions:
            ```yaml
            {actions_yaml}
            ```

            <IMPORTANT>
            - Maintain the order: update the interactive message first, then post exactly one confirmation reply in the thread.
            - Do not call `tools.slack.update_message` unless the injected instructions explicitly require it.
            - Stop immediately after the confirmation reply and emit the standalone `DONE` message to close the run.
            </IMPORTANT>

            <TaggingUsers>
            You can tag users in the channel by using their user ID in the format `<@some-user-id>`.
            </TaggingUsers>

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
