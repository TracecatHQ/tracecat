"""AI Slackbot agent with three modes of messaging:

1. If no event is provided, the agent will send a message to the channel using the provided prompt.
2. If an app mention event is provided (assumed to be an non-empty dict), the agent will respond to the app mention in the channel or thread.
3. If an interaction payload (e.g. for button clicks) is provided (assumed to be a dict with payload field), the agent will respond to the interaction.

For deduplication, we use the SlackApiError: {'ok': False, 'error': 'already_reacted'} as a heuristic.
"""

import orjson
from tracecat_registry.integrations.agents.prompts.slackbot import (
    SlackAppMentionPrompts,
    SlackInteractionPrompts,
    SlackNoEventPrompts,
    SlackPrompts,
)
from tracecat_registry.integrations.slack_sdk import call_method, slack_secret
from tracecat.agent.runtime import run_agent

from tracecat_registry import registry
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS
from tracecat.registry.fields import ActionType, TextArea
from pydantic import BaseModel
from typing_extensions import Doc
from typing import Annotated, Any
from slack_sdk.errors import SlackApiError


async def _ack_event(channel_id: str, ts: str):
    try:
        await call_method(
            sdk_method="reactions_add",
            params={"channel": channel_id, "timestamp": ts, "name": "eyes"},
        )
    except SlackApiError as e:
        if e.response.get("error") == "already_reacted":
            raise ValueError(
                "Another agent has already reacted to this message. Please wait for them to finish."
            )
        else:
            raise e


async def _notify_error(channel_id: str, thread_ts: str | None, ts: str | None):
    # Remove the eyes emoji
    if ts:
        await call_method(
            sdk_method="reactions_remove",
            params={"channel": channel_id, "timestamp": ts, "name": "eyes"},
        )

    if ts:
        # Add the warning emoji
        await call_method(
            sdk_method="reactions_add",
            params={"channel": channel_id, "timestamp": ts, "name": "warning"},
        )

    await call_method(
        sdk_method="chat_postMessage",
        params={
            "channel": channel_id,
            "text": "I'm having trouble responding to your message. Please try again.",
            "thread_ts": thread_ts,
        },
    )


async def _remove_ack(channel_id: str, ts: str):
    await call_method(
        sdk_method="reactions_remove",
        params={"channel": channel_id, "timestamp": ts, "name": "eyes"},
    )


class AppMentionEvent(BaseModel):
    channel_id: str
    thread_ts: str
    ts: str
    messages: list[dict[str, Any]]
    user_id: str | None = None


async def _handle_app_mention(
    event: dict[str, Any], limit_messages: int
) -> AppMentionEvent:
    # App mention event
    try:
        event_body = event["event"]
        ts = event_body["ts"]
        thread_ts = event_body.get("thread_ts")
        channel_id = event_body["channel"]
        user_id = event_body.get("user")
    except KeyError as exc:
        raise ValueError("Expected 'ts', 'thread_ts', and 'channel' in event.") from exc

    # Add "eyes" emoji to the app mention message
    await _ack_event(channel_id, ts)

    if thread_ts:
        # If in thread, list message replies
        response = await call_method(
            sdk_method="conversations_replies",
            params={
                "channel": channel_id,
                "ts": thread_ts,
                "limit": limit_messages,
            },
        )
        messages = response.get("messages", [])
    else:
        # If in channel, list conversation history
        response = await call_method(
            sdk_method="conversations_history",
            params={"channel": channel_id, "limit": limit_messages},
        )
        messages = response.get("messages", [])
        # Set thread_ts to the latest message
        thread_ts = ts
    return AppMentionEvent(
        messages=messages,
        thread_ts=thread_ts,
        ts=ts,
        channel_id=channel_id,
        user_id=user_id,
    )


class InteractionPayloadEvent(BaseModel):
    channel_id: str
    thread_ts: str
    ts: str
    messages: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    user_id: str | None = None
    callback_id: str | None = None
    response_url: str


