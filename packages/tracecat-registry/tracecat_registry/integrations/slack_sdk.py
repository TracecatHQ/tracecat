"""Generic interface for Slack SDK."""

from typing import Annotated, Any, Literal, cast
import asyncio

from pydantic import Field
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_sdk.webhook.async_client import AsyncWebhookClient

from tracecat_registry import RegistrySecret, registry, secrets


slack_secret = RegistrySecret(name="slack", keys=["SLACK_BOT_TOKEN"])
"""Slack bot token.

- name: `slack`
- keys:
    - `SLACK_BOT_TOKEN`
"""


@registry.register(
    default_title="Call method",
    description="Instantiate a Slack client and call a Slack SDK method.",
    display_group="Slack SDK",
    doc_url="https://api.slack.com/methods",
    namespace="tools.slack_sdk",
    secrets=[slack_secret],
)
async def call_method(
    sdk_method: Annotated[
        str,
        Field(
            ...,
            description="Slack Python SDK method name (e.g. `chat_postMessage`)",
        ),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Slack Python SDK method parameters"),
    ] = None,
) -> dict[str, Any]:
    bot_token = secrets.get("SLACK_BOT_TOKEN")
    client = AsyncWebClient(token=bot_token)
    params = params or {}
    result: AsyncSlackResponse = await getattr(client, sdk_method)(**params)
    data = result.data
    return cast(dict[str, Any], data)


@registry.register(
    default_title="Call paginated method",
    description="Instantiate a Slack client and call a paginated Slack SDK method.",
    display_group="Slack SDK",
    doc_url="https://api.slack.com/apis/pagination#methods",
    namespace="tools.slack_sdk",
    secrets=[slack_secret],
)
async def call_paginated_method(
    sdk_method: Annotated[
        str,
        Field(
            ...,
            description="Slack Python SDK method name that supports cursor pagination (e.g. `conversations_history`)",
        ),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Slack Python SDK method parameters"),
    ] = None,
    key: Annotated[
        str | None,
        Field(..., description="Key to extract from the response."),
    ] = None,
    limit: Annotated[
        int,
        Field(
            ...,
            description="Maximum number of items to retrieve. Must be less than 1000",
        ),
    ] = 200,
) -> list[dict[str, Any]]:
    bot_token = secrets.get("SLACK_BOT_TOKEN")
    client = AsyncWebClient(token=bot_token)
    members = []
    params = params or {}
    key = None
    async for page in await getattr(client, sdk_method)(**params, limit=limit):
        data = page.data
        members.extend(data[key] if key else data)
    return members


### Other utilities


@registry.register(
    default_title="Lookup many users by email",
    description="Lookup users by emails. Returns a list of users found and a list of users not found.",
    display_group="Slack",
    doc_url="https://api.slack.com/methods/users.lookupByEmail",
    namespace="tools.slack",
    secrets=[slack_secret],
)
async def lookup_users_by_email(
    emails: Annotated[list[str], Field(..., description="List of user emails.")],
) -> dict[str, list[dict[str, Any] | str]]:
    bot_token = secrets.get("SLACK_BOT_TOKEN")
    client = AsyncWebClient(token=bot_token)

    async def lookup_single_email(email: str) -> tuple[bool, dict[str, Any] | str]:
        try:
            result = await client.users_lookupByEmail(email=email)
            return True, cast(dict[str, Any], result.data)["user"]
        except SlackApiError as e:
            if e.response["error"] == "users_not_found":
                return False, email
            else:
                raise e

    # Process all emails concurrently
    results = await asyncio.gather(*[lookup_single_email(email) for email in emails])

    # Separate results into found and not found
    found = [data for found_flag, data in results if found_flag]
    not_found = [data for found_flag, data in results if not found_flag]

    return {"found": found, "not_found": not_found}


### Webhook client for response_url


@registry.register(
    default_title="Post response",
    description="Post messsage back to Slack interaction via `response_url`.",
    display_group="Slack",
    doc_url="https://api.slack.com/interactivity/handling#message_responses",
    namespace="tools.slack_sdk",
)
async def post_response(
    url: Annotated[str, Field(..., description="Webhook URL.")],
    text: Annotated[
        str | None,
        Field(..., description="Text to send to the webhook."),
    ] = None,
    blocks: Annotated[
        list[dict[str, Any]] | None,
        Field(..., description="Blocks to send to the webhook."),
    ] = None,
    response_type: Annotated[
        Literal["in_channel", "ephemeral"],
        Field(..., description="Response type. Defaults to `ephemeral`."),
    ] = "ephemeral",
    replace_original: Annotated[
        bool,
        Field(..., description="Whether to replace the original message."),
    ] = False,
    thread_ts: Annotated[
        str | None,
        Field(
            ...,
            description="Thread timestamp. If None, defaults to the current timestamp.",
        ),
    ] = None,
) -> dict[str, Any]:
    client = AsyncWebhookClient(url=url)
    body = {
        "text": text,
        "blocks": blocks,
        "response_type": response_type,
        "replace_original": replace_original,
    }
    if thread_ts:
        body["thread_ts"] = thread_ts
    response = await client.send_dict(body)
    return {
        "status_code": response.status_code,
        "body": response.body,
    }
