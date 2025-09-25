from tracecat_registry.integrations.agents.prompts.slackbot import SlackbotPrompts
from tracecat_registry.integrations.slack_sdk import (
    call_method,
    is_app_mention_in_channel,
    is_app_mention_in_thread,
    slack_secret,
)
from tracecat.agent.runtime import run_agent

from tracecat_registry import registry
from tracecat_registry.core.agent import PYDANTIC_AI_REGISTRY_SECRETS
from tracecat.registry.fields import ActionType, TextArea
from typing_extensions import Doc
from typing import Annotated, Any


@registry.register(
    default_title="AI Slackbot",
    description="Agentic AI Slackbot with tool calling capabilities.",
    display_group="AI",
    doc_url="https://docs.slack.dev/reference/events/app_mention/",
    namespace="ai",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS, slack_secret],
)
async def slackbot(
    app_mention_event: Annotated[
        dict[str, Any],
        Doc("Slack app mention event passed in via Tracecat webhook TRIGGER."),
    ],
    instructions: Annotated[str, Doc("Instructions for the agent."), TextArea()],
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

    bot_actions = ["tools.slack.post_message"]
    if actions:
        bot_actions = list(set([*actions, *bot_actions]))

    # Determine is app mention in channel or thread
    channel_id = app_mention_event["event"]["channel"]
    if is_app_mention_in_channel(app_mention_event):
        # If in channel, list conversation history
        response = await call_method(
            sdk_method="conversations_history",
            params={"channel": channel_id, "limit": limit_messages},
        )
        thread_ts = app_mention_event["event"]["ts"]
        messages = response.get("messages", [])
    elif is_app_mention_in_thread(app_mention_event):
        # If in thread, list message replies
        thread_ts = app_mention_event["event"]["thread_ts"]
        response = await call_method(
            sdk_method="conversations_replies",
            params={"channel": channel_id, "ts": thread_ts, "limit": limit_messages},
        )
        messages = response.get("messages", [])
    else:
        raise ValueError("App mention event is not in channel or thread.")

    prompts = SlackbotPrompts(
        messages=messages,
        user_instructions=instructions,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )

    return await run_agent(
        user_prompt=prompts.user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        actions=bot_actions,
        instructions=prompts.instructions,
        model_settings=model_settings,
        retries=retries,
        base_url=base_url,
    )
