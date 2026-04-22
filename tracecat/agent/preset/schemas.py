"""Pydantic schemas for agent preset resources."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tracecat.agent.types import AgentConfig, OutputType
from tracecat.core.schemas import Schema
from tracecat.identifiers import WorkspaceID


class AgentPresetSkillBindingBase(Schema):
    """Shared fields for preset skill bindings."""

    skill_id: uuid.UUID
    skill_version_id: uuid.UUID


class AgentPresetSkillBindingRead(AgentPresetSkillBindingBase):
    """Resolved preset skill binding with metadata."""

    skill_name: str
    skill_version: int


class AgentPresetSkillBindingChange(BaseModel):
    """Diff entry for skill binding changes between preset versions."""

    skill_id: uuid.UUID
    skill_name: str
    old_skill_version_id: uuid.UUID | None = None
    old_skill_version: int | None = None
    new_skill_version_id: uuid.UUID | None = None
    new_skill_version: int | None = None


PresetName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
PresetSlug = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]
PresetModelField = Annotated[
    str,
    StringConstraints(max_length=120),
]
PresetModelWriteField = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]


class AgentPresetExecutionConfig(Schema):
    """Execution fields that define a preset version."""

    instructions: str | None = Field(default=None)
    model_name: PresetModelField
    model_provider: PresetModelField
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_integrations: list[str] | None = Field(default=None)
    retries: int = Field(default=3, ge=0)
    enable_thinking: bool = Field(default=True)
    enable_internet_access: bool = Field(default=False)


class AgentPresetExecutionConfigWrite(Schema):
    """Write-time execution validation for mutable preset fields."""

    instructions: str | None = Field(default=None)
    model_name: PresetModelWriteField
    model_provider: PresetModelWriteField
    catalog_id: uuid.UUID | None = Field(default=None)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_integrations: list[str] | None = Field(default=None)
    retries: int = Field(default=3, ge=0)
    enable_thinking: bool = Field(default=True)
    enable_internet_access: bool = Field(default=False)


class AgentPresetBase(AgentPresetExecutionConfigWrite):
    """Shared fields for agent preset mutations."""

    description: str | None = Field(default=None, max_length=1000)
    skills: list[AgentPresetSkillBindingBase] | None = Field(default=None)


class AgentPresetCreate(AgentPresetBase):
    """Payload for creating a new agent preset."""

    name: PresetName
    slug: PresetSlug | None = None


class AgentPresetUpdate(BaseModel):
    """Payload for updating an existing agent preset."""

    name: PresetName | None = None
    slug: PresetSlug | None = None
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    model_name: PresetModelWriteField | None = None
    model_provider: PresetModelWriteField | None = None
    catalog_id: uuid.UUID | None = None
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_integrations: list[str] | None = Field(default=None)
    retries: int | None = Field(default=None, ge=0)
    enable_thinking: bool | None = Field(default=None)
    enable_internet_access: bool | None = Field(default=None)
    skills: list[AgentPresetSkillBindingBase] | None = Field(default=None)


class AgentPresetReadMinimal(Schema):
    """Minimal API model for reading agent presets in list endpoints."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    name: str
    slug: str
    description: str | None
    current_version_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class AgentPresetRead(AgentPresetExecutionConfig):
    """API model for reading agent presets."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    name: str
    slug: str
    description: str | None = Field(default=None, max_length=1000)
    current_version_id: uuid.UUID | None = None
    skills: list[AgentPresetSkillBindingRead] = Field(default_factory=list)
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
            enable_thinking=self.enable_thinking,
            enable_internet_access=self.enable_internet_access,
        )


class AgentPresetWithConfig(AgentPresetRead):
    """Agent preset with the resolved configuration attached."""

    config: AgentConfig

    @classmethod
    def from_preset(cls, preset: AgentPresetRead) -> AgentPresetWithConfig:
        return cls(**preset.model_dump(), config=preset.to_agent_config())


class AgentPresetVersionReadMinimal(Schema):
    """Metadata returned when listing immutable preset versions."""

    id: uuid.UUID
    preset_id: uuid.UUID
    workspace_id: WorkspaceID
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentPresetVersionRead(AgentPresetExecutionConfig):
    """Full response model for an immutable preset version."""

    id: uuid.UUID
    preset_id: uuid.UUID
    workspace_id: WorkspaceID
    version: int
    skills: list[AgentPresetSkillBindingRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScalarFieldChange(BaseModel):
    """Scalar field change between two preset versions."""

    field: str
    old_value: Any = None
    new_value: Any = None


class StringListFieldChange(BaseModel):
    """List diff for preset version fields."""

    field: str
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class ToolApprovalFieldChange(BaseModel):
    """Approval diff for a single tool."""

    tool: str
    old_value: bool | None = None
    new_value: bool | None = None


class AgentPresetVersionDiff(BaseModel):
    """Structured diff between two preset versions."""

    base_version_id: uuid.UUID
    base_version: int
    compare_version_id: uuid.UUID
    compare_version: int
    instructions_changed: bool = False
    base_instructions: str | None = None
    compare_instructions: str | None = None
    scalar_changes: list[ScalarFieldChange] = Field(default_factory=list)
    list_changes: list[StringListFieldChange] = Field(default_factory=list)
    tool_approval_changes: list[ToolApprovalFieldChange] = Field(default_factory=list)
    skill_changes: list[AgentPresetSkillBindingChange] = Field(default_factory=list)
    total_changes: int = 0
