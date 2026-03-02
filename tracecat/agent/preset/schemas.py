"""Pydantic schemas for agent preset resources."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.types import AgentConfig, OutputType
from tracecat.core.schemas import Schema
from tracecat.identifiers import WorkspaceID


class AgentPresetBase(Schema):
    """Shared fields for agent preset mutations."""

    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    model_name: str = Field(..., min_length=1, max_length=120)
    model_provider: str = Field(..., min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_integrations: list[str] | None = Field(default=None)
    retries: int = Field(default=3, ge=0)
    enable_internet_access: bool = Field(default=False)
    assigned_role_id: uuid.UUID | None = Field(default=None)


class AgentPresetCreate(AgentPresetBase):
    """Payload for creating a new agent preset."""

    name: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=160)


class AgentPresetUpdate(BaseModel):
    """Payload for updating an existing agent preset."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    model_provider: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_integrations: list[str] | None = Field(default=None)
    retries: int | None = Field(default=None, ge=0)
    enable_internet_access: bool | None = Field(default=None)
    assigned_role_id: uuid.UUID | None = Field(default=None)


class AgentPresetReadMinimal(Schema):
    """Minimal API model for reading agent presets in list endpoints."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    name: str
    slug: str
    description: str | None
    is_system: bool = False
    created_at: datetime
    updated_at: datetime


class AgentPresetRead(AgentPresetBase):
    """API model for reading agent presets."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    name: str
    slug: str
    is_system: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    def to_agent_config(self) -> AgentConfig:
        """Convert the preset into an executable agent configuration."""

        return AgentConfig(
            model_name=self.model_name,
            model_provider=self.model_provider,
            base_url=self.base_url,
            instructions=self.instructions,
            output_type=self.output_type,
            actions=self.actions,
            namespaces=self.namespaces,
            tool_approvals=self.tool_approvals,
            retries=self.retries,
            enable_internet_access=self.enable_internet_access,
        )


class AgentPresetWithConfig(AgentPresetRead):
    """Agent preset with the resolved configuration attached."""

    config: AgentConfig

    @classmethod
    def from_preset(cls, preset: AgentPresetRead) -> AgentPresetWithConfig:
        return cls(**preset.model_dump(), config=preset.to_agent_config())
