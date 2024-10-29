"""Generic interface for Slack SDK."""

from typing import Annotated, Any

from pydantic import Field
from slack_sdk.web.async_client import AsyncWebClient

from tracecat_registry import RegistrySecret, registry, secrets

slack_secret = RegistrySecret(name="slack", keys=["SLACK_BOT_TOKEN"])
"""Slack secret.

- name: `slack`
- keys:
    - `SLACK_BOT_TOKEN`
"""


@registry.register(
    default_title="Call Slack API",
    description="Call any Slack API using the Slack Python SDK",
    display_group="Slack",
    namespace="integrations.slack",
    secrets=[slack_secret],
)
async def call_slack_api(
    sdk_method: Annotated[
        str,
        Field(
            ..., description="Slack Python SDK method name (e.g. `chat_postMessage`)"
        ),
    ],
    params: Annotated[
        dict, Field(..., description="Slack Python SDK method parameters")
    ],
) -> dict[str, Any]:
    bot_token = secrets.get("SLACK_BOT_TOKEN")
    client = AsyncWebClient(token=bot_token)
    result = await getattr(client, sdk_method)(**params)
    return result


@registry.register(
    default_title="Call Paginated Slack API",
    description="Call any Slack API that supports cursor / pagination using the Slack Python SDK and retrieve all items",
    display_group="Slack",
    namespace="integrations.slack",
    secrets=[slack_secret],
)
async def call_paginated_slack_api(
    sdk_method: Annotated[
        str,
        Field(
            ...,
            description="Slack Python SDK method name that supports cursor / pagination (e.g. `conversations_history`)",
        ),
    ],
    params: Annotated[
        dict, Field(..., description="Slack Python SDK method parameters")
    ],
    limit: Annotated[
        int,
        Field(
            ...,
            description="Maximum number of items to retrieve. Must be less than 1000",
        ),
    ] = 200,
) -> dict[str, Any]:
    bot_token = secrets.get("SLACK_BOT_TOKEN")
    client = AsyncWebClient(token=bot_token)
    cursor = None
    items = []
    while True:
        result = await getattr(client, sdk_method)(**params, cursor=cursor, limit=limit)
        items.extend(result["items"])
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return items
