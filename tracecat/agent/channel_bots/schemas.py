"""Request and response shapes for the channel bot admin API."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

CHANNEL_BOTS_PREFIX = "/agent/channel-bots"
SLACK_OAUTH_CALLBACK_PATH = f"{CHANNEL_BOTS_PREFIX}/slack/oauth/callback"


class ChannelBotProvider(StrEnum):
    SLACK = "slack"


class ChannelBotStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISCONNECTED = "disconnected"
    NEEDS_RECONNECT = "needs_reconnect"


class ChannelBotRunState(StrEnum):
    """The statuses an admin sets directly; the others follow install and uninstall."""

    ACTIVE = "active"
    PAUSED = "paused"


class SlackBotCredentialsUpdate(BaseModel):
    """Slack app credentials to replace; unset fields keep their stored value.

    No bot token: only OAuth sets one, together with the install it belongs to.
    """

    signing_secret: str | None = Field(default=None, min_length=1)
    client_id: str | None = Field(default=None, min_length=1)
    client_secret: str | None = Field(default=None, min_length=1)


class AgentChannelBotCreate(BaseModel):
    """Reserve a bot and its endpoint URL before the provider app exists."""

    agent_preset_id: uuid.UUID
    provider: ChannelBotProvider = ChannelBotProvider.SLACK


class AgentChannelBotUpdate(BaseModel):
    """Retarget, pause or resume, or replace credentials."""

    agent_preset_id: uuid.UUID | None = Field(
        default=None,
        description="New target. Existing threads stay on the agent they started with.",
    )
    status: ChannelBotRunState | None = None
    credentials: SlackBotCredentialsUpdate | None = None


class AgentChannelBotRead(BaseModel):
    """A bot with its install metadata. Secrets are reported only as present or not."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_preset_id: uuid.UUID
    provider: ChannelBotProvider
    status: ChannelBotStatus
    provider_tenant_id: str | None
    provider_enterprise_id: str | None
    provider_app_id: str | None
    provider_bot_user_id: str | None
    granted_scopes: list[str]
    missing_scopes: list[str] = Field(
        description="Required scopes the install did not grant; reinstall to add them."
    )
    update_needed: bool
    manifest_version: int
    client_id: str | None
    has_bot_token: bool
    has_signing_secret: bool
    has_client_secret: bool
    endpoint_url: str
    oauth_redirect_url: str
    created_at: datetime
    updated_at: datetime


class SlackBotOAuthStartRequest(BaseModel):
    """Start installing a customer-owned Slack app over OAuth."""

    bot_id: uuid.UUID | None = Field(
        default=None, description="Bot to install into. If omitted, creates one."
    )
    agent_preset_id: uuid.UUID
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)
    signing_secret: str = Field(..., min_length=1)
    return_url: str = Field(..., min_length=1)


class SlackBotOAuthStartResponse(BaseModel):
    authorization_url: str
    bot: AgentChannelBotRead


class SlackBotOAuthCallback(BaseModel):
    """Slack's redirect back to Tracecat after the user approves or cancels."""

    state: str
    code: str | None = None
    error: str | None = None
    error_description: str | None = None


class SlackBotOAuthOutcome(BaseModel):
    """Where to send the browser, and whether the install succeeded."""

    return_url: str | None = Field(
        description="The caller's return URL from the state; None when the state is invalid."
    )
    ok: bool
    message: str | None = None
