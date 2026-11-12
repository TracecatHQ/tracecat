"""Request and response shapes for the channel bot admin API."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CHANNEL_BOTS_PREFIX = "/agent/channel-bots"
OAUTH_CALLBACK_ROUTE = f"{CHANNEL_BOTS_PREFIX}/{{provider}}/oauth/callback"


class ChannelBotProvider(StrEnum):
    SLACK = "slack"
    TEAMS = "teams"


class ChannelBotTransport(StrEnum):
    """How a bot's events arrive: Slack posts them, or a Socket Mode socket reads them."""

    HTTP = "http"
    SOCKET = "socket"


def oauth_callback_path(provider: ChannelBotProvider) -> str:
    """The public path a provider sends the browser back to after OAuth consent."""
    return OAUTH_CALLBACK_ROUTE.format(provider=provider.value)


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

    No bot token: only OAuth sets one, together with the install it belongs to. A Teams
    bot's credentials are replaced by installing it again.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal[ChannelBotProvider.SLACK]
    signing_secret: str | None = Field(default=None, min_length=1)
    client_id: str | None = Field(default=None, min_length=1)
    client_secret: str | None = Field(default=None, min_length=1)


type ChannelBotCredentialsUpdate = Annotated[
    SlackBotCredentialsUpdate, Field(discriminator="provider")
]


class AgentChannelBotCreate(BaseModel):
    """Reserve a bot before the provider app exists."""

    model_config = ConfigDict(extra="forbid")

    agent_preset_id: uuid.UUID
    provider: ChannelBotProvider


class AgentChannelBotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_preset_id: uuid.UUID | None = Field(
        default=None,
        description="New target. Existing threads stay on the agent they started with.",
    )
    status: ChannelBotRunState | None = Field(default=None)
    credentials: ChannelBotCredentialsUpdate | None = Field(
        default=None, description="Must name the bot's provider."
    )


class SlackBotDetails(BaseModel):
    """Secrets are reported only as present or not."""

    provider: Literal[ChannelBotProvider.SLACK]
    transport: ChannelBotTransport
    provider_enterprise_id: str | None
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
    oauth_redirect_url: str


class TeamsBotDetails(BaseModel):
    """Secrets are reported only as present or not."""

    provider: Literal[ChannelBotProvider.TEAMS]
    has_client_secret: bool


type ChannelBotDetails = Annotated[
    SlackBotDetails | TeamsBotDetails, Field(discriminator="provider")
]


class AgentChannelBotRead(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_preset_id: uuid.UUID
    provider: ChannelBotProvider
    status: ChannelBotStatus
    provider_tenant_id: str | None
    provider_app_id: str | None
    endpoint_url: str | None = Field(
        description="Inbound event URL; None for a provider that delivers events over a socket."
    )
    created_at: datetime
    updated_at: datetime
    details: ChannelBotDetails

    @model_validator(mode="after")
    def _details_match_provider(self) -> Self:
        if self.details.provider != self.provider:
            raise ValueError("details must be for the bot's provider")
        return self


class ChannelBotInstallBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: uuid.UUID | None = Field(
        default=None,
        description="Bot to install into; it must already target agent_preset_id. If omitted, creates one.",
    )
    agent_preset_id: uuid.UUID


class SlackBotInstall(ChannelBotInstallBase):
    """Install a customer-owned Slack app over OAuth."""

    provider: Literal[ChannelBotProvider.SLACK]
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)
    signing_secret: str = Field(..., min_length=1)
    app_token: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "App-level token with connections:write. Required when the deployment"
            " receives Slack events over Socket Mode; rejected otherwise."
        ),
    )
    return_url: str = Field(..., min_length=1)


class TeamsBotInstall(ChannelBotInstallBase):
    """Connect a customer-owned Azure Bot, or replace a Teams bot's credentials."""

    provider: Literal[ChannelBotProvider.TEAMS]
    app_id: uuid.UUID = Field(..., description="The Azure Bot's Microsoft App ID.")
    tenant_id: uuid.UUID = Field(
        ..., description="The Microsoft Entra tenant the single-tenant app belongs to."
    )
    client_secret: str = Field(..., min_length=1)


type ChannelBotInstall = Annotated[
    SlackBotInstall | TeamsBotInstall, Field(discriminator="provider")
]


class ChannelBotInstallResult(BaseModel):
    bot: AgentChannelBotRead
    authorization_url: str | None = Field(
        description="Where to send the browser to finish the install; None when the provider has no browser step.",
    )


class ChannelBotOAuthCallback(BaseModel):
    """A provider's redirect back to Tracecat after the user approves or cancels."""

    # Provider-specific redirect parameters reach the provider as `model_extra`
    # strings (a list when repeated) for it to validate.
    model_config = ConfigDict(extra="allow")

    state: str
    code: str | None = Field(default=None)
    error: str | None = Field(default=None)
    error_description: str | None = Field(default=None)


class ChannelBotOAuthResult(BaseModel):
    return_url: str | None = Field(
        description="The caller's return URL from the state; None when the state is invalid."
    )
    ok: bool
    message: str | None = Field(default=None)
