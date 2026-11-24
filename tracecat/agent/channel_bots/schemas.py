"""Request and response shapes for the channel bot admin API."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracecat.agent.channel_users.enums import ChannelAccessPolicy
from tracecat.pagination import CursorPaginatedResponse

CHANNEL_BOTS_PREFIX = "/agent/channel-bots"
OAUTH_CALLBACK_ROUTE = f"{CHANNEL_BOTS_PREFIX}/{{provider}}/oauth/callback"


class ChannelBotProvider(StrEnum):
    SLACK = "slack"


class ChannelBotTransport(StrEnum):
    """How a bot's events arrive: posted to our endpoint, or read from a provider socket."""

    HTTP = "http"
    SOCKET = "socket"


class ChannelBotAppOwner(StrEnum):
    """Who owns a bot's provider app."""

    # The workspace's own app, answering as one agent.
    WORKSPACE = "workspace"
    # The deployment's app: one install per provider tenant, routed per channel.
    PLATFORM = "platform"


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

    No bot token: only OAuth sets one, together with the install it belongs to.
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
    channel_access_policy: ChannelAccessPolicy | None = Field(
        default=None,
        description="Who may act in the bot's channels, for every agent it serves.",
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


type ChannelBotDetails = Annotated[SlackBotDetails, Field(discriminator="provider")]


class AgentChannelBotRead(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_preset_id: uuid.UUID = Field(
        description="The agent that answers; a platform install's default agent."
    )
    provider: ChannelBotProvider
    app_owner: ChannelBotAppOwner
    status: ChannelBotStatus
    provider_tenant_id: str | None
    provider_app_id: str | None
    endpoint_url: str | None = Field(
        description="Inbound event URL; None when the bot's events arrive over a socket."
    )
    channel_access_policy: ChannelAccessPolicy = Field(
        description="Who may act in the bot's channels, for every agent it serves."
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
    """Install a workspace-owned Slack app over OAuth."""

    provider: Literal[ChannelBotProvider.SLACK]
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)
    signing_secret: str = Field(..., min_length=1)
    app_token: str | None = Field(
        default=None,
        pattern=r"^(xoxe\.)?xapp-.+",
        description=(
            "App-level token (`xapp-`, or rotating `xoxe.xapp-`) with connections:write."
            " Required when the deployment receives Slack events over Socket Mode; rejected"
            " otherwise."
        ),
    )
    app_refresh_token: str | None = Field(
        default=None,
        pattern=r"^xoxe-.+",
        description=(
            "The refresh token Slack shows once beside a rotating `xoxe.xapp-` app-level"
            " token. Required with one; rejected otherwise."
        ),
    )
    return_url: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _refresh_token_matches_app_token(self) -> Self:
        check_slack_app_refresh_token(self.app_token, self.app_refresh_token)
        return self


def check_slack_app_refresh_token(
    app_token: str | None, app_refresh_token: str | None
) -> None:
    """A rotating `xoxe.xapp-` app-level token comes with its refresh token, and only one does."""
    rotating = app_token is not None and app_token.startswith("xoxe.")
    if rotating and app_refresh_token is None:
        raise ValueError(
            "A rotating `xoxe.xapp-` app-level token needs its refresh token"
        )
    if not rotating and app_refresh_token is not None:
        raise ValueError(
            "Only a rotating `xoxe.xapp-` app-level token takes a refresh token"
        )


type ChannelBotInstall = Annotated[SlackBotInstall, Field(discriminator="provider")]


class ChannelBotPlatformApp(BaseModel):
    """A provider app the deployment owns, which a workspace installs in one step."""

    provider: ChannelBotProvider


class ChannelBotAppManifest(BaseModel):
    """What an admin takes to the provider to create a bot's app."""

    filename: str
    media_type: str
    encoding: Literal["text", "base64"]
    content: str
    # The provider page that creates the app from it, if the provider has one.
    create_url: str | None


class ChannelBotPlatformAppSetup(BaseModel):
    """What a platform admin pastes into the deployment's own provider app."""

    transport: ChannelBotTransport
    oauth_redirect_url: str
    # Events and interactions share it; None over Socket Mode.
    request_url: str | None
    manifest: ChannelBotAppManifest


class ChannelBotPlatformInstall(BaseModel):
    """Install the platform app, which the deployment registers, over OAuth; or reconnect."""

    model_config = ConfigDict(extra="forbid")

    provider: ChannelBotProvider
    bot_id: uuid.UUID | None = Field(
        default=None,
        description="Platform install to reconnect. If omitted, creates one.",
    )
    agent_preset_id: uuid.UUID | None = Field(
        default=None,
        description="The install's default agent. Required without bot_id; with it, replaces it.",
    )
    return_url: str = Field(..., min_length=1)


class AgentChannelRouteSet(BaseModel):
    """Answer new threads in one channel with another agent than the default."""

    model_config = ConfigDict(extra="forbid")

    provider_channel_id: str = Field(
        ..., min_length=1, max_length=255, pattern=r"^\S+$"
    )
    agent_preset_id: uuid.UUID


class ChannelBotChannel(BaseModel):
    """A channel in the bot's provider tenant, which a route can name."""

    id: str
    name: str
    is_private: bool
    is_member: bool
    # The team the channel belongs to, where the tenant spans several: a Teams team or
    # a Slack Enterprise Grid workspace.
    team_id: str | None = None
    team_name: str | None = None


class AgentChannelRouteRead(BaseModel):
    bot_id: uuid.UUID
    provider_channel_id: str
    agent_preset_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AgentChannelWorkspaceRouteRead(BaseModel):
    """A channel a workspace bot answers in, and the agent that answers there."""

    bot_id: uuid.UUID
    provider: ChannelBotProvider
    app_owner: ChannelBotAppOwner
    provider_channel_id: str
    routed: bool = Field(
        description="A platform install's route; False for a channel the bot is in without one, answered by its default agent."
    )
    agent_preset_id: uuid.UUID
    agent_name: str
    channel_name: str | None = Field(
        description="The channel's name in the provider now; None when the lookup did not find it."
    )
    is_private: bool | None
    is_member: bool | None = Field(
        description="Whether the bot is in the channel; None when the lookup could not tell."
    )
    name_unavailable: bool = Field(
        description="The bot's channels were not looked up: it is not running, or the provider call failed."
    )


class ChannelBotListingFailure(BaseModel):
    """A running bot whose channels the provider would not list, so it has no channel rows."""

    bot_id: uuid.UUID
    needs_reinstall: bool = Field(
        description="The bot's app lacks the scopes to list channels until it is reinstalled."
    )


class AgentChannelWorkspaceRoutesPage(
    CursorPaginatedResponse[AgentChannelWorkspaceRouteRead]
):
    listing_failures: list[ChannelBotListingFailure] = Field(
        default_factory=list,
        description="Running bots whose channels could not be listed; the same on every page.",
    )


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


class InboxChannelThread(BaseModel):
    """The provider channel a session's linked thread is in."""

    bot_id: uuid.UUID = Field(..., description="Channel bot the thread is linked on")
    provider: ChannelBotProvider = Field(..., description="The bot's provider")
    provider_channel_id: str = Field(..., description="The provider's channel ID")
    provider_bot_user_id: str | None = Field(
        default=None, description="The bot's own provider user ID, as mentions name it"
    )
