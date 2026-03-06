"""Domain types for persisted MCP catalog queries."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from tracecat.identifiers import WorkspaceID
from tracecat.integrations.enums import MCPCatalogArtifactType


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
