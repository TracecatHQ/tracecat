"""API contracts for linking channel senders to Tracecat users."""

from __future__ import annotations

import uuid
from datetime import datetime
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from tracecat import config
from tracecat.agent.channel_users.enums import ChannelLinkMethod

CHANNEL_LINKS_PREFIX = "/agent/channel-links"
CHANNEL_LINK_CALLBACK_ROUTE = f"{CHANNEL_LINKS_PREFIX}/callback"
"""Where the provider returns after sign-in; fixed so the provider can allowlist it."""


def channel_link_callback_url() -> str:
    """The provider sign-in redirect URL; a provider app must allowlist it."""
    return f"{config.TRACECAT__PUBLIC_API_URL.rstrip('/')}{CHANNEL_LINK_CALLBACK_ROUTE}"


def channel_link_page_url(token: str) -> str:
    """The app page that confirms a link token."""
    base_url = config.TRACECAT__PUBLIC_APP_URL.rstrip("/")
    return f"{base_url}/channel-links/{quote(token, safe='')}"


class ChannelLinkSignInRead(BaseModel):
    """Where to send the browser to prove the provider identity."""

    sign_in_url: str


class AgentChannelUserRead(BaseModel):
    """A linked provider user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    provider_tenant_id: str
    provider_user_id: str
    user_id: uuid.UUID | None
    link_method: ChannelLinkMethod
    provider_email: str | None
    linked_at: datetime
