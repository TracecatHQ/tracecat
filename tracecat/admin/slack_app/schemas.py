"""The Tracecat Slack app's registration, as platform admins read and change it."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracecat.agent.channels.bots.schemas import (
    ChannelBotPlatformAppSetup,
    check_slack_app_refresh_token,
)


class SlackPlatformAppRead(BaseModel):
    """The registration without its secrets, and what to paste into Slack."""

    app_id: str | None
    client_id: str | None
    client_secret_set: bool
    signing_secret_set: bool
    app_token_set: bool
    icon_uploaded: bool
    distribution_enabled: bool
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
        pattern=r"^(xoxe\.)?xapp-.+",
        description=(
            "App-level token (`xapp-`, or rotating `xoxe.xapp-`) with connections:write."
            " Required when the deployment receives Slack events over Socket Mode."
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

    @model_validator(mode="after")
    def _refresh_token_matches_app_token(self) -> Self:
        check_slack_app_refresh_token(self.app_token, self.app_refresh_token)
        return self


class SlackPlatformAppSetupStepsUpdate(BaseModel):
    """Mark the Slack-only setup steps done or not done; an omitted step keeps its value."""

    model_config = ConfigDict(extra="forbid")

    icon_uploaded: bool | None = None
    distribution_enabled: bool | None = None

    @model_validator(mode="after")
    def _names_a_step(self) -> Self:
        if self.icon_uploaded is None and self.distribution_enabled is None:
            raise ValueError("Mark at least one setup step")
        return self
