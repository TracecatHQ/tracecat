"""Domain types for persisted MCP catalog queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from tracecat.identifiers import WorkspaceID
from tracecat.integrations.enums import MCPAuthType, MCPCatalogArtifactType


@dataclass(frozen=True, slots=True)
class MCPCatalogSearchResult:
    """Persisted catalog artifact returned from a wrapper search."""

    id: UUID
    mcp_integration_id: UUID
    workspace_id: WorkspaceID
    artifact_type: MCPCatalogArtifactType
    artifact_key: str
    artifact_ref: str
    display_name: str | None
    description: str | None
    input_schema: dict[str, object] | None
    scope_name: str
    rank: float


@dataclass(frozen=True, slots=True)
class MCPCatalogSearchResults:
    """Search response for persisted wrapper-visible catalog artifacts."""

    workspace_id: WorkspaceID
    query: str
    results: tuple[MCPCatalogSearchResult, ...]


@dataclass(frozen=True, slots=True)
class MCPCatalogResolvedArtifact:
    """Authorized persisted artifact plus integration execution metadata."""

    id: UUID
    mcp_integration_id: UUID
    workspace_id: WorkspaceID
    artifact_type: MCPCatalogArtifactType
    artifact_key: str
    artifact_ref: str
    display_name: str | None
    description: str | None
    scope_name: str
    server_type: str
    server_uri: str | None
    auth_type: MCPAuthType
    oauth_integration_id: UUID | None
    encrypted_headers: bytes | None
    timeout: int | None


@dataclass(frozen=True, slots=True)
class MCPCatalogToolResult:
    """Wrapper result for an MCP tool call."""

    workspace_id: WorkspaceID
    artifact: MCPCatalogResolvedArtifact
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MCPCatalogResourceResult:
    """Wrapper result for an MCP resource read."""

    workspace_id: WorkspaceID
    artifact: MCPCatalogResolvedArtifact
    contents: tuple[dict[str, Any], ...]
    truncated: bool
    max_content_chars: int
    total_content_chars: int


@dataclass(frozen=True, slots=True)
class MCPCatalogPromptResult:
    """Wrapper result for an MCP prompt fetch."""

    workspace_id: WorkspaceID
    artifact: MCPCatalogResolvedArtifact
    result: dict[str, Any]
