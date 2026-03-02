"""Schemas and domain types for external channel integrations."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import NotRequired, TypedDict

from pydantic import BaseModel, Field


class ChannelType(StrEnum):
    """Supported external channel types."""

    SLACK = "slack"


class SlackChannelContext(TypedDict):
    """Slack thread metadata used to bind events to an agent session."""

    channel_type: NotRequired[str]
    team_id: str
    channel_id: str
    thread_ts: str
    user_id: str
    event_ts: str
    bot_user_id: str


class SlackChannelTokenConfig(BaseModel):
    """Slack channel token configuration."""

    slack_bot_token: str = Field(
        ...,
        min_length=1,
        description="Slack bot token used for API calls",
    )
    slack_signing_secret: str = Field(
        ...,
        min_length=1,
        description="Slack signing secret used for request verification",
    )


class AgentChannelTokenCreate(BaseModel):
    """Request schema for creating an external channel token."""

    agent_preset_id: uuid.UUID = Field(
        ...,
        description="Preset to link this channel token to",
    )
    channel_type: ChannelType = Field(
        ...,
        description="External channel type",
    )
    config: SlackChannelTokenConfig = Field(
        ...,
        description="Channel-specific configuration payload",
    )
    is_active: bool = Field(
        default=True,
        description="Whether this token is active",
    )


class AgentChannelTokenUpdate(BaseModel):
    """Request schema for updating an external channel token."""

    config: SlackChannelTokenConfig | None = Field(
        default=None,
        description="Updated channel configuration payload",
    )
    is_active: bool | None = Field(
        default=None,
        description="Activation state",
    )


class AgentChannelTokenRead(BaseModel):
    """Response schema for an external channel token."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_preset_id: uuid.UUID
    channel_type: ChannelType
    config: SlackChannelTokenConfig
    is_active: bool
    public_token: str
    endpoint_url: str
    created_at: datetime
    updated_at: datetime


class ValidatedChannelToken(BaseModel):
    """Validated public channel token payload returned by request dependency."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_preset_id: uuid.UUID
    channel_type: ChannelType
    config: SlackChannelTokenConfig
    public_token: str