async def _handle_interaction_payload(
    event: dict[str, Any], limit_messages: int
) -> InteractionPayloadEvent:
    try:
        payload = orjson.loads(event["payload"])
        container = payload["container"]
        channel_id = container["channel_id"]
        ts = container["message_ts"]
        thread_ts = container.get("thread_ts", ts)
        actions = payload["actions"]
        callback_id = payload.get("callback_id")
        user_id = payload.get("user", {}).get("id")
        response_url = payload.get("response_url")
        if not response_url and payload.get("response_urls"):
            # Use the first response URL if multiple are provided
            response_url = payload["response_urls"][0].get("response_url")
    except KeyError as exc:
        raise ValueError(
            "Expected 'container', 'message_ts', and 'actions' in payload."
        ) from exc

    if not channel_id or not ts:
        raise ValueError("Interaction payload is missing channel or message timestamp.")

    if not response_url:
        raise ValueError(
            "Interaction payload is missing response_url for post_response tool."
        )

    # Add "eyes" emoji to the interaction payload message
    await _ack_event(channel_id, ts)

    if thread_ts:
        # If in thread, list message replies
        response = await call_method(
            sdk_method="conversations_replies",
            params={
                "channel": channel_id,
                "ts": thread_ts,
                "limit": limit_messages,
            },
        )
        messages = response.get("messages", [])
    else:
        # If in channel, list conversation history
        response = await call_method(
            sdk_method="conversations_history",
            params={"channel": channel_id, "limit": limit_messages},
        )
        messages = response.get("messages", [])
        # Set thread_ts to the latest message
        thread_ts = ts

    return InteractionPayloadEvent(
        thread_ts=thread_ts,
        ts=ts,
        channel_id=channel_id,
        messages=messages,
        actions=actions,
        user_id=user_id,
        callback_id=callback_id,
        response_url=response_url,
    )


@registry.register(
    default_title="AI Slackbot",
    description="Agentic AI Slackbot with tool calling capabilities.",
    display_group="AI",
    doc_url="https://docs.slack.dev/reference/events/app_mention/",
    namespace="ai",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS, slack_secret],
)
async def slackbot(
    event: Annotated[
        dict[str, Any] | None,
        Doc(
            "Slack app mention event or interaction payload (e.g. for button clicks) passed in via Tracecat webhook TRIGGER. If None, the agent will send a message to the channel."
        ),
    ],
    prompt: Annotated[
        str,
        Doc("Initial prompt for the agent. Used when no event is provided."),
        TextArea(),
    ],
    instructions: Annotated[
        str,
        Doc(
            "Instructions for the agent across proactive, app mention, and interaction flows."
        ),
        TextArea(),
    ],
    channel_id: Annotated[str, Doc("Channel ID to send the initial message to.")],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    actions: Annotated[
        list[str] | None,
        Doc(
            "Actions to include in the agent on top of the default Slack actions (e.g. 'tools.slack.post_message')."
        ),
        ActionType(multiple=True),
    ] = None,
    model_settings: Annotated[
        dict[str, Any] | None, Doc("Model settings for the agent.")
    ] = None,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 6,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
    limit_messages: Annotated[
        int, Doc("Max number of messages to look back in the conversation.")
    ] = 5,
) -> Any:
    if limit_messages > 20:
        raise ValueError("Cannot look back more than 20 messages in a conversation.")

    bot_actions = [
        "tools.slack.post_message",
        "tools.slack.update_message",
        "tools.slack_sdk.post_response",
    ]
    if actions:
        bot_actions = list(set([*actions, *bot_actions]))

    ts = None
    thread_ts = None

    prompts: SlackPrompts

    if event and ("event" in event or "payload" in event):
        if event.get("event", {}).get("type") == "app_mention":
            slackbot_event = await _handle_app_mention(event, limit_messages)
            channel_id = slackbot_event.channel_id
            thread_ts = slackbot_event.thread_ts
            ts = slackbot_event.ts
            prompts = SlackAppMentionPrompts(
                channel_id=channel_id,
                response_instructions=instructions,
                messages=slackbot_event.messages,
                thread_ts=thread_ts,
                trigger_ts=ts,
                trigger_user_id=slackbot_event.user_id,
            )
        elif "payload" in event:
            slackbot_event = await _handle_interaction_payload(event, limit_messages)
            channel_id = slackbot_event.channel_id
            thread_ts = slackbot_event.thread_ts
            ts = slackbot_event.ts
            prompts = SlackInteractionPrompts(
                channel_id=channel_id,
                response_instructions=instructions,
                messages=slackbot_event.messages,
                thread_ts=thread_ts,
                trigger_ts=ts,
                actions=slackbot_event.actions,
                acting_user_id=slackbot_event.user_id,
                callback_id=slackbot_event.callback_id,
                response_url=slackbot_event.response_url,
            )
        else:
            raise ValueError("Unsupported Slack event type.")
    else:
        prompts = SlackNoEventPrompts(
            channel_id=channel_id,
            response_instructions=instructions,
            initial_prompt=prompt,
        )

    try:
        response = await run_agent(
            user_prompt=prompts.user_prompt,
            model_name=model_name,
            model_provider=model_provider,
            actions=bot_actions,
            instructions=prompts.instructions,
            model_settings=model_settings,
            retries=retries,
            base_url=base_url,
        )
    except Exception as e:
        # Send unexpected error message to Slack with the thread_ts
        await _notify_error(channel_id, thread_ts, ts)
        raise e
    else:
        if ts:
            await _remove_ack(channel_id, ts)
    return response
