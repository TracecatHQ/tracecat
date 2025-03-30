"""Generic interface for Slack SDK."""

from itertools import zip_longest
from typing import Annotated, Any

from pydantic import Field
from slack_sdk.web.async_client import AsyncWebClient
from slugify import slugify

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
    result = await getattr(client, sdk_method)(**params)
    return result


@registry.register(
    default_title="Call paginated method",
    description="Instantiate a Slack client and call a paginated Slack SDK method.",
    display_group="Slack SDK",
    doc_url="https://api.slack.com/methods",
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
    default_title="Format fields",
    description="Format fields into a section block.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/blocks#section",
    namespace="tools.slack_blocks",
)
def format_fields(
    fields: Annotated[
        list[dict[str, Any]],
        Field(
            ...,
            description='Mapping of field names and values (e.g. `[{"status": "critical"}, {"role": "admin"}]`)',
        ),
    ],
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc-fields`."),
    ] = None,
) -> dict[str, Any]:
    fields_pairs = [d.popitem() for d in fields]
    fields_str = "\n".join([f">*{k}*: {v}" for k, v in fields_pairs])
    block_id = block_id or "tc_fields"
    block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": fields_str},
        "block_id": block_id,
    }
    return block


@registry.register(
    default_title="Format fields context",
    description="Format fields into a context block with optional images per field.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/blocks#context",
    namespace="tools.slack_blocks",
)
def format_fields_context(
    fields: Annotated[
        list[dict[str, Any]],
        Field(
            ...,
            description='Mapping of field names and values (e.g. `[{"status": "critical"}, {"role": "admin"}]`)',
        ),
    ],
    images: Annotated[
        list[str] | None,
        Field(..., description="List of image URLs to display alongside the fields."),
    ] = None,
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc-links`."),
    ] = None,
) -> dict[str, Any]:
    block_id = block_id or "tc_fields_context"
    fields_pairs = [d.popitem() for d in fields]
    if images:
        elements = []
        for image_url, fields_item in zip_longest(images, fields_pairs):
            k, v = fields_item
            text = f"{k}: *{v}*"
            if image_url:
                elements.append(
                    {
                        "type": "image",
                        "image_url": image_url,
                        "alt_text": text,
                    }
                )
            elements.append(
                {
                    "type": "mrkdwn",
                    "text": text,
                }
            )
    else:
        elements = [
            {
                "type": "mrkdwn",
                "text": f"{k}: *{v}*",
            }
            for k, v in fields_pairs
        ]

    block = {
        "type": "context",
        "elements": elements,
        "block_id": block_id,
    }
    return block


@registry.register(
    default_title="Format links",
    description="Format a list of links into a block.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/blocks#input",
    namespace="tools.slack_blocks",
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
        Field(
            ..., description="Labels for the links. If None, defaults to the link text."
        ),
    ] = None,
    max_length: Annotated[
        int,
        Field(..., description="Maximum length of the links."),
    ] = 75,
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc-links`."),
    ] = None,
) -> dict[str, Any]:
    block_id = block_id or "tc_links"
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
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "\n".join(formatted_links)}],
        "block_id": block_id,
    }
    return block


@registry.register(
    default_title="Format choices",
    description="Format a list of choices into an interactive block of buttons.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/blocks#actions",
    namespace="tools.slack_blocks",
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
        list[str] | None,
        Field(
            ...,
            description='Unique values for each input (e.g. ["yes", "no"]). If None, defaults to a slugified version of the label.',
        ),
    ] = None,
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
    block_id = block_id or "tc_choices"
    if not values:
        values = [slugify(label) for label in labels]
    if not button_ids:
        button_ids = [slugify(label) for label in labels]
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "emoji": True, "text": input},
            "value": value,
        }
        for input, value in zip(labels, values, strict=False)
    ]
    buttons = [
        {
            **button,
            "action_id": button_id,
        }
        for button, button_id in zip(buttons, button_ids, strict=False)
    ]
    block = {"type": "actions", "elements": buttons, "block_id": block_id}
    return block


@registry.register(
    default_title="Format text input",
    description="Format a text input block.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/block-elements#input",
    namespace="tools.slack_blocks",
)
def format_text_input(
    prompt: Annotated[
        str,
        Field(..., description="Prompt to ask the user."),
    ],
    multiline: Annotated[
        bool,
        Field(..., description="Whether the input should be multiline."),
    ] = False,
    dispatch_action: Annotated[
        bool,
        Field(..., description="Whether pressing Enter submits the input."),
    ] = False,
    min_length: Annotated[
        int | None,
        Field(..., description="Min length of the text input. Defaults to 1."),
    ] = None,
    max_length: Annotated[
        int | None,
        Field(..., description="Max length of the text input. Defaults to 255."),
    ] = None,
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc-text-input`."),
    ] = None,
) -> dict[str, Any]:
    block_id = block_id or "tc_text_input"
    min_length = min_length or 1
    max_length = max_length or 255
    block = {
        "dispatch_action": dispatch_action,
        "type": "input",
        "label": {"type": "plain_text", "emoji": True, "text": prompt},
        "element": {
            "type": "plain_text_input",
            "action_id": block_id,
            "multiline": multiline,
            "min_length": min_length,
            "max_length": max_length,
            "dispatch_action_config": {"trigger_actions_on": ["on_enter_pressed"]},
        },
        "block_id": block_id,
    }
    return block


@registry.register(
    default_title="Format overflow menu",
    description="Format a list of choices into an overflow menu element.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/block-elements#overflow",
    namespace="tools.slack_elements",
)
def format_overflow_menu(
    labels: Annotated[
        list[str],
        Field(..., description="List of labels for the overflow menu."),
    ],
    values: Annotated[
        list[str] | None,
        Field(
            ...,
            description="List of values for the overflow menu. If None, defaults to a slugified version of the label.",
        ),
    ] = None,
    urls: Annotated[
        list[str] | None,
        Field(
            ...,
            description="List of URLs for the overflow menu. If None, no URLs will be linked.",
        ),
    ] = None,
    action_id: Annotated[
        str | None,
        Field(..., description="Action ID. If None, defaults to `tc-overflow-menu`."),
    ] = None,
) -> dict[str, Any]:
    action_id = action_id or "tc_overflow_menu"

    if values and len(values) != len(labels):
        raise ValueError(
            f"`labels` and `values` must have the same length. Got {len(labels)} values and {len(values)} values."
        )

    if not values:
        values = [slugify(label) for label in labels]

    if urls:
        options = [
            {
                "text": {"type": "plain_text", "emoji": True, "text": label},
                "value": value,
                "url": url,
            }
            for label, value, url in zip_longest(labels, values, urls)
        ]
    else:
        options = [
            {
                "text": {"type": "plain_text", "emoji": True, "text": label},
                "value": value,
            }
            for label, value in zip(labels, values, strict=False)
        ]

    block = {
        "type": "overflow",
        "options": options,
        "action_id": action_id,
    }
    return block
