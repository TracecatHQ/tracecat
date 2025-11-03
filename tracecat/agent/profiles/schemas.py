"""Pydantic schemas for agent profile resources."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.types import AgentConfig, OutputType
from tracecat.identifiers import OwnerID


class AgentProfileBase(BaseModel):
    """Shared fields for agent profile mutations."""

    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    model_name: str = Field(..., min_length=1, max_length=120)
    model_provider: str = Field(..., min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    fixed_arguments: dict[str, dict[str, Any]] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_server_url: str | None = Field(default=None, max_length=500)
    mcp_server_headers: dict[str, str] | None = Field(default=None)
    model_settings: dict[str, Any] | None = Field(default=None)
    retries: int = Field(default=3, ge=0)


class AgentProfileCreate(AgentProfileBase):
    """Payload for creating a new agent profile."""

    name: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=160)


class AgentProfileUpdate(AgentProfileBase):
    """Payload for updating an existing agent profile."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=160)


class AgentProfileRead(AgentProfileBase):
    """API model for reading agent profiles."""

    id: uuid.UUID
    owner_id: OwnerID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    def to_agent_config(self) -> AgentConfig:
        """Convert the profile into an executable agent configuration."""

        return AgentConfig(
            model_name=self.model_name,
            model_provider=self.model_provider,
            base_url=self.base_url,
            instructions=self.instructions,
            output_type=self.output_type,
            actions=self.actions,
            namespaces=self.namespaces,
            fixed_arguments=self.fixed_arguments,
            tool_approvals=self.tool_approvals,
            mcp_server_url=self.mcp_server_url,
            mcp_server_headers=self.mcp_server_headers,
            model_settings=self.model_settings,
            retries=self.retries,
        )


class AgentProfileWithConfig(AgentProfileRead):
    """Agent profile with the resolved configuration attached."""

    config: AgentConfig

    @classmethod
    def from_profile(cls, profile: AgentProfileRead) -> AgentProfileWithConfig:
        return cls(**profile.model_dump(), config=profile.to_agent_config())
