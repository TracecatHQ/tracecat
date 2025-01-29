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
    default_title="Call Slack SDK",
    description="Instantiate a Slack client and call a Slack SDK method.",
    display_group="Slack",
    doc_url="https://api.slack.com/methods",
    namespace="tools.slack",
    secrets=[slack_secret],
)
async def call_python_sdk(
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
    default_title="Call paginated Slack SDK",
    description="Instantiate a Slack client and call a paginated Slack SDK method.",
    display_group="Slack",
    doc_url="https://api.slack.com/methods",
    namespace="tools.slack",
    secrets=[slack_secret],
)
async def call_python_sdk_paginated(
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


### Block utilities
### https://app.slack.com/block-kit-builder


@registry.register(
    default_title="Format metadata",
    description="Format metadata into a section block.",
    display_group="Slack",
    doc_url="https://api.slack.com/methods",
    namespace="tools.slack_blocks",
    secrets=[slack_secret],
)
async def format_metadata(
    metadata: Annotated[
        dict[str, str],
        Field(
            ...,
            description='Mapping of field names and values (e.g. `{"status": "critical", "role": "admin"}`)',
        ),
    ],
    as_columns: Annotated[
        bool,
        Field(
            ...,
            description="Whether to organize the metadata into two columns.",
        ),
    ] = False,
) -> dict[str, Any]:
    metadata_str = "\n\n".join([f"**{k}**: {v}" for k, v in metadata.items()])
    if as_columns:
        fields = [
            {"type": "mrkdwn", "text": f"**{k}**: {v}"} for k, v in metadata.items()
        ]
        block = {"type": "section", "fields": fields}
    else:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": metadata_str}}
    return block
