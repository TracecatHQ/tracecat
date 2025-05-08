"""Generic interface for Slack SDK."""

from typing import Annotated, Any, cast

from pydantic import Field
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_sdk.webhook.async_client import AsyncWebhookClient
from typing import Literal

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
        if not key:
            key = [k for k in data.keys() if isinstance(data[k], list)][0]
        members.extend(data[key])
    return members


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
        list[dict[str, str]],
        Field(
            ...,
            description=(
                "List of JSONs with `field` and `value` keys."
                " E.g. `[{'field': 'status', 'value': 'critical'}, {'field': 'role', 'value': 'admin'}]`."
            ),
        ),
    ],
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc_fields`."),
    ] = None,
) -> dict[str, Any]:
    fields_str = "\n".join([f"> *{x['field']}*: {x['value']}" for x in fields])
    block_id = block_id or "tc_fields"
    block = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": fields_str},
        "block_id": block_id,
    }
    return block


@registry.register(
    default_title="Format fields as context",
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
            description=(
                "List of JSONs with `field`, `value`, and `image_url` (optional) keys."
                " E.g. `[{'field': 'status', 'value': 'critical', 'image_url': 'https://example.com/image.png'}, {'field': 'role', 'value': 'admin']`."
            ),
        ),
    ],
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc_fields_context`."),
    ] = None,
) -> dict[str, Any]:
    block_id = block_id or "tc_fields_context"
    elements = []
    for field in fields:
        element = {
            "type": "mrkdwn",
            "text": f"*{field['field']}*: {field['value']}",
        }
        if "image_url" in field:
            element = [
                {
                    "type": "image",
                    "image_url": field["image_url"],
                    "alt_text": f"{field['field']} {field['value']}",
                },
                element,
            ]
            elements.extend(element)
        else:
            elements.append(element)
    block = {"type": "context", "elements": elements, "block_id": block_id}
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
        list[dict[str, str]],
        Field(
            ...,
            description=(
                "List of JSONs with `url` and `text` (optional) keys."
                " E.g. `[{'url': 'https://www.google.com', 'text': 'Google'}, {'url': 'https://www.yahoo.com'}]`."
            ),
        ),
    ],
    max_length: Annotated[
        int,
        Field(..., description="Maximum length of the links."),
    ] = 75,
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc_links`."),
    ] = None,
) -> dict[str, Any]:
    block_id = block_id or "tc_links"
    formatted_links = [
        f"<{link['url']}|{link.get('text', link['url'][:max_length])}>"
        for link in links
    ]
    block = {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "\n".join(formatted_links)}],
        "block_id": block_id,
    }
    return block


@registry.register(
    default_title="Format buttons",
    description="Format a list of buttons into a block.",
    display_group="Slack",
    doc_url="https://api.slack.com/reference/block-kit/block-elements#button",
    namespace="tools.slack_blocks",
)
def format_buttons(
    buttons: Annotated[
        list[dict[str, str]],
        Field(
            ...,
            description=(
                "List of JSONs with `text`, `action_id` (optional), and `value` with `style` (optional) or `url` keys."
                " See examples: https://api.slack.com/reference/block-kit/block-elements#button__examples"
            ),
        ),
    ],
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc_buttons`."),
    ] = None,
) -> dict[str, Any]:
    block_id = block_id or "tc_buttons"
    elements = []
    for button in buttons:
        if "url" in button:
            try:
                element = {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": button["text"],
                    },
                    "url": button["url"],
                }
            except KeyError as e:
                raise ValueError(
                    f"Expected `text` and `url` keys in button. Got button: {button}"
                ) from e
        else:
            try:
                element = {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "emoji": True,
                        "text": button["text"],
                    },
                    "value": button["value"],
                }
            except KeyError as e:
                raise ValueError(
                    f"Expected `text` and `value` keys in button. Got button: {button}"
                ) from e
            if "style" in button:
                element["style"] = button["style"]

        if "action_id" in button:
            element["action_id"] = button["action_id"]

        elements.append(element)
    block = {"type": "actions", "elements": elements, "block_id": block_id}
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
    min_length: Annotated[
        int | None,
        Field(..., description="Min length of the text input. Defaults to 1."),
    ] = None,
    max_length: Annotated[
        int | None,
        Field(..., description="Max length of the text input. Defaults to 255."),
    ] = None,
    dispatch_action: Annotated[
        bool,
        Field(..., description="Whether pressing Enter submits the input."),
    ] = False,
    action_id: Annotated[
        str | None,
        Field(..., description="Action ID. If None, defaults to `tc_text_input`."),
    ] = None,
    block_id: Annotated[
        str | None,
        Field(..., description="Block ID. If None, defaults to `tc_text_input`."),
    ] = None,
) -> dict[str, Any]:
    action_id = action_id or "tc_text_input"
    block_id = block_id or "tc_text_input"
    min_length = min_length or 1
    max_length = max_length or 255
    block = {
        "dispatch_action": dispatch_action,
        "type": "input",
        "label": {"type": "plain_text", "emoji": True, "text": prompt},
        "element": {
            "type": "plain_text_input",
            "action_id": action_id,
            "multiline": multiline,
            "min_length": min_length,
            "max_length": max_length,
            "dispatch_action_config": {"trigger_actions_on": ["on_enter_pressed"]},
        },
        "block_id": block_id,
    }
    return block


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
