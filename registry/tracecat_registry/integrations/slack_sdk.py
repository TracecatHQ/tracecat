"""Generic interface for Slack SDK."""

from typing import Annotated, Any

from pydantic import Field
from slack_sdk.web.async_client import AsyncWebClient

from tracecat_registry import RegistrySecret, registry, secrets

slack_secret = RegistrySecret(name="slack", keys=["SLACK_BOT_TOKEN"])
"""Slack bot token.

- name: `slack`
- keys:
    - `SLACK_BOT_TOKEN`
"""


@registry.register(
    default_title="Call Slack API",
    description="Instantiate a Slack client and call an API method.",
    display_group="Slack",
    doc_url="https://api.slack.com/methods",
    namespace="tools.slack",
    secrets=[slack_secret],
)
async def call_sdk(
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
    result = await getattr(client, sdk_method)(**params)
    return result


@registry.register(
    default_title="Call paginated Slack API",
    description="Instantiate a Slack client and call a paginated API method.",
    display_group="Slack",
    doc_url="https://api.slack.com/methods",
    namespace="tools.slack",
    secrets=[slack_secret],
)
async def call_sdk_paginated(
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
    cursor = None
    items = []
    params = params or {}
    while True:
        result = await getattr(client, sdk_method)(**params, cursor=cursor, limit=limit)
        items.extend(result["items"])
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return items
