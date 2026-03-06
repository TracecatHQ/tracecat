"""Types for local stdio MCP artifact execution."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tracecat.auth.types import Role
from tracecat.identifiers import WorkspaceID
from tracecat.integrations.enums import MCPCatalogArtifactType


class LocalMCPArtifactOperation(StrEnum):
    """Operation executed against a local stdio MCP server."""

    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"


class RunLocalMCPArtifactWorkflowInput(BaseModel):
    """Input for local stdio MCP artifact workflow execution."""

    role: Role
    workspace_id: WorkspaceID
    artifact_ref_or_id: str
    operation: LocalMCPArtifactOperation
    arguments: dict[str, Any] | None = Field(default=None)

    @property
    def artifact_type(self) -> MCPCatalogArtifactType:
        match self.operation:
            case LocalMCPArtifactOperation.TOOL:
                return MCPCatalogArtifactType.TOOL
            case LocalMCPArtifactOperation.RESOURCE:
                return MCPCatalogArtifactType.RESOURCE
            case LocalMCPArtifactOperation.PROMPT:
                return MCPCatalogArtifactType.PROMPT


class RunLocalMCPArtifactWorkflowResult(BaseModel):
    """Result for one local stdio MCP artifact operation."""

    result: dict[str, Any] | None = Field(default=None)
    contents: tuple[dict[str, Any], ...] = Field(default=())
    truncated: bool = False
    max_content_chars: int | None = Field(default=None)
    total_content_chars: int | None = Field(default=None)
