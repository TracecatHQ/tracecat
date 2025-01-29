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
### Functions to create commonly used pre-formatted blocks
### https://app.slack.com/block-kit-builder


@registry.register(
    default_title="Format metadata",
    description="Format metadata into a section block.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/blocks#section",
    namespace="tools.slack_blocks",
    secrets=[slack_secret],
)
def format_metadata(
    metadata: Annotated[
        dict[str, str],
        Field(
            ...,
            description='Mapping of field names and values (e.g. `{"status": "critical", "role": "admin"}`)',
        ),
    ],
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc-metadata`."),
    ] = None,
    as_columns: Annotated[
        bool,
        Field(
            ...,
            description="Whether to organize the metadata into two columns.",
        ),
    ] = False,
) -> dict[str, Any]:
    metadata_str = "\n\n".join([f"*{k}*: {v}" for k, v in metadata.items()])
    block_id = block_id or "tc-metadata"
    if as_columns:
        fields = [
            {"type": "mrkdwn", "text": f"*{k}*: {v}"} for k, v in metadata.items()
        ]
        block = {"type": "section", "fields": fields}
    else:
        block = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": metadata_str},
            "block_id": block_id,
        }
    return block


@registry.register(
    default_title="Format links",
    description="Format a list of links into a block.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/blocks#input",
    namespace="tools.slack_blocks",
    secrets=[slack_secret],
)
def format_links(
    links: Annotated[
        list[str],
        Field(
            ...,
            description='List of links (e.g. ["https://www.google.com", "https://www.yahoo.com"])',
        ),
    ],
    labels: Annotated[
        list[str] | None,
        Field(..., description="Labels for the links."),
    ] = None,
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc-links`."),
    ] = None,
    max_length: Annotated[
        int,
        Field(..., description="Maximum length of the links."),
    ] = 75,
) -> dict[str, Any]:
    block_id = block_id or "tc-links"
    if labels:
        try:
            formatted_links = [
                f"<{link}|{label}>" for link, label in zip(links, labels, strict=False)
            ]
        except ValueError as e:
            raise ValueError(
                f"`labels` and `links` must have the same length. Got {len(labels)} labels and {len(links)} links."
            ) from e
    else:
        formatted_links = [f"<{link}|{link[:max_length]}>" for link in links]
    block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(formatted_links)},
        "block_id": block_id,
    }
    return block


@registry.register(
    default_title="Format choices",
    description="Format a list of choices into an interactive block of buttons.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/blocks#actions",
    namespace="tools.slack_blocks",
    secrets=[slack_secret],
)
def format_choices(
    labels: Annotated[
        list[str],
        Field(
            ...,
            description='Unique display names for each input (e.g. ["Yes", "No"]). Max 75 characters per label.',
        ),
    ],
    values: Annotated[
        list[str],
        Field(
            ...,
            description='Unique values for each input (e.g. ["yes", "no"]). Max 2000 characters per value.',
        ),
    ],
    button_ids: Annotated[
        list[str] | None,
        Field(
            ...,
            description='Unique identifiers for each input (e.g. ["yes", "no"]). Max 255 characters per identifier.',
        ),
    ] = None,
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc-choices`."),
    ] = None,
) -> dict[str, Any]:
    block_id = block_id or "tc-choices"
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "emoji": True, "text": input},
            "value": value,
        }
        for input, value in zip(labels, values, strict=False)
    ]
    if button_ids:
        buttons = [
            {
                **button,
                "action_id": identifier,
            }
            for button, identifier in zip(buttons, button_ids, strict=False)
        ]
    block = {"type": "actions", "elements": buttons, "block_id": block_id}
    return block
