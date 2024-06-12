"""Unified API for ChatOps.

Note: only Slack is supported at the moment.

Supported Capabilities:
- `post_message`: no common input schema implemented yet.
"""

import os
from typing import Any

from tracecat.actions.integrations import get_capability
from tracecat.actions.schemas.messages import MessageBase


async def post_chat_messages(
    channel: str, messages: list[MessageBase], vendor: str
) -> list[dict[str, Any]]:
    secret = os.getenv(vendor)
    post_vendor_messages = get_capability(
        category="chat", capability="post_message", vendor=vendor
    )
    responses = await post_vendor_messages(channel=channel, messages=messages, **secret)
    return responses
