"""The Tracecat Slack app's registration, as platform admins read and change it."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.channel_bots.schemas import ChannelBotPlatformAppSetup


class SlackPlatformAppRead(BaseModel):
    """The registration without its secrets, and what to paste into Slack."""

    app_id: str | None
    client_id: str | None
    client_secret_set: bool
    signing_secret_set: bool
    app_token_set: bool
    # A stored registration that no longer decrypts; saving replaces it.
    unreadable: bool
    setup: ChannelBotPlatformAppSetup


class SlackPlatformAppUpdate(BaseModel):
    """Register or change the app. An omitted secret keeps its stored value while the
    app ID stays the same."""

    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1)
    client_secret: str | None = Field(default=None, min_length=1)
    signing_secret: str | None = Field(default=None, min_length=1)
    app_token: str | None = Field(
        default=None,
        pattern=r"^xapp-.+",
        description=(
            "App-level token (`xapp-`) with connections:write. Required when the"
            " deployment receives Slack events over Socket Mode."
        ),
    )
